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
import paho.mqtt.client as mqtt
from PIL import Image
import io
from urllib.parse import urlparse, parse_qs

# Configuration
OPTIONS_PATH = "/data/options.json"
CACHE_DIR = "/share/lyrics"

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
        "lyric_providers": ["lrclib", "musixmatch", "genius"],
        "mqtt_enabled": False,
        "mqtt_topic": "synclyrics/dominant_color",
        "mqtt_host": "core-mosquitto",
        "mqtt_port": 1883
    }

options = get_options()
HA_URL = "http://supervisor/core/api"
HA_TOKEN = os.getenv("SUPERVISOR_TOKEN")

# MQTT Client
mqtt_client = None

def get_mqtt_client(opts):
    global mqtt_client
    if not opts.get("mqtt_enabled"):
        return None
    
    if mqtt_client is None:
        try:
            client = mqtt.Client()
            if opts.get("mqtt_user") and opts.get("mqtt_password"):
                client.username_pw_set(opts["mqtt_user"], opts["mqtt_password"])
            
            client.connect(opts.get("mqtt_host", "core-mosquitto"), opts.get("mqtt_port", 1883), 60)
            client.loop_start()
            mqtt_client = client
            logger.info("MQTT Client connected")
        except Exception as e:
            logger.error(f"Failed to connect to MQTT: {e}")
            return None
    return mqtt_client

def extract_dominant_color(image_data):
    """Extract dominant color from image data and return as RGB string."""
    try:
        img = Image.open(io.BytesIO(image_data))
        img = img.convert('RGB')
        img.thumbnail((100, 100))
        # Use quantize to get the most frequent color
        paletted = img.quantize(colors=1)
        dominant_rgb = paletted.convert('RGB').getpixel((0, 0))
        return {"r": dominant_rgb[0], "g": dominant_rgb[1], "b": dominant_rgb[2]}
    except Exception as e:
        logger.error(f"Error extracting color: {e}")
        return None

async def publish_color(image_url, opts):
    """Fetch image, extract color and publish to MQTT."""
    if not image_url or not opts.get("mqtt_enabled"):
        return

    try:
        target_url = image_url
        if image_url.startswith("/api/proxy"):
            parsed = urlparse(image_url)
            target_url = parse_qs(parsed.query).get('url', [None])[0]
        
        if not target_url:
            return

        async with aiohttp.ClientSession() as session:
            if target_url.startswith("/"):
                full_url = f"{HA_URL.replace('/api', '')}{target_url}"
            else:
                full_url = target_url

            async with session.get(full_url, headers={"Authorization": f"Bearer {HA_TOKEN}"}) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    color = extract_dominant_color(data)
                    if color:
                        client = get_mqtt_client(opts)
                        if client:
                            topic = opts.get("mqtt_topic", "synclyrics/dominant_color")
                            client.publish(topic, json.dumps(color), retain=True)
                            logger.info(f"Published color {color} to {topic}")
    except Exception as e:
        logger.error(f"Error in publish_color: {e}")

logger.info("SyncLyrics Backend starting...")

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
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
    last_options = None
    
    while True:
        try:
            current_options = get_options()
            options_changed = last_options is not None and current_options != last_options
            last_options = current_options

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
                        
                        current_pos = raw_pos
                        if state == "playing" and raw_pos is not None and updated_at:
                            diff = time.time() - parse_ha_time(updated_at)
                            current_pos = raw_pos + diff

                        song_key = f"{artist}_{title}"
                        
                        if not title:
                            pass
                        elif song_key != last_song_key or options_changed:
                            if song_key != last_song_key:
                                logger.info(f"Song changed: {title} by {artist}")
                            else:
                                logger.info("Options changed, broadcasting update")
                            
                            lyrics = await fetch_lyrics(artist, title, int(attr.get("media_duration", 0)))
                            
                            image_url = attr.get("entity_picture")
                            if image_url:
                                proxy_url = f"/api/proxy?url={image_url}"
                                asyncio.create_task(publish_color(proxy_url, current_options))
                                image_url = proxy_url

                            song_info = {
                                "title": title,
                                "artist": artist,
                                "album": attr.get("media_album_name"),
                                "image": image_url,
                                "position": current_pos,
                                "duration": attr.get("media_duration"),
                                "state": state,
                                "lyrics": lyrics
                            }
                            
                            current_state["song"] = song_info
                            current_state["options"] = current_options
                            
                            last_song_key = song_key
                            last_broadcast_pos = current_pos
                            last_broadcast_state = state
                            await manager.broadcast(json.dumps({"type": "update", "data": song_info, "options": current_options}))
                        else:
                            time_passed = 1.0 
                            expected_pos = last_broadcast_pos + time_passed if last_broadcast_state == "playing" else last_broadcast_pos
                            
                            is_seeking = abs((current_pos or 0) - (expected_pos or 0)) > 2.0
                            is_state_change = state != last_broadcast_state
                            
                            if is_seeking or is_state_change:
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
            traceback.print_exc()
        
        await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(monitor_ha_state())

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.get("/api/proxy")
async def proxy_image(url: str):
    """Proxy image requests to Home Assistant."""
    if not url:
        return {"error": "No URL provided"}
    if not url.startswith("/"):
        return {"error": "Invalid URL"}

    async with aiohttp.ClientSession() as session:
        target_url = f"{HA_URL.replace('/api', '')}{url}"
        async with session.get(target_url, headers={"Authorization": f"Bearer {HA_TOKEN}"}) as resp:
            if resp.status == 200:
                content = await resp.read()
                from fastapi import Response
                return Response(content=content, media_type=resp.headers.get("Content-Type"))
            else:
                return {"error": f"Failed to fetch image: {resp.status}"}

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
