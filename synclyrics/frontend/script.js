let ws;
let currentLyrics = [];
let rawLyrics = ""; // Store raw lyrics for re-parsing
let currentPosition = 0;
let duration = 0;
let isPlaying = false;
let startTime = 0;
let lastUpdate = 0;
let offset = 0; // Manual offset in seconds
let autoOffset = 0.5; // Default compensation for network lag
let gameMode = false;

function connectWS() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const baseUrl = window.location.pathname.replace(/\/$/, '');
    const wsUrl = `${protocol}//${window.location.host}${baseUrl}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'update') {
            updateSong(msg.data, msg.options);
        } else if (msg.type === 'sync') {
            syncPosition(msg.data);
        }
    };

    ws.onclose = () => setTimeout(connectWS, 2000);
}

function updateSong(data, options) {
    document.getElementById('song-title').innerText = data.title || "Unknown";
    document.getElementById('song-artist').innerText = data.artist || "Unknown";
    document.getElementById('footer-song-title').innerText = data.title || "Unknown";
    document.getElementById('footer-song-artist').innerText = data.artist || "Unknown";


    duration = data.duration || 0;

    // Initial game mode state from config
    gameMode = options.game_mode_enabled;
    document.getElementById('game-mode-toggle').checked = gameMode;

    // Background
    const bg = document.getElementById('background-layer');
    if (options.show_background && data.image) {
        bg.style.backgroundImage = `url(${data.image})`;
        bg.classList.add('visible');
    } else {
        bg.classList.remove('visible');
    }

    // Cinema Mode
    const isCinema = options.cinema_mode;
    document.body.classList.toggle('cinema-mode', isCinema);
    if (isCinema) {
        document.documentElement.style.setProperty('--cinema-width', (options.cinema_screen_width || 80) + '%');
        document.documentElement.style.setProperty('--cinema-height', (options.cinema_screen_height || 60) + '%');
        document.documentElement.style.setProperty('--cinema-opacity', (options.cinema_screen_opacity !== undefined ? options.cinema_screen_opacity / 100 : 0.7));

        const cinemaBg = document.getElementById('cinema-background');
        if (options.background_url && cinemaBg.src !== options.background_url) {
            cinemaBg.src = options.background_url;
        }
    }

    // Header/Footer visibility (in cinema mode, these are inside the #app which is sized)
    document.getElementById('header').classList.toggle('visible', options.show_header);
    document.getElementById('footer').classList.toggle('visible', options.show_progress_bar);

    // Parse Lyrics
    if (rawLyrics !== data.lyrics) {
        rawLyrics = data.lyrics;
        parseLRC(rawLyrics);
    }

    // Reset scroll to top for the new song
    const container = document.getElementById('lyrics-container');
    if (container) container.scrollTop = 0;

    syncPosition({ position: data.position, state: data.state });
}

function toggleGameMode() {
    gameMode = document.getElementById('game-mode-toggle').checked;
    console.log("[DEBUG] Game Mode Toggled:", gameMode);

    // Reset dataset to force scroll on next tick
    document.querySelectorAll('.lyric-line').forEach(el => delete el.dataset.lastActiveIndex);

    parseLRC(rawLyrics);
}

function parseLRC(lrcText) {
    if (!lrcText) {
        currentLyrics = [{ time: 0, text: "Lyrics not found" }];
        renderLyrics();
        return;
    }

    const lines = lrcText.split('\n');
    const timeRegex = /\[(\d+):(\d+)\.(\d+)\]/;
    currentLyrics = [];

    lines.forEach(line => {
        const match = timeRegex.exec(line);
        if (match) {
            const time = parseInt(match[1]) * 60 + parseInt(match[2]) + parseInt(match[3]) / 100;
            let text = line.replace(timeRegex, '').trim();

            if (gameMode && text.length > 10) {
                text = maskWords(text);
            }

            currentLyrics.push({ time, text, isMasked: text.includes('<span class="masked">') });
        }
    });

    if (currentLyrics.length === 0 && lines.some(l => l.trim().length > 0)) {
        currentLyrics = lines
            .filter(l => l.trim().length > 0)
            .map((text, i) => ({
                time: -1,
                text: gameMode && text.length > 10 ? maskWords(text) : text
            }));
    }

    renderLyrics();
}

function maskWords(text) {
    const words = text.split(' ');
    const count = Math.max(1, Math.floor(words.length / 3));
    // Use a seed or predictable random if we want consistency, 
    // but for "instant fun" random is fine.
    for (let i = 0; i < count; i++) {
        const idx = Math.floor(Math.random() * words.length);
        if (words[idx].length > 2 && !words[idx].includes('<span')) {
            words[idx] = `<span class="masked">${words[idx]}</span>`;
        }
    }
    return words.join(' ');
}

function renderLyrics() {
    const container = document.getElementById('lyrics-container');
    container.innerHTML = currentLyrics.map((line, i) => `
        <div class="lyric-line ${line.isMasked ? 'hidden-word' : ''}" id="line-${i}">${line.text}</div>
    `).join('');
}

function syncPosition(data) {
    if (data.position !== undefined) {
        currentPosition = data.position;
    }
    isPlaying = data.state === 'playing';
    lastUpdate = Date.now();
    console.log("[DEBUG] Sync received:", { pos: currentPosition, playing: isPlaying });
}

function adjustOffset(val) {
    offset += val;
    document.getElementById('offset-val').innerText = offset.toFixed(1) + 's';
}

function updateUI() {
    const now = Date.now();
    const elapsed = isPlaying ? (now - lastUpdate) / 1000 : 0;
    const actualPos = currentPosition + elapsed + offset + autoOffset;

    // Update Progress Bar
    if (duration > 0) {
        const progress = (actualPos / duration) * 100;
        document.getElementById('progress-bar').style.width = Math.min(100, progress) + '%';
    }

    // Highlight current lyric
    let activeIndex = -1;
    for (let i = 0; i < currentLyrics.length; i++) {
        if (actualPos >= currentLyrics[i].time) {
            activeIndex = i;
        } else {
            break;
        }
    }

    if (activeIndex !== -1) {
        const lines = document.querySelectorAll('.lyric-line');
        lines.forEach((el, i) => {
            const isActive = i === activeIndex;
            el.classList.toggle('active', isActive);
            el.classList.toggle('past', i < activeIndex);

            if (isActive && el.dataset.lastActiveIndex !== activeIndex.toString()) {
                const containerHeight = container.offsetHeight;
                const elOffsetTop = el.offsetTop;
                const elHeight = el.offsetHeight;

                // Calculate scroll position to center the element
                const scrollPos = elOffsetTop - (containerHeight / 2) + (elHeight / 2);

                container.scrollTo({
                    top: scrollPos,
                    behavior: 'smooth'
                });

                el.dataset.lastActiveIndex = activeIndex.toString();
            }
        });
    }

    requestAnimationFrame(updateUI);
}

function toggleScreen() {
    const app = document.getElementById('app');
    const btn = document.getElementById('screen-toggle-btn');
    const isCollapsed = app.classList.toggle('collapsed');
    btn.innerText = isCollapsed ? '▼' : '▲';
}

function toggleFullscreen() {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(err => {
            console.error(`Error attempting to enable full-screen mode: ${err.message} (${err.name})`);
        });
    } else {
        if (document.exitFullscreen) {
            document.exitFullscreen();
        }
    }
}

function updateVisibilitySettings() {
    const hideArtist = document.getElementById('hide-artist-toggle').checked;
    const hideTitle = document.getElementById('hide-title-toggle').checked;

    document.getElementById('song-artist').classList.toggle('hidden-by-option', hideArtist);
    document.getElementById('song-title').classList.toggle('hidden-by-option', hideTitle);

    // Also apply to footer info
    document.getElementById('footer-song-artist').classList.toggle('hidden-by-option', hideArtist);
    document.getElementById('footer-song-title').classList.toggle('hidden-by-option', hideTitle);
}


connectWS();
updateUI();

window.addEventListener('resize', () => {
    // Force re-scroll to active line on resize
    const activeLine = document.querySelector('.lyric-line.active');
    if (activeLine) {
        const container = document.getElementById('lyrics-container');
        const scrollPos = activeLine.offsetTop - (container.offsetHeight / 2) + (activeLine.offsetHeight / 2);
        container.scrollTo({ top: scrollPos, behavior: 'auto' });
    }
});
