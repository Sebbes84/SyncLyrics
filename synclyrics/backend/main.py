import asyncio
import json
import os
import logging
import traceback
import time
from datetime import datetime
from typing import Optional
import syncedlyrics

import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
import uvicorn

# Configuration
OPTIONS_PATH = "/data/options.json"
CACHE_DIR = "/data/lyrics"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SyncLyrics")

app = FastAPI()

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# Global state to store the latest song for new connections
current_state = {
    "song": None,
    "options": None
}

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

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # Send initial state if available
        if current_state["song"]:
            await websocket.send_text(json.dumps({
                "type": "update",
                "data": current_state["song"],
                "options": current_state["options"]
            }))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
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
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    
    cache_path = os.path.join(CACHE_DIR, filename)

    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            return f.read()

    current_options = get_options()
    
    def search():
        try:
            mx_token = current_options.get("musixmatch_token")
            gn_token = current_options.get("genius_token")
            if mx_token: os.environ["MUSIXMATCH_TOKEN"] = mx_token
            if gn_token: os.environ["GENIUS_ACCESS_TOKEN"] = gn_token
            return syncedlyrics.search(f"{artist} - {title}")
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

def parse_ha_time(time_str):
    """Parse HA ISO time string to unix timestamp."""
    try:
        dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        return dt.timestamp()
    except Exception:
        return time.time()

async def monitor_ha_state():
    """Monitor Home Assistant player state with drift compensation."""
    last_song_key = None
    last_broadcast_pos = -1
    last_broadcast_state = None
    
    while True:
        try:
            current_options = get_options()
            entity_id = current_options.get("spotify_entity")
            if not HA_TOKEN:
                await asyncio.sleep(5)
                continue

            async with aiohttp.ClientSession() as session:
                url = f"{HA_URL}/states/{entity_id}"
                async with session.get(url, headers={"Authorization": f"Bearer {HA_TOKEN}"}) as resp:
                    if resp.status == 200:
                        state_data = await resp.json()
                        attr = state_data.get("attributes", {})
                        
                        title = attr.get("media_title")
                        artist = attr.get("media_artist")
                        state = state_data.get("state")
                        raw_pos = attr.get("media_position")
                        updated_at = attr.get("media_position_updated_at")
                        
                        # Compensate for drift
                        current_pos = raw_pos
                        if state == "playing" and raw_pos is not None and updated_at:
                            diff = time.time() - parse_ha_time(updated_at)
                            current_pos = raw_pos + diff

                        song_key = f"{artist}_{title}"
                        
                        if not title:
                            pass
                        elif song_key != last_song_key:
                            logger.info(f"Song changed: {title} by {artist}")
                            lyrics = await fetch_lyrics(artist, title, int(attr.get("media_duration", 0)))
                            
                            song_info = {
                                "title": title,
                                "artist": artist,
                                "album": attr.get("media_album_name"),
                                "image": attr.get("entity_picture"),
                                "position": current_pos,
                                "duration": attr.get("media_duration"),
                                "state": state,
                                "lyrics": lyrics
                            }
                            
                            # Update global state for new connections
                            current_state["song"] = song_info
                            current_state["options"] = current_options
                            
                            last_song_key = song_key
                            last_broadcast_pos = current_pos
                            last_broadcast_state = state
                            await manager.broadcast(json.dumps({"type": "update", "data": song_info, "options": current_options}))
                        else:
                            # Song is the same, check for seek or state change
                            time_passed = 1.0 
                            expected_pos = last_broadcast_pos + time_passed if last_broadcast_state == "playing" else last_broadcast_pos
                            
                            is_seeking = abs((current_pos or 0) - (expected_pos or 0)) > 2.0
                            is_state_change = state != last_broadcast_state
                            
                            if is_seeking or is_state_change:
                                # Update position in stored state too
                                if current_state["song"]:
                                    current_state["song"]["position"] = current_pos
                                    current_state["song"]["state"] = state
                                
                                last_broadcast_pos = current_pos
                                last_broadcast_state = state
                                await manager.broadcast(json.dumps({
                                    "type": "sync",
                                    "data": {"position": current_pos, "state": state}
                                }))
                    else:
                        logger.error(f"HA API Error {resp.status}")
        except Exception as e:
            logger.error(f"Error: {e}")
        
        await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(monitor_ha_state())

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

app.mount("/", StaticFiles(directory="/app/frontend", html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8099)
