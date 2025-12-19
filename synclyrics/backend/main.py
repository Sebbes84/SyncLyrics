import asyncio
import json
import os
import logging
import traceback
from typing import Optional
import syncedlyrics

import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import uvicorn

print("[DEBUG] main.py: Script started", flush=True)

# Configuration
OPTIONS_PATH = "/data/options.json"
CACHE_DIR = "/data/lyrics"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SyncLyrics")

app = FastAPI()

# Configuration (In a HA addon, these are in /data/options.json)
OPTIONS_PATH = "/data/options.json"
CACHE_DIR = "/data/lyrics"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

def get_options():
    if os.path.exists(OPTIONS_PATH):
        with open(OPTIONS_PATH, 'r') as f:
            return json.load(f)
    return {
        "spotify_entity": "media_player.spotify_user",
        "cache_size_mb": 100,
        "show_header": True,
        "show_progress_bar": True,
        "show_background": True,
        "game_mode_enabled": False,
        "lyric_providers": ["lrclib", "musixmatch", "genius"]
    }

options = get_options()
HA_URL = "http://supervisor/core/api"
HA_TOKEN = os.getenv("SUPERVISOR_TOKEN")

logger.info("SyncLyrics Backend starting...")
logger.info(f"Options loaded: {json.dumps(options, indent=2)}")
logger.info(f"HA_URL: {HA_URL}")
logger.info(f"HA_TOKEN present: {'Yes' if HA_TOKEN else 'No'}")

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

async def fetch_lyrics(artist: str, title: str, duration: int) -> Optional[str]:
    """Fetch lyrics using syncedlyrics library."""
    filename = f"{artist}_{title}".replace(" ", "_").lower() + ".lrc"
    cache_path = os.path.join(CACHE_DIR, filename)

    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            return f.read()

    providers = options.get("lyric_providers", ["lrclib", "musixmatch", "genius"])
    
    # syncedlyrics.search is synchronous, so we run it in a thread
    def search():
        try:
            # Set tokens as environment variables if provided
            mx_token = options.get("musixmatch_token")
            gn_token = options.get("genius_token")
            
            if mx_token:
                os.environ["MUSIXMATCH_TOKEN"] = mx_token
            if gn_token:
                os.environ["GENIUS_ACCESS_TOKEN"] = gn_token

            # We join providers into a string for syncedlyrics if it supports it, 
            # or we iterate manually. syncedlyrics search allows specifying providers.
            lrc = syncedlyrics.search(f"{artist} - {title}", providers=providers)
            return lrc
        except Exception as e:
            logger.error(f"Error in syncedlyrics search: {e}")
            return None

    loop = asyncio.get_event_loop()
    lyrics = await loop.run_in_executor(None, search)

    if lyrics:
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(lyrics)
        return lyrics
    
    return None

async def monitor_ha_state():
    """Monitor Home Assistant player state."""
    last_song = None
    
    while True:
        try:
            # Refresh options in each loop to catch changes without restart
            current_options = get_options()
            entity_id = current_options.get("spotify_entity")
            
            logger.debug(f"Monitoring entity: {entity_id}")
            
            if not HA_TOKEN:
                logger.error("SUPERVISOR_TOKEN is missing! Cannot reach HA API.")
                await asyncio.sleep(10)
                continue

            headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
            async with aiohttp.ClientSession() as session:
                url = f"{HA_URL}/states/{entity_id}"
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        state = await resp.json()
                        attr = state.get("attributes", {})
                        
                        current_song = {
                            "title": attr.get("media_title"),
                            "artist": attr.get("media_artist"),
                            "album": attr.get("media_album_name"),
                            "image": attr.get("entity_picture"),
                            "position": attr.get("media_position"),
                            "duration": attr.get("media_duration"),
                            "state": state.get("state") # playing, paused, etc
                        }

                        if not current_song["title"]:
                            # logger.debug("No song title found in attributes")
                            pass
                        elif current_song["title"] != (last_song["title"] if last_song else None):
                            logger.info(f"Song changed: {current_song['title']} by {current_song['artist']}")
                            lyrics = await fetch_lyrics(
                                current_song["artist"], 
                                current_song["title"], 
                                int(current_song["duration"]) if current_song["duration"] else 0
                            )
                            current_song["lyrics"] = lyrics
                            last_song = current_song
                            
                            # Broadcast to all connected clients
                            await manager.broadcast(json.dumps({
                                "type": "update",
                                "data": current_song,
                                "options": current_options
                            }))
                        else:
                            # Just broadcast the current position if playing
                            await manager.broadcast(json.dumps({
                                "type": "sync",
                                "data": {
                                    "position": current_song["position"],
                                    "state": current_song["state"]
                                }
                            }))
                    else:
                        error_body = await resp.text()
                        logger.error(f"Failed to fetch state from HA. Status: {resp.status}, Entity: {entity_id}, URL: {url}")
                        logger.error(f"Response body: {error_body}")
        except Exception as e:
            logger.error(f"Error monitoring HA: {e}")
        
        await asyncio.sleep(2) # Increased sleep slightly to be less aggressive

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(monitor_ha_state())

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "SyncLyrics is running"}

@app.websocket("/ws")
@app.websocket("/ws/")
async def websocket_endpoint(websocket: WebSocket):
    print(f"[DEBUG] main.py: WebSocket connection attempt from {websocket.client}", flush=True)
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        print("[DEBUG] main.py: WebSocket disconnected", flush=True)
        manager.disconnect(websocket)
    except Exception as e:
        print(f"[DEBUG] main.py: WebSocket error: {e}", flush=True)
        manager.disconnect(websocket)

@app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def catch_all(request: Request, path_name: str):
    print(f"[DEBUG] main.py: Catch-all route hit: {request.method} /{path_name}", flush=True)
    return JSONResponse(
        status_code=404,
        content={"error": "Not Found", "requested_path": path_name, "method": request.method}
    )

# Serve static frontend (last)
app.mount("/", StaticFiles(directory="/app/frontend", html=True), name="frontend")

if __name__ == "__main__":
    print("[DEBUG] main.py: Entering __main__", flush=True)
    try:
        uvicorn.run(app, host="0.0.0.0", port=8099)
    except Exception as e:
        print(f"[DEBUG] main.py: FATAL ERROR during uvicorn run: {e}", flush=True)
        traceback.print_exc()
