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
import re
from deep_translator import GoogleTranslator
from langdetect import detect

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
        "translate_lyrics": False,
        "target_language": "fr"
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

def translate_lrc(lrc_text, target_lang="fr"):
    """Translate LRC text to target language, preserving structure."""
    if not lrc_text:
        return lrc_text
        
    lines = lrc_text.split('\n')
    original_texts = []
    line_indices = [] # Stores which lines have text to translate
    
    time_regex = r"\[\d+:\d+\.\d+\]"
    
    for i, line in enumerate(lines):
        match = re.search(time_regex, line)
        if match:
            text = re.sub(time_regex, "", line).strip()
            if text and not text.startswith('['): # Avoid metadata lines
                original_texts.append(text)
                line_indices.append(i)
            
    if not original_texts:
        return lrc_text
        
    try:
        # Detect language to decide if we should skip translation
        sample = "\n".join(original_texts[:min(len(original_texts), 20)])
        try:
            from langdetect import detect_langs
            probs = detect_langs(sample)
            # If the most likely language is the target language and it's very certain (>90%), skip.
            # Otherwise, let Google handle it with 'auto' detection.
            if probs and probs[0].lang == target_lang and probs[0].prob > 0.9:
                logger.info(f"Lyrics appear to be already in {target_lang} (confidence {probs[0].prob}), skipping.")
                return lrc_text
        except Exception as e:
            logger.warning(f"Language detection skip-check failed: {e}")
            
        logger.info(f"Translating lyrics (multi-language support enabled)")
        
        # Always use 'auto' for the actual translation to handle mixed languages
        translator = GoogleTranslator(source='auto', target=target_lang)
        translated_lines = []
        current_chunk = []
        current_length = 0
        
        for text in original_texts:
            if current_length + len(text) > 4000:
                chunk_text = "\n".join(current_chunk)
                translated_chunk = translator.translate(chunk_text)
                translated_lines.extend(translated_chunk.strip().split('\n'))
                current_chunk = [text]
                current_length = len(text)
            else:
                current_chunk.append(text)
                current_length += len(text) + 1
        
        if current_chunk:
            chunk_text = "\n".join(current_chunk)
            translated_chunk = translator.translate(chunk_text)
            translated_lines.extend(translated_chunk.strip().split('\n'))
        
        # Reconstruct with "Original | Translation" 
        new_lines = list(lines)
        for i, original_idx in enumerate(line_indices):
            if i < len(translated_lines):
                translation = translated_lines[i].strip()
                if translation and translation.lower() != original_texts[i].lower():
                    new_lines[original_idx] = f"{lines[original_idx]} | {translation}"
        
        return "\n".join(new_lines)
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return lrc_text

async def fetch_lyrics(artist: str, title: str, duration: int) -> Optional[str]:
    """Fetch lyrics using syncedlyrics library."""
    filename = f"{artist}_{title}".replace(" ", "_").lower() + ".lrc"
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    
    cache_path = os.path.join(CACHE_DIR, filename)

    current_options = get_options()
    should_translate = current_options.get("translate_lyrics", False)
    target_lang = current_options.get("target_language", "fr")
    
    # 1. Check for translated cache if requested
    if should_translate:
        trans_filename = f"{artist}_{title}_{target_lang}".replace(" ", "_").lower() + ".lrc"
        trans_cache_path = os.path.join(CACHE_DIR, trans_filename)
        if os.path.exists(trans_cache_path):
            with open(trans_cache_path, 'r', encoding='utf-8') as f:
                return f.read()

    # 2. Check for original cache
    lyrics = None
    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            lyrics = f.read()
    
    # 3. If not cached, fetch from internet
    if not lyrics:
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

    # 4. Handle translation if needed (either for new or cached lyrics)
    if lyrics and should_translate:
        loop = asyncio.get_event_loop()
        translated_lyrics = await loop.run_in_executor(None, lambda: translate_lrc(lyrics, target_lang))
        
        # Cache the translated version
        trans_filename = f"{artist}_{title}_{target_lang}".replace(" ", "_").lower() + ".lrc"
        trans_cache_path = os.path.join(CACHE_DIR, trans_filename)
        with open(trans_cache_path, 'w', encoding='utf-8') as f:
            f.write(translated_lyrics)
        return translated_lyrics
            
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
            
            # Detect option change
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
                        
                        # Compensate for drift
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
                            
                            # Local proxy for images if accessed via IP
                            image_url = attr.get("entity_picture")
                            if image_url:
                                image_url = f"/api/proxy?url={image_url}"

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
    
    # Ensure the URL is from HA
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
