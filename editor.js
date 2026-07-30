// ==================== GLOBAL STATE ====================
let ffmpeg = null, ffmpegLoaded = false;
let mediaItems = []; // Imported media files
let clips = []; // Clips on timeline
let transitions = []; // Transitions between clips
let selectedClips = []; // Currently selected clips
let clipboard = null; // Copied clip data
let currentTool = 'select';
let currentTransitionType = 'dissolve';
const PROJECT_SCHEMA = 'clipforge.project';
const PROJECT_SCHEMA_VERSION = 1;
const PROJECT_DB_NAME = 'clipforge-recovery';
const PROJECT_STORE_NAME = 'projects';
const PROJECT_RECOVERY_KEY = 'current';
const BROWSER_PROXY_PROFILE = 1;
let recoveryDbPromise = null;
let recoverySaveTimer = null;
let recoverySnapshot = null;
let projectLoading = false;
let exportInProgress = false;
let exportCancelRequested = false;
let browserProxyJob = null;
let browserFfmpegJob = null;
let lastBrowserFfmpegJob = null;
let ffmpegInitPromise = null;
const trackStates = {
    video: { visible: true, locked: false },
    audio: { muted: false, solo: false },
    music: { muted: false, solo: false },
};

// Playback state
let isPlaying = false;
let currentTime = 0; // in seconds
let duration = 0;
let playbackInterval = null;

// Timeline state
let pixelsPerSecond = 50; // Zoom level
let timelineOffset = 0;
let draggingClip = null;
let draggingHandle = null;
let isDraggingPlayhead = false;

// Undo/redo history
const undoStack = [];
const redoStack = [];
const MAX_UNDO = 50;

function pushUndo() {
    undoStack.push({
        clips: clips.map(c => ({ ...c })),
        transitions: transitions.map(t => ({ ...t })),
    });
    if (undoStack.length > MAX_UNDO) undoStack.shift();
    redoStack.length = 0;
}

// Audio context for waveforms
let audioContext = null;

// Preview elements
let previewVideo, previewCanvas, previewCtx;

// ==================== INITIALIZATION ====================
document.addEventListener('DOMContentLoaded', async () => {
    previewVideo = document.getElementById('previewVideo');
    previewCanvas = document.getElementById('previewCanvas');
    previewCtx = previewCanvas?.getContext('2d');
    
    setupEventListeners();
    renderRuler();
    initProjectRecovery();
    await initFFmpeg();
});

function withTimeout(promise, timeoutMs, label) {
    let timeoutId;
    const timeout = new Promise((_, reject) => {
        timeoutId = setTimeout(() => reject(new Error(`${label} timed out after ${Math.round(timeoutMs / 1000)} seconds`)), timeoutMs);
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timeoutId));
}

class BrowserJobConflictError extends Error {}
class BrowserJobCancelledError extends Error {}

function browserJobLabel(type) {
    return {
        waveform: 'Waveform',
        proxy: 'Proxy',
        export: 'Export',
    }[type] || 'Media job';
}

function browserJobId(type) {
    const token = globalThis.crypto?.randomUUID?.().replaceAll('-', '')
        || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
    return `cf_${type}_${token}`;
}

function updateBrowserJobUi(job = browserFfmpegJob) {
    const root = document.documentElement;
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    const cancelButton = document.getElementById('cancelJobButton');
    const exportButton = document.getElementById('exportButton');
    if (job) {
        root.dataset.browserJobType = job.type;
        root.dataset.browserJobState = job.state;
        root.dataset.browserJobProgress = String(job.progress || 0);
        statusDot?.classList.remove('ready');
        if (statusText) {
            const progress = job.state === 'running' && job.progress > 0
                ? ` ${job.progress}%`
                : '';
            statusText.textContent = `${job.label}: ${job.state}${progress}`;
        }
        if (cancelButton) {
            cancelButton.hidden = false;
            cancelButton.disabled = job.cancelRequested;
        }
        if (exportButton) exportButton.disabled = true;
        return;
    }
    root.dataset.browserJobType = '';
    root.dataset.browserJobState = 'idle';
    root.dataset.browserJobProgress = '0';
    statusDot?.classList.toggle('ready', ffmpegLoaded);
    if (statusText) statusText.textContent = ffmpegLoaded ? 'Ready' : 'Engine unavailable';
    if (cancelButton) {
        cancelButton.hidden = true;
        cancelButton.disabled = false;
    }
    if (exportButton) exportButton.disabled = false;
}

function assertBrowserJobActive(job) {
    if (
        browserFfmpegJob !== job
        || job.cancelRequested
        || job.state === 'cancelling'
    ) {
        throw new BrowserJobCancelledError(`${job.label} cancelled`);
    }
}

async function restartBrowserFfmpeg(job) {
    ffmpegLoaded = false;
    ffmpeg = job.engine;
    document.getElementById('statusDot')?.classList.remove('ready');
    const overlay = document.getElementById('loadingOverlay');
    overlay?.classList.remove('hidden');
    document.getElementById('loadingText').textContent =
        `Restarting FFmpeg after ${job.label.toLowerCase()} cancellation...`;
    const coreURL = new URL(
        './vendor/ffmpeg/core/ffmpeg-core.js',
        window.location.href,
    ).href;
    const wasmURL = new URL(
        './vendor/ffmpeg/core/ffmpeg-core.wasm',
        window.location.href,
    ).href;
    try {
        await withTimeout(
            job.engine.load({ coreURL, wasmURL }),
            90000,
            'FFmpeg cancellation recovery',
        );
        ffmpegLoaded = true;
        overlay?.classList.add('hidden');
        document.getElementById('statusText').textContent = 'Ready';
        return true;
    } catch (error) {
        ffmpeg = null;
        console.error('FFmpeg cancellation recovery failed:', error);
        document.getElementById('statusText').textContent = 'Engine unavailable';
        document.getElementById('loadingText').textContent =
            `FFmpeg recovery failed: ${error.message}`;
        console.error(`FFmpeg did not recover after ${job.label.toLowerCase()} cancellation`);
        return false;
    }
}

async function cancelBrowserFfmpegJob(expectedType = null) {
    const job = browserFfmpegJob;
    if (!job || (expectedType && job.type !== expectedType)) return false;
    if (job.cancelRequested) return job.completion;
    job.cancelRequested = true;
    job.state = 'cancelling';
    updateBrowserJobUi(job);
    if (job.type === 'export') {
        exportCancelRequested = true;
        document.getElementById('loadingText').textContent = 'Cancelling export...';
        document.getElementById('cancelExportButton').disabled = true;
    }
    try {
        job.engine?.terminate();
        job.terminated = true;
    } catch (error) {
        console.warn('FFmpeg termination failed:', error);
    }
    return job.completion;
}

async function runBrowserFfmpegJob(type, options, runner) {
    if (browserFfmpegJob) {
        throw new BrowserJobConflictError(
            `${browserFfmpegJob.label} is already ${browserFfmpegJob.state}`,
        );
    }
    if (!ffmpegLoaded || !ffmpeg) {
        throw new Error('The FFmpeg engine is not ready');
    }
    let resolveCompletion;
    const job = {
        id: browserJobId(type),
        type,
        label: options?.label || browserJobLabel(type),
        mediaId: options?.mediaId ?? null,
        state: 'running',
        progress: 0,
        cancelRequested: false,
        terminated: false,
        files: new Set(),
        startedAt: new Date().toISOString(),
        engine: ffmpeg,
        completion: new Promise(resolve => {
            resolveCompletion = resolve;
        }),
    };
    browserFfmpegJob = job;
    if (type === 'proxy') browserProxyJob = job;
    updateBrowserJobUi(job);

    const api = {
        job,
        path(role, extension = '') {
            const safeRole = String(role).replace(/[^a-z0-9_-]/gi, '_').slice(0, 40);
            const safeExtension = /^\.[a-z0-9]{1,8}$/i.test(extension) ? extension.toLowerCase() : '';
            const path = `${job.id}_${safeRole}${safeExtension}`;
            job.files.add(path);
            return path;
        },
        track(path) {
            job.files.add(path);
            return path;
        },
        async writeSource(path, file) {
            assertBrowserJobActive(job);
            const payload = await window.ffmpegFetchFile(file);
            assertBrowserJobActive(job);
            job.files.add(path);
            await job.engine.writeFile(path, payload);
            assertBrowserJobActive(job);
        },
        async writeFile(path, payload) {
            assertBrowserJobActive(job);
            job.files.add(path);
            await job.engine.writeFile(path, payload);
            assertBrowserJobActive(job);
        },
        async exec(args, label) {
            assertBrowserJobActive(job);
            job.stage = label;
            job.progress = 0;
            updateBrowserJobUi(job);
            const exitCode = await job.engine.exec(args);
            assertBrowserJobActive(job);
            if (exitCode !== 0) {
                throw new Error(`${label} failed with FFmpeg exit code ${exitCode}`);
            }
        },
        async readFile(path) {
            assertBrowserJobActive(job);
            const data = await job.engine.readFile(path);
            assertBrowserJobActive(job);
            return data;
        },
        async deleteFile(path) {
            if (!job.terminated) {
                try { await job.engine.deleteFile(path); } catch (_) {}
            }
            job.files.delete(path);
        },
        assertActive() {
            assertBrowserJobActive(job);
        },
    };

    try {
        const result = await runner(api);
        assertBrowserJobActive(job);
        job.state = 'succeeded';
        return result;
    } catch (error) {
        if (job.cancelRequested || error instanceof BrowserJobCancelledError) {
            job.state = 'cancelled';
            throw new BrowserJobCancelledError(`${job.label} cancelled`);
        }
        job.state = 'failed';
        job.error = error?.message || String(error);
        throw error;
    } finally {
        if (!job.terminated && ffmpegLoaded && ffmpeg === job.engine) {
            for (const path of [...job.files]) {
                try { await job.engine.deleteFile(path); } catch (_) {}
            }
            job.files.clear();
        }
        if (job.cancelRequested) {
            job.engineReusable = await restartBrowserFfmpeg(job);
        }
        job.finishedAt = new Date().toISOString();
        lastBrowserFfmpegJob = {
            id: job.id,
            type: job.type,
            label: job.label,
            state: job.state,
            progress: job.progress,
            stage: job.stage || null,
            error: job.error || null,
            engineReusable: job.engineReusable ?? true,
            startedAt: job.startedAt,
            finishedAt: job.finishedAt,
        };
        window.clipforgeLastBrowserJob = { ...lastBrowserFfmpegJob };
        browserFfmpegJob = null;
        if (type === 'proxy') browserProxyJob = null;
        resolveCompletion({ ...lastBrowserFfmpegJob });
        updateBrowserJobUi(null);
        renderExportPreflight();
    }
}

async function initFFmpeg(options = {}) {
    if (ffmpegInitPromise) return ffmpegInitPromise;
    ffmpegInitPromise = initializeFFmpeg(options);
    try {
        return await ffmpegInitPromise;
    } finally {
        ffmpegInitPromise = null;
    }
}

async function initializeFFmpeg({ successToast = true, timeoutMs = 30000 } = {}) {
    await window.coiReady;
    document.documentElement.dataset.crossOriginIsolated = String(
        window.crossOriginIsolated
    );
    document.documentElement.dataset.browserRuntime = 'local';

    try {
        document.getElementById('loadingText').textContent = 'Loading FFmpeg modules...';

        const { FFmpeg } = await withTimeout(
            import('./vendor/ffmpeg/ffmpeg/index.js'),
            15000,
            'Local FFmpeg module load',
        );
        const { fetchFile } = await withTimeout(
            import('./vendor/ffmpeg/util/index.js'),
            15000,
            'Local FFmpeg utility load',
        );

        ffmpeg = new FFmpeg();
        window.ffmpegFetchFile = fetchFile;

        ffmpeg.on("progress", ({ progress }) => {
            const value = Number(progress);
            const pct = Number.isFinite(value) && value >= 0 && value <= 1
                ? Math.round(value * 100)
                : (browserFfmpegJob?.progress || 0);
            document.getElementById('loadingProgress').style.width = pct + '%';
            document.getElementById('loadingProgressTrack').setAttribute('aria-valuenow', String(pct));
            if (browserFfmpegJob) {
                browserFfmpegJob.progress = pct;
                updateBrowserJobUi(browserFfmpegJob);
            }
        });
        ffmpeg.on("log", ({ message }) => {
            console.log('[ffmpeg]', message);
        });

        document.getElementById('loadingText').textContent = 'Loading local FFmpeg core (~31 MB)...';

        const coreURL = new URL(
            './vendor/ffmpeg/core/ffmpeg-core.js',
            window.location.href,
        ).href;
        const wasmURL = new URL(
            './vendor/ffmpeg/core/ffmpeg-core.wasm',
            window.location.href,
        ).href;

        document.getElementById('loadingText').textContent = 'Initializing FFmpeg...';
        await withTimeout(
            ffmpeg.load({ coreURL, wasmURL }),
            timeoutMs,
            'FFmpeg initialization',
        );

        ffmpegLoaded = true;
        document.getElementById('loadingOverlay').classList.add('hidden');
        document.getElementById('statusDot').classList.add('ready');
        document.getElementById('statusText').textContent = 'Ready';

        if (successToast) toast('success', 'ClipForge ready!');
        return true;
    } catch (e) {
        ffmpegLoaded = false;
        const errMsg = (e && (e.message || e.toString())) || 'Unknown error';
        console.error('FFmpeg load error:', e);
        document.getElementById('statusText').textContent = 'Engine unavailable';
        const overlay = document.getElementById('loadingOverlay');
        overlay.setAttribute('role', 'alert');
        const content = document.createElement('div');
        content.style.textAlign = 'center';
        const icon = document.createElement('div');
        icon.style.cssText = 'font-size:48px;margin-bottom:16px';
        icon.textContent = '⚠️';
        const title = document.createElement('div');
        title.style.cssText = 'font-size:14px;margin-bottom:16px;color:var(--text-1)';
        title.textContent = 'Failed to load FFmpeg engine';
        const details = document.createElement('div');
        details.style.cssText = 'font-size:12px;margin-bottom:16px;color:var(--text-2);max-width:400px;word-break:break-word';
        details.textContent = errMsg;
        const retry = document.createElement('button');
        retry.className = 'btn primary';
        retry.textContent = 'Retry';
        retry.addEventListener('click', () => window.location.reload());
        content.append(icon, title, details, retry);
        overlay.replaceChildren(content);
        return false;
    }
}

// ==================== EVENT LISTENERS ====================
function setupEventListeners() {
    const actions = {
        'open-project': () => document.getElementById('projectFileInput').click(),
        'show-edit-menu': event => showEditMenu(event),
        'save-project': () => saveProject(),
        'show-export': () => showExportModal(),
        'import-media': () => document.getElementById('fileInput').click(),
        'recover-project': () => recoverLastProject(),
        'relink-media': () => document.getElementById('relinkFileInput').click(),
        'quick-effect': (_event, control) => applyQuickEffect(control.dataset.effect),
        'go-start': () => goToStart(),
        'step-backward': () => stepBackward(),
        'toggle-play': () => togglePlay(),
        'step-forward': () => stepForward(),
        'go-end': () => goToEnd(),
        'toggle-mute': () => toggleMute(),
        'set-tool': (_event, control) => setTool(control.dataset.tool),
        'split-clip': () => splitClip(),
        'delete-selected': () => deleteSelected(),
        'add-transition': () => addTransition(),
        'select-transition': (_event, control) => selectTransitionType(
            control.dataset.transition
        ),
        'cut-clip': () => cutClip(),
        'copy-clip': () => copyClip(),
        'paste-clip': () => pasteClip(),
        'unlink-audio': () => unlinkAudio(),
        'hide-export': () => hideExportModal(),
        'export-video': () => exportVideo(),
        'cancel-export': () => cancelExport(),
        'cancel-job': () => cancelBrowserFfmpegJob(),
    };
    document.addEventListener('click', event => {
        const control = event.target.closest?.('[data-action]');
        if (!control) return;
        const handler = actions[control.dataset.action];
        if (handler) handler(event, control);
    });
    document.addEventListener('input', event => {
        const control = event.target.closest?.('[data-input-action]');
        if (!control) return;
        if (control.dataset.inputAction === 'preview-volume') {
            setVolume(control.value);
        } else if (control.dataset.inputAction === 'timeline-zoom') {
            setZoom(control.value);
        } else if (control.dataset.inputAction === 'clip-property') {
            updateClipProperty(control.dataset.property, control.value);
        }
    });

    // File input
    document.getElementById('fileInput').addEventListener('change', handleFileInput);
    document.getElementById('projectFileInput').addEventListener('change', handleProjectFileInput);
    document.getElementById('relinkFileInput').addEventListener('change', handleRelinkFileInput);
    document.getElementById('exportFormat').addEventListener('change', renderExportPreflight);
    document.getElementById('exportResolution').addEventListener('change', renderExportPreflight);
    
    // Drop zone
    const dropZone = document.getElementById('dropZone');
    dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
    dropZone.addEventListener('drop', e => { e.preventDefault(); dropZone.classList.remove('drag-over'); handleFileDrop(e.dataTransfer.files); });
    dropZone.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            document.getElementById('fileInput').click();
        }
    });
    
    // Timeline interactions
    const tracksContainer = document.getElementById('tracksContainer');
    tracksContainer.addEventListener('mousedown', onTimelineMouseDown);
    tracksContainer.addEventListener('mousemove', onTimelineMouseMove);
    tracksContainer.addEventListener('mouseup', onTimelineMouseUp);
    tracksContainer.addEventListener('mouseleave', onTimelineMouseUp);
    tracksContainer.addEventListener('wheel', onTimelineWheel, { passive: false });
    
    // Ruler click for playhead
    document.getElementById('timelineRuler').addEventListener('mousedown', onRulerClick);
    
    // Context menu
    document.addEventListener('contextmenu', onContextMenu);
    document.addEventListener('click', () => document.getElementById('contextMenu').classList.remove('visible'));
    
    // Keyboard shortcuts
    document.addEventListener('keydown', handleKeyboard);
    
    // Preview video events
    previewVideo.addEventListener('timeupdate', onVideoTimeUpdate);
    previewVideo.addEventListener('loadedmetadata', onVideoLoaded);
    previewVideo.addEventListener('ended', onVideoEnded);
    previewVideo.addEventListener('error', () => {
        document.getElementById('previewPlaceholder').style.display = 'flex';
        document.querySelector('.preview-placeholder-text').textContent =
            'Preview could not decode this media. Try another browser or export with the desktop app.';
        toast('error', 'Preview could not decode the selected media');
    });
    
    // Panel tabs
    document.querySelectorAll('.panel-tab').forEach(tab => {
        tab.addEventListener('click', () => activatePanelTab(tab));
        tab.addEventListener('keydown', event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const tabs = [...tab.parentElement.querySelectorAll('[role="tab"]')];
            let index = tabs.indexOf(tab);
            if (event.key === 'Home') index = 0;
            else if (event.key === 'End') index = tabs.length - 1;
            else index = (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
            tabs[index].focus();
            activatePanelTab(tabs[index]);
        });
    });

    document.querySelectorAll('[data-track-control]').forEach(button => {
        button.addEventListener('click', () => toggleTrackControl(button));
    });
    
    // Media list drag
    document.getElementById('mediaList').addEventListener('dragstart', onMediaDragStart);
    window.addEventListener('beforeunload', releaseMediaUrls);
}

function activatePanelTab(tab) {
    const tabList = tab.parentElement;
    const panelOwner = tab.closest('.media-panel, .properties-panel');
    tabList.querySelectorAll('[role="tab"]').forEach(item => {
        const active = item === tab;
        item.classList.toggle('active', active);
        item.setAttribute('aria-selected', String(active));
        item.tabIndex = active ? 0 : -1;
    });
    panelOwner.querySelectorAll('.tab-panel').forEach(panel => {
        panel.hidden = panel.id !== tab.getAttribute('aria-controls');
    });
}

function handleKeyboard(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    
    const key = e.key.toLowerCase();
    
    // Tool shortcuts
    if (key === 'v') setTool('select');
    if (key === 'c' && !e.ctrlKey) setTool('razor');
    if (key === 'y') setTool('slip');
    if (key === 'h') setTool('hand');
    
    // Playback
    if (key === ' ') { e.preventDefault(); togglePlay(); }
    if (key === 'j') stepBackward();
    if (key === 'k') togglePlay();
    if (key === 'l') stepForward();
    if (key === 'home') goToStart();
    if (key === 'end') goToEnd();
    if (key === 'arrowleft') { currentTime = Math.max(0, currentTime - 1/30); updatePlayhead(); }
    if (key === 'arrowright') { currentTime = Math.min(duration, currentTime + 1/30); updatePlayhead(); }
    
    // Editing
    if (key === 's' && !e.ctrlKey) splitClip();
    if (key === 'delete' || key === 'backspace') deleteSelected();
    if (e.ctrlKey && key === 'c') copyClip();
    if (e.ctrlKey && key === 'x') cutClip();
    if (e.ctrlKey && key === 'v') pasteClip();
    if (e.ctrlKey && key === 'a') { e.preventDefault(); selectAllClips(); }
    if (e.ctrlKey && e.shiftKey && key === 'z') { e.preventDefault(); redo(); }
    else if (e.ctrlKey && key === 'z') { e.preventDefault(); undo(); }
    if (e.ctrlKey && key === 's') { e.preventDefault(); saveProject(); }
}

// ==================== MEDIA IMPORT ====================
function handleFileInput(e) {
    handleFileDrop(e.target.files);
}

async function handleFileDrop(files) {
    for (const file of files) {
        const type = getMediaType(file);
        if (!type) continue;
        
        const media = {
            id: Date.now() + Math.random(),
            file,
            name: file.name,
            type,
            duration: 0,
            thumbnail: null,
            waveform: null,
            url: URL.createObjectURL(file)
        };
        
        // Get duration and generate thumbnail/waveform
        if (type === 'video' || type === 'audio') {
            await loadMediaMetadata(media);
        } else if (type === 'image') {
            media.duration = 5; // Default 5 seconds for images
            media.thumbnail = media.url;
        }
        
        mediaItems.push(media);
    }
    
    renderMediaList();
    toast('success', `Imported ${files.length} file(s)`);
}

function getMediaType(file) {
    if (file.type.startsWith('video/')) return 'video';
    if (file.type.startsWith('audio/')) return 'audio';
    if (file.type.startsWith('image/')) return 'image';
    
    const ext = file.name.split('.').pop().toLowerCase();
    if (['mp4', 'webm', 'mkv', 'avi', 'mov', 'flv'].includes(ext)) return 'video';
    if (['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a'].includes(ext)) return 'audio';
    if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'].includes(ext)) return 'image';
    
    return null;
}

async function loadMediaMetadata(media) {
    return new Promise((resolve) => {
        const element = media.type === 'video' ? document.createElement('video') : document.createElement('audio');
        element.src = media.url;
        element.preload = 'metadata';
        
        element.onloadedmetadata = async () => {
            media.duration = element.duration;
            
            if (media.type === 'video') {
                // Generate thumbnail
                element.currentTime = Math.min(1, element.duration / 4);
                element.onseeked = () => {
                    const canvas = document.createElement('canvas');
                    canvas.width = 160;
                    canvas.height = 90;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(element, 0, 0, canvas.width, canvas.height);
                    media.thumbnail = canvas.toDataURL();
                    media.width = element.videoWidth;
                    media.height = element.videoHeight;
                    resolve();
                };
            } else {
                // Generate waveform for audio
                try {
                    media.waveform = await generateWaveform(media.file);
                } catch (e) {
                    console.warn('Waveform generation failed:', e);
                }
                resolve();
            }
        };
        
        element.onerror = () => {
            console.error('Failed to load media:', media.name);
            resolve();
        };
    });
}

async function generateWaveform(file) {
    if (!audioContext) {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    
    const arrayBuffer = await file.arrayBuffer();
    const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
    
    const rawData = audioBuffer.getChannelData(0);
    const samples = 200; // Number of samples for waveform
    const blockSize = Math.floor(rawData.length / samples);
    const waveformData = [];
    
    for (let i = 0; i < samples; i++) {
        let sum = 0;
        for (let j = 0; j < blockSize; j++) {
            sum += Math.abs(rawData[i * blockSize + j]);
        }
        waveformData.push(sum / blockSize);
    }
    
    // Normalize
    const max = Math.max(...waveformData);
    return waveformData.map(v => v / max);
}

function renderMediaList() {
    const list = document.getElementById('mediaList');
    list.replaceChildren();
    
    if (mediaItems.length === 0) {
        const empty = document.createElement('div');
        empty.style.cssText = 'text-align:center;padding:40px 20px;color:var(--text-3)';
        const icon = document.createElement('div');
        icon.style.cssText = 'font-size:20px;margin-bottom:10px;opacity:0.4';
        icon.textContent = '⊕';
        const message = document.createElement('div');
        message.style.cssText = 'font-size:11px;line-height:1.5;color:var(--text-2)';
        message.append('No media yet', document.createElement('br'));
        const hint = document.createElement('span');
        hint.style.color = 'var(--text-3)';
        hint.textContent = 'Import files to start editing';
        message.appendChild(hint);
        empty.append(icon, message);
        list.appendChild(empty);
        document.getElementById('relinkButton').hidden = true;
        scheduleProjectRecovery();
        return;
    }
    
    mediaItems.forEach(media => {
        const item = document.createElement('div');
        item.className = `media-item${media.missing ? ' missing' : ''}`;
        item.dataset.id = String(media.id);
        item.draggable = !media.missing;
        if (!media.missing) {
            item.addEventListener('dblclick', () => addToTimeline(media.id));
        }

        const thumb = document.createElement('div');
        thumb.className = 'media-thumb';
        if (media.thumbnail) {
            const image = document.createElement('img');
            image.src = media.thumbnail;
            image.alt = '';
            thumb.appendChild(image);
        } else {
            const icon = document.createElement('span');
            icon.className = 'media-thumb-icon';
            icon.textContent = media.missing ? '⚠' : (media.type === 'audio' ? '🎵' : '📷');
            thumb.appendChild(icon);
        }

        const info = document.createElement('div');
        info.className = 'media-info';
        const name = document.createElement('div');
        name.className = 'media-name';
        name.textContent = media.name;
        const meta = document.createElement('div');
        meta.className = 'media-meta';
        const type = document.createElement('span');
        type.textContent = media.missing ? 'Missing — relink required' : media.type;
        const mediaDuration = document.createElement('span');
        mediaDuration.className = 'media-duration';
        mediaDuration.textContent = formatTimecode(media.duration);
        meta.append(type, mediaDuration);
        info.append(name, meta);

        if (media.type === 'video' && !media.missing) {
            const proxyButton = document.createElement('button');
            proxyButton.className = 'media-proxy-btn';
            proxyButton.textContent = browserProxyButtonLabel(media);
            proxyButton.setAttribute('aria-label', browserProxyButtonLabel(media));
            proxyButton.addEventListener('click', event => {
                event.stopPropagation();
                generateBrowserProxy(media.id);
            });
            info.appendChild(proxyButton);
        }

        item.append(thumb, info);
        list.appendChild(item);
    });
    document.getElementById('relinkButton').hidden = !mediaItems.some(media => media.missing);
    scheduleProjectRecovery();
}

// ==================== TIMELINE CLIPS ====================
function addToTimeline(mediaId) {
    const media = mediaItems.find(m => m.id == mediaId);
    if (!media || media.missing || !media.file) {
        toast('error', 'Relink this source before adding it to the timeline');
        return;
    }
    pushUndo();
    
    // Find the end of existing clips
    const track = media.type === 'audio' ? 'music' : 'video';
    const trackClips = clips.filter(c => c.track === track);
    const startTime = trackClips.length > 0 ? Math.max(...trackClips.map(c => c.startTime + c.duration)) : 0;
    
    const clip = {
        id: Date.now() + Math.random(),
        mediaId: media.id,
        track,
        startTime,
        duration: media.duration,
        inPoint: 0,
        outPoint: media.duration,
        name: media.name,
        type: media.type,
        thumbnail: media.thumbnail,
        waveform: media.waveform,
        url: media.proxyUrl || media.url,
        proxyActive: Boolean(media.proxyUrl),
        // Effects
        opacity: 100,
        scale: 100,
        rotation: 0,
        brightness: 0,
        contrast: 0,
        saturation: 0,
        volume: 100
    };
    
    // If video, also add linked audio clip
    if (media.type === 'video') {
        const audioClip = {
            ...clip,
            id: Date.now() + Math.random() + 1,
            track: 'audio',
            type: 'audio',
            linkedTo: clip.id
        };
        clip.linkedTo = audioClip.id;
        clips.push(audioClip);
        
        // Generate waveform for video's audio
        generateVideoWaveform(media).then(waveform => {
            audioClip.waveform = waveform;
            renderTimeline();
        });
    }
    
    clips.push(clip);
    updateDuration();
    renderTimeline();
    
    // Show preview
    if (media.type === 'video') {
        loadPreview(media.proxyUrl || media.url);
    }
    
    toast('info', `Added "${media.name}" to timeline`);
}

async function generateVideoWaveform(media) {
    if (!ffmpegLoaded) return null;

    try {
        return await runBrowserFfmpegJob(
            'waveform',
            { label: `Waveform: ${media.name}`, mediaId: media.id },
            async job => {
                const extension =
                    media.file.name.match(/\.[A-Za-z0-9]{1,8}$/)?.[0] || '.bin';
                const inputName = job.path('input', extension);
                const outputName = job.path('audio', '.raw');
                await job.writeSource(inputName, media.file);
                await job.exec(
                    [
                        '-i', inputName,
                        '-ac', '1',
                        '-ar', '8000',
                        '-f', 'f32le',
                        '-acodec', 'pcm_f32le',
                        outputName,
                    ],
                    'Extracting waveform',
                );

                const audioData = await job.readFile(outputName);
                const floatArray = new Float32Array(audioData.buffer);
                const samples = 200;
                const blockSize = Math.max(1, Math.floor(floatArray.length / samples));
                const waveformData = [];

                for (let i = 0; i < samples; i++) {
                    let sum = 0;
                    for (let j = 0; j < blockSize; j++) {
                        const idx = i * blockSize + j;
                        if (idx < floatArray.length) {
                            sum += Math.abs(floatArray[idx]);
                        }
                    }
                    waveformData.push(sum / blockSize);
                }

                const max = Math.max(...waveformData);
                return waveformData.map(value => max > 0 ? value / max : 0);
            },
        );
    } catch (e) {
        if (e instanceof BrowserJobConflictError) {
            console.info(`Waveform deferred: ${e.message}`);
            return null;
        }
        if (e instanceof BrowserJobCancelledError) return null;
        console.warn('Video waveform extraction failed:', e);
        return null;
    }
}

function browserProxyKey(file) {
    return `v${BROWSER_PROXY_PROFILE}:${file.name}:${file.size}:${file.lastModified}`;
}

function browserProxyButtonLabel(media) {
    if (browserProxyJob?.mediaId == media.id) {
        return browserProxyJob.cancelRequested ? 'Cancelling proxy…' : 'Cancel proxy';
    }
    if (media.proxyUrl) {
        return media.proxyActive
            ? `Use original (${formatBytes(media.proxySize || 0)} proxy active)`
            : `Use proxy (${formatBytes(media.proxySize || 0)})`;
    }
    const estimate = Math.max(
        1024 * 1024,
        Math.min(Number(media.file?.size || 0) * 0.35, Number(media.duration || 0) * 4_128_000 / 8),
    );
    return `Create proxy (~${formatBytes(estimate)})`;
}

function applyBrowserProxy(media, payload) {
    if (media.proxyUrl?.startsWith('blob:')) URL.revokeObjectURL(media.proxyUrl);
    media.proxyKey = payload.key;
    media.proxySize = payload.size;
    media.proxyUrl = URL.createObjectURL(payload.blob);
    media.proxyActive = true;
    clips.filter(clip => clip.mediaId == media.id).forEach(clip => {
        clip.url = media.proxyUrl;
        clip.proxyActive = true;
    });
}

async function restoreBrowserProxy(media) {
    if (!media.file) return false;
    const key = browserProxyKey(media.file);
    if (media.proxy?.key && media.proxy.key !== key) return false;
    try {
        const payload = await browserProxyStore('get', key);
        if (!payload || payload.profile !== BROWSER_PROXY_PROFILE || !(payload.blob instanceof Blob)) {
            return false;
        }
        applyBrowserProxy(media, payload);
        return true;
    } catch (error) {
        console.warn('Browser proxy restore failed:', error);
        return false;
    }
}

async function pruneBrowserProxyCache() {
    try {
        const records = await browserProxyStore('all');
        records.sort((a, b) => Number(b.createdAt || 0) - Number(a.createdAt || 0));
        for (const record of records.slice(10)) {
            await browserProxyStore('delete', record.key);
        }
    } catch (error) {
        console.warn('Browser proxy pruning failed:', error);
    }
}

async function generateBrowserProxy(mediaId) {
    const media = mediaItems.find(item => item.id == mediaId);
    if (!media?.file || media.type !== 'video') return;
    if (media.proxyUrl && !browserFfmpegJob) {
        media.proxyActive = !media.proxyActive;
        clips.filter(clip => clip.mediaId == media.id).forEach(clip => {
            clip.url = media.proxyActive ? media.proxyUrl : media.url;
            clip.proxyActive = media.proxyActive;
        });
        loadPreview(media.proxyActive ? media.proxyUrl : media.url);
        renderMediaList();
        renderTimeline();
        toast('info', media.proxyActive ? 'Proxy selected for preview' : 'Original selected for preview');
        return;
    }
    if (browserFfmpegJob) {
        if (browserFfmpegJob.type === 'proxy' && browserFfmpegJob.mediaId == media.id) {
            cancelBrowserFfmpegJob('proxy');
            renderMediaList();
        } else {
            toast(
                'info',
                `${browserFfmpegJob.label} is active; finish or cancel it first`,
            );
        }
        return;
    }
    if (!ffmpegLoaded) {
        toast('error', 'The FFmpeg engine is not ready');
        return;
    }

    try {
        await runBrowserFfmpegJob(
            'proxy',
            { label: `Proxy: ${media.name}`, mediaId: media.id },
            async job => {
                renderMediaList();
                const extension =
                    media.file.name.match(/\.[A-Za-z0-9]{1,8}$/)?.[0] || '.bin';
                const inputName = job.path('input', extension);
                const outputName = job.path('output', '.mp4');
                await job.writeSource(inputName, media.file);
                await job.exec(
                    [
                        '-i', inputName,
                        '-map', '0:v:0', '-map', '0:a?',
                        '-vf', 'scale=w=min(1280\\,iw):h=-2',
                        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28',
                        '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
                        '-y', outputName,
                    ],
                    'Encoding proxy',
                );
                const data = await job.readFile(outputName);
                const blob = new Blob([data.buffer], { type: 'video/mp4' });
                const payload = {
                    key: browserProxyKey(media.file),
                    profile: BROWSER_PROXY_PROFILE,
                    blob,
                    size: blob.size,
                    createdAt: Date.now(),
                };
                job.assertActive();
                await browserProxyStore('put', payload.key, payload);
                job.assertActive();
                await pruneBrowserProxyCache();
                job.assertActive();
                applyBrowserProxy(media, payload);
                if (previewVideo?.src === media.url) loadPreview(media.proxyUrl);
            },
        );
        toast('success', 'Proxy cached and selected for preview; export still uses the original');
    } catch (error) {
        if (error instanceof BrowserJobCancelledError) {
            toast('info', 'Proxy cancelled; FFmpeg is ready for the next job');
        } else {
            console.error('Browser proxy error:', error);
            toast('error', `Proxy failed: ${error.message}`);
        }
    } finally {
        renderMediaList();
        renderTimeline();
    }
}

function renderTimeline() {
    const tracks = {
        video: document.getElementById('videoTrack'),
        audio: document.getElementById('audioTrack'),
        music: document.getElementById('musicTrack')
    };
    
    // Clear tracks
    Object.values(tracks).forEach(track => {
        track.innerHTML = '';
    });
    Object.entries(tracks).forEach(([name, track]) => {
        const state = trackStates[name];
        track.classList.toggle('hidden-track', state?.visible === false);
        track.classList.toggle('locked', Boolean(state?.locked));
        const anotherSoloed = ['audio', 'music'].some(
            key => key !== name && trackStates[key]?.solo
        );
        track.classList.toggle('muted', Boolean(state?.muted || anotherSoloed));
    });
    
    // Set timeline width based on duration
    const totalWidth = Math.max(duration * pixelsPerSecond + 500, document.getElementById('tracksContainer').offsetWidth);
    document.getElementById('tracksScroll').style.width = totalWidth + 'px';
    
    // Render clips
    clips.forEach(clip => {
        const track = tracks[clip.track];
        if (!track) return;
        
        const left = clip.startTime * pixelsPerSecond;
        const width = clip.duration * pixelsPerSecond;
        
        const clipEl = document.createElement('div');
        clipEl.className = `clip ${clip.track !== 'video' ? 'audio-clip' : ''} ${selectedClips.includes(clip) ? 'selected' : ''}`;
        clipEl.dataset.id = clip.id;
        clipEl.style.left = left + 'px';
        clipEl.style.width = width + 'px';
        
        if (clip.track === 'video') {
            clipEl.style.background = `var(--track-video)`;
        } else if (clip.track === 'audio') {
            clipEl.style.background = `var(--track-audio)`;
        } else {
            clipEl.style.background = `var(--track-music)`;
        }
        
        const header = document.createElement('div');
        header.className = 'clip-header';
        header.textContent = clip.name;
        const content = document.createElement('div');
        content.className = 'clip-content';
        if (clip.thumbnail && clip.type === 'video') {
            const image = document.createElement('img');
            image.className = 'clip-thumbnail';
            image.src = clip.thumbnail;
            image.alt = '';
            content.appendChild(image);
        }
        if (clip.waveform) {
            const canvas = document.createElement('canvas');
            canvas.className = 'waveform-canvas';
            canvas.dataset.clip = String(clip.id);
            content.appendChild(canvas);
        }
        const leftHandle = document.createElement('div');
        leftHandle.className = 'clip-handle left';
        const rightHandle = document.createElement('div');
        rightHandle.className = 'clip-handle right';
        clipEl.append(header, content, leftHandle, rightHandle);
        
        track.appendChild(clipEl);
        
        // Draw waveform
        if (clip.waveform) {
            requestAnimationFrame(() => {
                const canvas = clipEl.querySelector('.waveform-canvas');
                if (canvas) {
                    drawWaveform(canvas, clip.waveform, clip.track);
                }
            });
        }
    });
    
    // Render transitions
    transitions.forEach(trans => {
        const track = tracks.video;
        const left = trans.time * pixelsPerSecond - trans.duration * pixelsPerSecond / 2;
        const width = trans.duration * pixelsPerSecond;
        
        const transEl = document.createElement('div');
        transEl.className = `transition ${selectedClips.some(c => c.transitionId === trans.id) ? 'selected' : ''}`;
        transEl.dataset.transitionId = trans.id;
        transEl.style.left = left + 'px';
        transEl.style.width = Math.max(width, 20) + 'px';
        transEl.innerHTML = '🔀';
        transEl.title = trans.type;
        
        track.appendChild(transEl);
    });
    
    // Update playhead
    updatePlayhead();
    renderRuler();
    scheduleProjectRecovery();
}

function drawWaveform(canvas, waveformData, track) {
    const ctx = canvas.getContext('2d');
    const width = canvas.offsetWidth;
    const height = canvas.offsetHeight;
    
    canvas.width = width;
    canvas.height = height;
    
    const color = track === 'video' || track === 'audio' ? '#22c55e' : '#06b6d4';
    
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.7;
    
    const barWidth = width / waveformData.length;
    const centerY = height / 2;
    
    for (let i = 0; i < waveformData.length; i++) {
        const barHeight = waveformData[i] * height * 0.8;
        const x = i * barWidth;
        ctx.fillRect(x, centerY - barHeight / 2, barWidth - 1, barHeight);
    }
}

function renderRuler() {
    const rulerEl = document.getElementById('rulerMarkers');
    const containerWidth = document.getElementById('tracksContainer').offsetWidth;
    const totalWidth = Math.max(duration * pixelsPerSecond + 500, containerWidth);
    
    rulerEl.innerHTML = '';
    
    // Determine interval based on zoom
    let interval = 1; // seconds
    if (pixelsPerSecond < 20) interval = 10;
    else if (pixelsPerSecond < 50) interval = 5;
    else if (pixelsPerSecond > 100) interval = 0.5;
    
    const numMarks = Math.ceil(totalWidth / (interval * pixelsPerSecond)) + 1;
    
    for (let i = 0; i <= numMarks; i++) {
        const time = i * interval;
        const x = time * pixelsPerSecond;
        
        const major = i % (interval < 1 ? 2 : 5) === 0;
        
        const mark = document.createElement('div');
        mark.className = `ruler-mark ${major ? 'major' : 'minor'}`;
        mark.style.left = x + 'px';
        rulerEl.appendChild(mark);
        
        if (major) {
            const label = document.createElement('div');
            label.className = 'ruler-label';
            label.style.left = x + 'px';
            label.textContent = formatTimecodeShort(time);
            rulerEl.appendChild(label);
        }
    }
}

// ==================== TIMELINE INTERACTIONS ====================
function onTimelineMouseDown(e) {
    const rect = document.getElementById('tracksContainer').getBoundingClientRect();
    const x = e.clientX - rect.left + document.getElementById('tracksContainer').scrollLeft;
    const y = e.clientY - rect.top;
    const targetClip = e.target.closest('.clip');
    if (targetClip) {
        const target = clips.find(c => c.id == targetClip.dataset.id);
        if (target && trackStates[target.track]?.locked) {
            toast('warning', `${target.track} track is locked`);
            return;
        }
    }
    
    // Check for playhead
    const playheadX = currentTime * pixelsPerSecond;
    if (Math.abs(x - playheadX) < 10) {
        isDraggingPlayhead = true;
        return;
    }
    
    // Check for clip handle
    const handle = e.target.closest('.clip-handle');
    if (handle) {
        const clipEl = handle.closest('.clip');
        const clip = clips.find(c => c.id == clipEl.dataset.id);
        if (clip) {
            pushUndo();
            draggingHandle = {
                clip,
                side: handle.classList.contains('left') ? 'left' : 'right',
                startX: x,
                originalStart: clip.startTime,
                originalDuration: clip.duration,
                originalIn: clip.inPoint,
                originalOut: clip.outPoint
            };
        }
        return;
    }
    
    // Check for clip selection
    const clipEl = e.target.closest('.clip');
    if (clipEl) {
        const clip = clips.find(c => c.id == clipEl.dataset.id);
        if (clip) {
            // Razor tool
            if (currentTool === 'razor') {
                splitClipAt(clip, x / pixelsPerSecond);
                return;
            }
            
            // Selection
            if (!e.shiftKey && !selectedClips.includes(clip)) {
                selectedClips = [];
            }
            
            if (!selectedClips.includes(clip)) {
                selectedClips.push(clip);
                
                // Also select linked clip
                if (clip.linkedTo) {
                    const linked = clips.find(c => c.id === clip.linkedTo);
                    if (linked && !selectedClips.includes(linked)) {
                        selectedClips.push(linked);
                    }
                }
            }

            pushUndo();
            draggingClip = {
                clip,
                startX: x,
                startY: y,
                originalStart: clip.startTime
            };
            
            updateClipPropertiesPanel(clip);
            renderTimeline();
        }
        return;
    }
    
    // Check for transition selection
    const transEl = e.target.closest('.transition');
    if (transEl) {
        const trans = transitions.find(t => t.id == transEl.dataset.transitionId);
        if (trans) {
            toast('info', `Selected ${trans.type} transition`);
        }
        return;
    }
    
    // Clicked on empty space - deselect
    if (!e.shiftKey) {
        selectedClips = [];
        renderTimeline();
        clearClipPropertiesPanel();
    }
}

function onTimelineMouseMove(e) {
    const rect = document.getElementById('tracksContainer').getBoundingClientRect();
    const x = e.clientX - rect.left + document.getElementById('tracksContainer').scrollLeft;
    const y = e.clientY - rect.top;
    
    // Dragging playhead
    if (isDraggingPlayhead) {
        currentTime = Math.max(0, Math.min(duration, x / pixelsPerSecond));
        updatePlayhead();
        seekPreview(currentTime);
        return;
    }
    
    // Dragging handle (trimming)
    if (draggingHandle) {
        const delta = (x - draggingHandle.startX) / pixelsPerSecond;
        
        if (draggingHandle.side === 'left') {
            // Trim start
            const newStart = Math.max(0, draggingHandle.originalStart + delta);
            const newIn = Math.max(0, draggingHandle.originalIn + delta);
            const maxStart = draggingHandle.originalStart + draggingHandle.originalDuration - 0.1;
            
            draggingHandle.clip.startTime = Math.min(newStart, maxStart);
            draggingHandle.clip.inPoint = Math.min(newIn, draggingHandle.originalOut - 0.1);
            draggingHandle.clip.duration = draggingHandle.originalDuration - (draggingHandle.clip.startTime - draggingHandle.originalStart);
        } else {
            // Trim end
            const newDuration = Math.max(0.1, draggingHandle.originalDuration + delta);
            const media = mediaItems.find(m => m.id === draggingHandle.clip.mediaId);
            const maxDuration = media ? media.duration - draggingHandle.clip.inPoint : newDuration;
            
            draggingHandle.clip.duration = Math.min(newDuration, maxDuration);
            draggingHandle.clip.outPoint = draggingHandle.clip.inPoint + draggingHandle.clip.duration;
        }
        
        // Update linked clip
        if (draggingHandle.clip.linkedTo) {
            const linked = clips.find(c => c.id === draggingHandle.clip.linkedTo);
            if (linked) {
                linked.startTime = draggingHandle.clip.startTime;
                linked.duration = draggingHandle.clip.duration;
                linked.inPoint = draggingHandle.clip.inPoint;
                linked.outPoint = draggingHandle.clip.outPoint;
            }
        }
        
        updateDuration();
        renderTimeline();
        return;
    }
    
    // Dragging clip
    if (draggingClip) {
        const delta = (x - draggingClip.startX) / pixelsPerSecond;
        const newStart = Math.max(0, draggingClip.originalStart + delta);
        
        // Snap to other clips
        let snappedStart = newStart;
        const snapThreshold = 10 / pixelsPerSecond;
        
        clips.forEach(other => {
            if (other.id === draggingClip.clip.id || other.id === draggingClip.clip.linkedTo) return;
            
            // Snap to start
            if (Math.abs(newStart - other.startTime) < snapThreshold) {
                snappedStart = other.startTime;
            }
            // Snap to end
            if (Math.abs(newStart - (other.startTime + other.duration)) < snapThreshold) {
                snappedStart = other.startTime + other.duration;
            }
            // Snap clip end to other start
            if (Math.abs((newStart + draggingClip.clip.duration) - other.startTime) < snapThreshold) {
                snappedStart = other.startTime - draggingClip.clip.duration;
            }
        });
        
        // Snap to playhead
        if (Math.abs(newStart - currentTime) < snapThreshold) {
            snappedStart = currentTime;
        }
        if (Math.abs((newStart + draggingClip.clip.duration) - currentTime) < snapThreshold) {
            snappedStart = currentTime - draggingClip.clip.duration;
        }
        
        draggingClip.clip.startTime = snappedStart;
        
        // Move linked clip
        if (draggingClip.clip.linkedTo) {
            const linked = clips.find(c => c.id === draggingClip.clip.linkedTo);
            if (linked) {
                linked.startTime = snappedStart;
            }
        }
        
        updateDuration();
        renderTimeline();
    }
}

function onTimelineMouseUp() {
    isDraggingPlayhead = false;
    draggingClip = null;
    draggingHandle = null;
}

function onTimelineWheel(e) {
    if (e.ctrlKey) {
        // Zoom
        e.preventDefault();
        const delta = e.deltaY > 0 ? -5 : 5;
        const newZoom = Math.max(10, Math.min(200, pixelsPerSecond + delta));
        document.getElementById('zoomSlider').value = newZoom;
        setZoom(newZoom);
    }
}

function onRulerClick(e) {
    const rect = document.getElementById('rulerMarkers').getBoundingClientRect();
    const x = e.clientX - rect.left;
    currentTime = Math.max(0, Math.min(duration, x / pixelsPerSecond));
    updatePlayhead();
    seekPreview(currentTime);
}

function onContextMenu(e) {
    const clipEl = e.target.closest('.clip');
    if (clipEl || selectedClips.length > 0) {
        e.preventDefault();
        const menu = document.getElementById('contextMenu');
        menu.style.left = e.clientX + 'px';
        menu.style.top = e.clientY + 'px';
        menu.classList.add('visible');
    }
}

// ==================== MEDIA DRAG & DROP ====================
function onMediaDragStart(e) {
    const item = e.target.closest('.media-item');
    if (item) {
        e.dataTransfer.setData('mediaId', item.dataset.id);
    }
}

// ==================== TIMELINE TOOLS ====================
function setTool(tool) {
    currentTool = tool;
    document.querySelectorAll('.tool-btn').forEach(btn => {
        const active = btn.dataset.tool === tool;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', String(active));
    });
}

function toggleTrackControl(button) {
    const track = button.dataset.track;
    const control = button.dataset.trackControl;
    const state = trackStates[track];
    if (!state || !(control in state)) return;
    state[control] = !state[control];
    button.classList.toggle('active', state[control]);
    button.setAttribute('aria-pressed', String(state[control]));
    renderTimeline();
    toast('info', `${track} ${control} ${state[control] ? 'enabled' : 'disabled'}`);
}

function setZoom(value) {
    pixelsPerSecond = parseInt(value);
    document.getElementById('zoomValue').textContent = value + '%';
    renderTimeline();
}

// ==================== CLIP OPERATIONS ====================
function splitClip() {
    if (selectedClips.length === 0) {
        // Split all clips at playhead
        const clipsAtPlayhead = clips.filter(c => 
            c.startTime < currentTime && c.startTime + c.duration > currentTime
        );
        clipsAtPlayhead.forEach(clip => splitClipAt(clip, currentTime));
    } else {
        selectedClips.forEach(clip => {
            if (clip.startTime < currentTime && clip.startTime + clip.duration > currentTime) {
                splitClipAt(clip, currentTime);
            }
        });
    }
}

function splitClipAt(clip, time) {
    if (time <= clip.startTime || time >= clip.startTime + clip.duration) return;
    pushUndo();
    
    const splitPoint = time - clip.startTime;
    
    // Create new clip for second half
    const newClip = {
        ...clip,
        id: Date.now() + Math.random(),
        startTime: time,
        duration: clip.duration - splitPoint,
        inPoint: clip.inPoint + splitPoint,
        linkedTo: null
    };
    
    // Adjust original clip
    clip.duration = splitPoint;
    clip.outPoint = clip.inPoint + splitPoint;
    
    // Handle linked clips
    if (clip.linkedTo) {
        const linked = clips.find(c => c.id === clip.linkedTo);
        if (linked) {
            const newLinked = {
                ...linked,
                id: Date.now() + Math.random() + 1,
                startTime: time,
                duration: newClip.duration,
                inPoint: newClip.inPoint,
                linkedTo: newClip.id
            };
            newClip.linkedTo = newLinked.id;
            clips.push(newLinked);
            
            linked.duration = splitPoint;
            linked.outPoint = linked.inPoint + splitPoint;
        }
    }
    
    clips.push(newClip);
    renderTimeline();
    toast('info', 'Clip split');
}

function deleteSelected() {
    if (selectedClips.length === 0) return;
    pushUndo();

    selectedClips.forEach(clip => {
        // Also delete linked clip
        if (clip.linkedTo) {
            clips = clips.filter(c => c.id !== clip.linkedTo);
        }
        clips = clips.filter(c => c.id !== clip.id);
    });
    
    selectedClips = [];
    updateDuration();
    renderTimeline();
    clearClipPropertiesPanel();
    toast('info', 'Deleted selected clips');
}

function copyClip() {
    if (selectedClips.length === 0) return;
    clipboard = selectedClips.map(clip => ({ ...clip }));
    toast('info', 'Copied to clipboard');
}

function cutClip() {
    copyClip();
    deleteSelected();
}

function pasteClip() {
    if (!clipboard || clipboard.length === 0) return;
    pushUndo();

    const pasteTime = currentTime;
    const newClips = [];
    
    clipboard.forEach((clipData, index) => {
        const newClip = {
            ...clipData,
            id: Date.now() + Math.random() + index,
            startTime: pasteTime + (clipData.startTime - clipboard[0].startTime),
            linkedTo: null
        };
        newClips.push(newClip);
    });
    
    // Re-link clips if there were linked pairs
    for (let i = 0; i < clipboard.length; i++) {
        const original = clipboard[i];
        if (original.linkedTo) {
            const linkedIndex = clipboard.findIndex(c => c.id === original.linkedTo);
            if (linkedIndex !== -1) {
                newClips[i].linkedTo = newClips[linkedIndex].id;
            }
        }
    }
    
    clips.push(...newClips);
    selectedClips = newClips;
    updateDuration();
    renderTimeline();
    toast('info', 'Pasted clips');
}

function selectAllClips() {
    selectedClips = [...clips];
    renderTimeline();
}

function addTransition() {
    pushUndo();
    const videoClips = clips.filter(c => c.track === 'video').sort((a, b) => a.startTime - b.startTime);
    
    for (let i = 0; i < videoClips.length - 1; i++) {
        const current = videoClips[i];
        const next = videoClips[i + 1];
        const gap = next.startTime - (current.startTime + current.duration);
        
        // If clips are adjacent or overlapping
        if (Math.abs(gap) < 0.1) {
            const transTime = current.startTime + current.duration;
            
            // Check if transition already exists
            if (!transitions.some(t => Math.abs(t.time - transTime) < 0.1)) {
                transitions.push({
                    id: Date.now() + Math.random(),
                    time: transTime,
                    duration: 1, // 1 second dissolve
                    type: currentTransitionType
                });
                renderTimeline();
                toast('success', `Added ${currentTransitionType} transition`);
                return;
            }
        }
    }
    
    toast('info', 'Position clips adjacent to add transition');
}

function selectTransitionType(type) {
    currentTransitionType = type;
    document.querySelectorAll('.transition-item').forEach(item => {
        const selected = item.dataset.transition === type;
        item.classList.toggle('selected', selected);
        item.setAttribute('aria-pressed', String(selected));
    });
}

function unlinkAudio() {
    selectedClips.forEach(clip => {
        if (clip.linkedTo) {
            const linked = clips.find(c => c.id === clip.linkedTo);
            if (linked) {
                linked.linkedTo = null;
            }
            clip.linkedTo = null;
        }
    });
    toast('info', 'Audio unlinked');
}

// ==================== PLAYBACK ====================
function togglePlay() {
    if (isPlaying) {
        pause();
    } else {
        play();
    }
}

function play() {
    if (clips.length === 0) return;
    
    isPlaying = true;
    document.getElementById('playBtn').innerHTML = '⏸';
    
    // Start video playback
    if (previewVideo.src) {
        previewVideo.currentTime = currentTime;
        previewVideo.play();
    }
    
    playbackInterval = setInterval(() => {
        currentTime += 1/30;
        if (currentTime >= duration) {
            pause();
            currentTime = 0;
        }
        updatePlayhead();
    }, 1000/30);
}

function pause() {
    isPlaying = false;
    document.getElementById('playBtn').innerHTML = '▶';
    
    if (previewVideo.src) {
        previewVideo.pause();
    }
    
    if (playbackInterval) {
        clearInterval(playbackInterval);
        playbackInterval = null;
    }
}

function goToStart() {
    currentTime = 0;
    updatePlayhead();
    seekPreview(currentTime);
}

function goToEnd() {
    currentTime = duration;
    updatePlayhead();
    seekPreview(currentTime);
}

function stepForward() {
    currentTime = Math.min(duration, currentTime + 1);
    updatePlayhead();
    seekPreview(currentTime);
}

function stepBackward() {
    currentTime = Math.max(0, currentTime - 1);
    updatePlayhead();
    seekPreview(currentTime);
}

function updatePlayhead() {
    const playhead = document.getElementById('playhead');
    playhead.style.left = (currentTime * pixelsPerSecond) + 'px';
    
    document.getElementById('currentTime').textContent = formatTimecode(currentTime);
    
    // Auto-scroll timeline
    const container = document.getElementById('tracksContainer');
    const playheadX = currentTime * pixelsPerSecond;
    const viewLeft = container.scrollLeft;
    const viewRight = viewLeft + container.offsetWidth - 100;
    
    if (playheadX > viewRight) {
        container.scrollLeft = playheadX - 100;
    } else if (playheadX < viewLeft) {
        container.scrollLeft = Math.max(0, playheadX - 100);
    }
}

function updateDuration() {
    if (clips.length === 0) {
        duration = 0;
    } else {
        duration = Math.max(...clips.map(c => c.startTime + c.duration));
    }
    document.getElementById('totalTime').textContent = formatTimecode(duration);
}

// ==================== PREVIEW ====================
function loadPreview(url) {
    previewVideo.src = url;
    previewVideo.style.display = 'block';
    document.getElementById('previewPlaceholder').style.display = 'none';
}

function seekPreview(time) {
    if (previewVideo.src) {
        previewVideo.currentTime = time;
    }
}

function onVideoTimeUpdate() {
    if (isPlaying) {
        currentTime = previewVideo.currentTime;
        updatePlayhead();
    }
}

function onVideoLoaded() {
    console.log('Video loaded:', previewVideo.duration);
}

function onVideoEnded() {
    pause();
}

function setVolume(value) {
    previewVideo.volume = value / 100;
}

function toggleMute() {
    previewVideo.muted = !previewVideo.muted;
    const button = document.querySelector('.volume-btn');
    button.textContent = previewVideo.muted ? '🔇' : '🔊';
    button.setAttribute('aria-pressed', String(previewVideo.muted));
    button.setAttribute('aria-label', previewVideo.muted ? 'Unmute preview audio' : 'Mute preview audio');
}

// ==================== CLIP PROPERTIES ====================
function updateClipPropertiesPanel(clip) {
    document.querySelector('#clipProperties .properties-section-title').textContent = clip.name;
    document.getElementById('clipPropertiesContent').innerHTML = `
        <div style="font-size: 11px; color: var(--text-2);">
            Duration: ${formatTimecode(clip.duration)}<br>
            Start: ${formatTimecode(clip.startTime)}<br>
            Type: ${clip.type}
        </div>
    `;
    
    // Update sliders
    document.getElementById('opacitySlider').value = clip.opacity;
    document.getElementById('opacityValue').textContent = clip.opacity + '%';
    document.getElementById('scaleSlider').value = clip.scale;
    document.getElementById('scaleValue').textContent = clip.scale + '%';
    document.getElementById('rotationSlider').value = clip.rotation;
    document.getElementById('rotationValue').textContent = clip.rotation + '°';
    document.getElementById('brightnessSlider').value = clip.brightness;
    document.getElementById('brightnessValue').textContent = clip.brightness;
    document.getElementById('contrastSlider').value = clip.contrast;
    document.getElementById('contrastValue').textContent = clip.contrast;
    document.getElementById('saturationSlider').value = clip.saturation;
    document.getElementById('saturationValue').textContent = clip.saturation;
    document.getElementById('volumeValueSlider').value = clip.volume ?? 100;
    document.getElementById('volumeValue').textContent = (clip.volume ?? 100) + '%';
}

function clearClipPropertiesPanel() {
    document.querySelector('#clipProperties .properties-section-title').textContent = 'No Clip Selected';
    document.getElementById('clipPropertiesContent').innerHTML = 'Select a clip on the timeline to view its properties';
}

function updateClipProperty(property, value) {
    const valueEl = document.getElementById(property + 'Value');
    if (valueEl) {
        valueEl.textContent = value + (property === 'rotation' ? '°' : '%');
    }
    
    selectedClips.forEach(clip => {
        clip[property] = parseInt(value);
    });
    if (selectedClips.length > 0) renderTimeline();
}

function applyQuickEffect(effect) {
    if (selectedClips.length === 0) {
        toast('warning', 'Select a clip before applying an effect');
        return;
    }
    const presets = {
        cinematic: { brightness: -5, contrast: 20, saturation: -10 },
        bright: { brightness: 18, contrast: 5, saturation: 8 },
        mono: { brightness: 0, contrast: 12, saturation: -100 },
        reset: { brightness: 0, contrast: 0, saturation: 0 },
    };
    const preset = presets[effect];
    if (!preset) return;
    pushUndo();
    selectedClips.forEach(clip => Object.assign(clip, preset));
    updateClipPropertiesPanel(selectedClips[0]);
    renderTimeline();
    toast('success', 'Effect applied to selected clip');
}

// ==================== EXPORT ====================
function showExportModal() {
    if (clips.length === 0) {
        toast('error', 'No clips to export');
        return;
    }
    const modal = document.getElementById('exportModal');
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    renderExportPreflight();
    document.getElementById('exportFormat').focus();
}

function hideExportModal() {
    if (exportInProgress) return;
    const modal = document.getElementById('exportModal');
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    document.getElementById('exportButton').focus();
}

function sanitizeDownloadName(value) {
    const cleaned = String(value || '')
        .normalize('NFKC')
        .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '-')
        .replace(/^[.-]+/, '')
        .replace(/[.\s]+$/g, '')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 80);
    if (!cleaned || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(cleaned)) {
        return 'clipforge-export';
    }
    return cleaned;
}

function buildExportPreflight() {
    const format = document.getElementById('exportFormat')?.value || 'mp4';
    const resolution = document.getElementById('exportResolution')?.value || 'original';
    const reasons = [];
    const notes = [];
    const videoClips = clips
        .filter(clip => clip.track === 'video')
        .sort((a, b) => a.startTime - b.startTime);

    if (!ffmpegLoaded) reasons.push('The FFmpeg engine is not ready.');
    if (browserFfmpegJob && browserFfmpegJob.type !== 'export') {
        reasons.push(
            `${browserFfmpegJob.label} is ${browserFfmpegJob.state}; `
            + 'finish or cancel it before exporting.',
        );
    }
    if (videoClips.length === 0) reasons.push('At least one video clip is required.');
    if (videoClips.some(clip => clip.type !== 'video')) {
        reasons.push('Image timeline clips are not supported by browser export.');
    }
    if (transitions.length > 0) {
        reasons.push('Transitions are visible in the editor but are not yet rendered by browser export.');
    }
    const independentAudio = clips.filter(
        clip => (clip.track === 'audio' || clip.track === 'music') && !clip.linkedTo,
    );
    if (independentAudio.length > 0) {
        reasons.push('Unlinked audio and music tracks are not yet mixed by browser export.');
    }
    if (
        trackStates.video.visible === false
        || trackStates.audio.muted
        || trackStates.audio.solo
        || trackStates.music.muted
        || trackStates.music.solo
    ) {
        reasons.push('Track visibility, mute, and solo states are preview-only and must be reset before export.');
    }

    videoClips.forEach((clip, index) => {
        const media = mediaItems.find(item => item.id == clip.mediaId);
        if (!media || media.missing || !media.file) {
            reasons.push(`Clip ${index + 1} (${clip.name || 'unnamed'}) needs its source relinked.`);
        }
        if (!(Number(clip.duration) > 0) || Number(clip.inPoint) < 0) {
            reasons.push(`Clip ${index + 1} has invalid trim timing.`);
        }
        if (Number(clip.opacity ?? 100) !== 100 || Number(clip.scale ?? 100) !== 100) {
            reasons.push(`Clip ${index + 1} uses opacity or scale, which browser export does not render yet.`);
        }
        const linkedAudio = clips.find(
            candidate => candidate.id === clip.linkedTo && candidate.track === 'audio',
        );
        if (!linkedAudio) {
            reasons.push(`Clip ${index + 1} has changed audio linkage, which browser export cannot represent safely.`);
        } else if (
            Number(linkedAudio.volume ?? 100) !== 100
            || Math.abs(finiteNumber(linkedAudio.startTime) - finiteNumber(clip.startTime)) > 0.01
            || Math.abs(finiteNumber(linkedAudio.duration) - finiteNumber(clip.duration)) > 0.01
            || Math.abs(finiteNumber(linkedAudio.inPoint) - finiteNumber(clip.inPoint)) > 0.01
        ) {
            reasons.push(`Clip ${index + 1} has edited linked-audio timing or volume, which browser export does not render yet.`);
        }
        if (index === 0 && finiteNumber(clip.startTime) > 0.05) {
            reasons.push(`There is an unrendered ${finiteNumber(clip.startTime).toFixed(2)} second gap before the first clip.`);
        } else if (index > 0) {
            const previous = videoClips[index - 1];
            const gap = Number(clip.startTime) - (Number(previous.startTime) + Number(previous.duration));
            if (Math.abs(gap) > 0.05) {
                reasons.push(
                    gap > 0
                        ? `There is an unrendered ${gap.toFixed(2)} second gap before clip ${index + 1}.`
                        : `Clip ${index + 1} overlaps the preceding clip without a supported transition.`,
                );
            }
        }
    });

    if (resolution === 'original' && videoClips.length > 1) {
        const dimensions = new Set(
            videoClips.map(clip => {
                const media = mediaItems.find(item => item.id == clip.mediaId);
                return media?.width && media?.height ? `${media.width}x${media.height}` : 'unknown';
            }),
        );
        if (dimensions.size > 1 || dimensions.has('unknown')) {
            reasons.push('Multi-clip original-resolution export requires matching, known source dimensions; choose a fixed resolution.');
        }
    }

    notes.push('Video trim, rotation, brightness, contrast, and saturation are rendered.');
    notes.push(
        format === 'gif'
            ? 'GIF has no audio by format; embedded source audio will be omitted.'
            : 'Embedded source audio is preserved when present.',
    );
    return { supported: reasons.length === 0, reasons: [...new Set(reasons)], notes, videoClips };
}

function renderExportPreflight() {
    const result = buildExportPreflight();
    const panel = document.getElementById('exportPreflight');
    const confirm = document.getElementById('confirmExportButton');
    if (!panel || !confirm) return result;
    panel.className = `export-preflight ${result.supported ? 'ready' : 'blocked'}`;
    panel.innerHTML = result.supported
        ? `<strong>Ready to export ${result.videoClips.length} video clip(s).</strong><ul>${result.notes.map(note => `<li>${escapeHtml(note)}</li>`).join('')}</ul>`
        : `<strong>Export blocked until these timeline states are resolved:</strong><ul>${result.reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>`;
    confirm.disabled = !result.supported;
    return result;
}

async function runFfmpeg(job, args, label) {
    await job.exec(args, label);
}

function cancelExport() {
    if (!exportInProgress || exportCancelRequested) return;
    cancelBrowserFfmpegJob('export');
}

async function exportVideo() {
    const preflight = renderExportPreflight();
    if (!preflight.supported || exportInProgress) return;

    hideExportModal();

    const overlay = document.getElementById('loadingOverlay');
    const cancelButton = document.getElementById('cancelExportButton');
    overlay.classList.remove('hidden');
    document.getElementById('loadingText').textContent = 'Exporting video...';
    document.getElementById('loadingProgress').style.width = '0%';
    document.getElementById('loadingProgressTrack').setAttribute('aria-valuenow', '0');
    cancelButton.hidden = false;
    cancelButton.disabled = false;
    exportInProgress = true;
    exportCancelRequested = false;

    try {
        await runBrowserFfmpegJob(
            'export',
            { label: 'Export' },
            async job => {
                const format = document.getElementById('exportFormat').value;
                const resolution = document.getElementById('exportResolution').value;
                const quality = document.getElementById('exportQuality').value;
                const filename = (
                    sanitizeDownloadName(document.getElementById('exportFilename').value)
                        .replace(/\.(mp4|webm|gif)$/i, '')
                    || 'clipforge-export'
                );
                document.getElementById('exportFilename').value = filename;
                const videoClips = preflight.videoClips;
                const segmentFiles = [];

                for (let i = 0; i < videoClips.length; i++) {
                    const clip = videoClips[i];
                    const media = mediaItems.find(item => item.id === clip.mediaId);
                    if (!media?.file) {
                        throw new Error(`Source is missing for ${clip.name}`);
                    }
                    document.getElementById('loadingText').textContent =
                        `Processing clip ${i + 1}/${videoClips.length}...`;
                    const extension =
                        media.file.name.match(/\.[A-Za-z0-9]{1,8}$/)?.[0] || '.bin';
                    const inputName = job.path(`input_${i}`, extension);
                    const segmentName = job.path(`segment_${i}`, '.mp4');
                    await job.writeSource(inputName, media.file);

                    const args = [];
                    if (clip.inPoint > 0) args.push('-ss', String(clip.inPoint));
                    args.push('-i', inputName);
                    args.push('-t', String(clip.duration));
                    args.push('-map', '0:v:0', '-map', '0:a?');

                    const filters = [];
                    if (resolution && resolution !== 'original') {
                        const [width, height] = resolution.split(':');
                        filters.push(
                            `scale=${width}:${height}:force_original_aspect_ratio=decrease,`
                            + `pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2`,
                        );
                    }
                    if (clip.brightness || clip.contrast || clip.saturation) {
                        filters.push(
                            `eq=brightness=${clip.brightness / 100}:`
                            + `contrast=${1 + clip.contrast / 100}:`
                            + `saturation=${1 + clip.saturation / 100}`,
                        );
                    }
                    if (clip.rotation) {
                        filters.push(`rotate=${clip.rotation}*PI/180`);
                    }
                    if (filters.length > 0) args.push('-vf', filters.join(','));

                    args.push(
                        '-c:v', 'libx264',
                        '-crf', quality,
                        '-preset', 'fast',
                        '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac',
                        '-b:a', '192k',
                        '-y', segmentName,
                    );
                    await runFfmpeg(job, args, `Clip ${i + 1}`);
                    await job.deleteFile(inputName);
                    segmentFiles.push(segmentName);
                }

                let finalOutput;
                if (segmentFiles.length === 1 && format === 'mp4') {
                    finalOutput = segmentFiles[0];
                } else if (segmentFiles.length === 1) {
                    const outputName = job.path('output', `.${format}`);
                    const args = ['-i', segmentFiles[0]];
                    if (format === 'webm') {
                        args.push(
                            '-c:v', 'libvpx-vp9', '-crf', quality, '-b:v', '0',
                            '-c:a', 'libopus',
                        );
                    } else {
                        args.push(
                            '-vf', 'fps=15,scale=480:-1:flags=lanczos',
                            '-loop', '0',
                        );
                    }
                    args.push('-y', outputName);
                    await runFfmpeg(job, args, 'Final format conversion');
                    await job.deleteFile(segmentFiles[0]);
                    finalOutput = outputName;
                } else {
                    const outputName = job.path('output', `.${format}`);
                    const concatName = job.path('concat', '.txt');
                    const concatList = segmentFiles
                        .map(path => `file '${path}'`)
                        .join('\n');
                    await job.writeFile(
                        concatName,
                        new TextEncoder().encode(concatList),
                    );
                    document.getElementById('loadingText').textContent = 'Joining clips...';
                    const args = ['-f', 'concat', '-safe', '0', '-i', concatName];
                    if (format === 'mp4') {
                        args.push('-c', 'copy', '-movflags', '+faststart');
                    } else if (format === 'webm') {
                        args.push(
                            '-c:v', 'libvpx-vp9', '-crf', quality, '-b:v', '0',
                            '-c:a', 'libopus',
                        );
                    } else {
                        args.push(
                            '-vf', 'fps=15,scale=480:-1:flags=lanczos',
                            '-loop', '0',
                        );
                    }
                    args.push('-y', outputName);
                    await runFfmpeg(job, args, 'Timeline join');
                    for (const path of segmentFiles) await job.deleteFile(path);
                    await job.deleteFile(concatName);
                    finalOutput = outputName;
                }

                const data = await job.readFile(finalOutput);
                const mimeType = format === 'gif' ? 'image/gif' : `video/${format}`;
                const blob = new Blob([data.buffer], { type: mimeType });
                const anchor = document.createElement('a');
                const blobUrl = URL.createObjectURL(blob);
                anchor.href = blobUrl;
                anchor.download = `${filename}.${format}`;
                anchor.click();
                setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
                await job.deleteFile(finalOutput);
                toast(
                    'success',
                    `Exported ${videoClips.length} clip(s) successfully!`,
                );
            },
        );
    } catch (e) {
        console.error('Export error:', e);
        toast(
            e instanceof BrowserJobCancelledError ? 'info' : 'error',
            e instanceof BrowserJobCancelledError
                ? 'Export cancelled; FFmpeg is ready for the next job'
                : `Export failed: ${e.message}`,
        );
    } finally {
        cancelButton.hidden = true;
        cancelButton.disabled = false;
        exportInProgress = false;
        exportCancelRequested = false;
        if (ffmpegLoaded) overlay.classList.add('hidden');
    }
}

// ==================== PROJECT MANAGEMENT ====================
function finiteNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function projectMediaReference(media) {
    const file = media.file;
    return {
        name: String(file?.name || media.name || 'media').slice(0, 255),
        size: finiteNumber(file?.size ?? media.reference?.size, 0),
        lastModified: finiteNumber(file?.lastModified ?? media.reference?.lastModified, 0),
        mime: String(file?.type || media.reference?.mime || '').slice(0, 127),
        relativePath: String(file?.webkitRelativePath || media.reference?.relativePath || '').slice(0, 1024),
    };
}

function serializeProject() {
    const project = {
        schema: PROJECT_SCHEMA,
        version: PROJECT_SCHEMA_VERSION,
        savedAt: new Date().toISOString(),
        name: document.querySelector('.project-name')?.textContent?.trim() || 'Untitled Project',
        media: mediaItems.map(media => ({
            id: media.id,
            name: String(media.name || '').slice(0, 255),
            type: ['video', 'audio', 'image'].includes(media.type) ? media.type : 'video',
            duration: finiteNumber(media.duration),
            width: finiteNumber(media.width),
            height: finiteNumber(media.height),
            reference: projectMediaReference(media),
            proxy: media.proxyKey ? {
                key: media.proxyKey,
                profile: BROWSER_PROXY_PROFILE,
                size: finiteNumber(media.proxySize),
            } : null,
        })),
        clips: clips.map(clip => ({
            id: clip.id,
            mediaId: clip.mediaId,
            track: ['video', 'audio', 'music'].includes(clip.track) ? clip.track : 'video',
            startTime: finiteNumber(clip.startTime),
            duration: finiteNumber(clip.duration),
            inPoint: finiteNumber(clip.inPoint),
            outPoint: finiteNumber(clip.outPoint, finiteNumber(clip.duration)),
            name: String(clip.name || '').slice(0, 255),
            type: ['video', 'audio', 'image'].includes(clip.type) ? clip.type : 'video',
            linkedTo: clip.linkedTo ?? null,
            opacity: finiteNumber(clip.opacity, 100),
            scale: finiteNumber(clip.scale, 100),
            rotation: finiteNumber(clip.rotation),
            brightness: finiteNumber(clip.brightness),
            contrast: finiteNumber(clip.contrast),
            saturation: finiteNumber(clip.saturation),
            volume: finiteNumber(clip.volume, 100),
        })),
        transitions: transitions.map(transition => ({
            id: transition.id,
            time: finiteNumber(transition.time),
            duration: finiteNumber(transition.duration, 1),
            type: ['dissolve', 'fade', 'wipe', 'zoom'].includes(transition.type)
                ? transition.type
                : 'dissolve',
        })),
        timeline: {
            pixelsPerSecond: finiteNumber(pixelsPerSecond, 50),
            trackStates: JSON.parse(JSON.stringify(trackStates)),
        },
    };
    return project;
}

function normalizeProject(raw) {
    if (!raw || typeof raw !== 'object') throw new Error('Project file must contain a JSON object');
    let source = raw;
    if (raw.schema !== PROJECT_SCHEMA) {
        if (!Array.isArray(raw.mediaItems) || !Array.isArray(raw.clips)) {
            throw new Error('This is not a ClipForge project file');
        }
        source = {
            schema: PROJECT_SCHEMA,
            version: PROJECT_SCHEMA_VERSION,
            name: 'Imported legacy project',
            media: raw.mediaItems.map(media => ({
                ...media,
                reference: {
                    name: media.name,
                    size: media.size || 0,
                    lastModified: media.lastModified || 0,
                    mime: media.type || '',
                },
            })),
            clips: raw.clips,
            transitions: raw.transitions || [],
            timeline: { pixelsPerSecond: 50, trackStates: {} },
        };
    }
    if (finiteNumber(source.version) > PROJECT_SCHEMA_VERSION) {
        throw new Error(`Project schema v${source.version} is newer than this editor supports`);
    }
    if (!Array.isArray(source.media) || !Array.isArray(source.clips) || !Array.isArray(source.transitions || [])) {
        throw new Error('Project media, clips, and transitions must be arrays');
    }
    if (source.media.length > 5000 || source.clips.length > 10000 || source.transitions.length > 5000) {
        throw new Error('Project exceeds the browser editor safety limits');
    }

    const canonicalIdMap = (items, prefix) => {
        const ids = new Map();
        items.forEach((item, index) => {
            if (!item || typeof item !== 'object') {
                throw new Error(`Project ${prefix} ${index + 1} must be an object`);
            }
            const sourceId = String(item.id ?? `${prefix}-source-${index + 1}`);
            if (ids.has(sourceId)) {
                throw new Error(`Project contains duplicate ${prefix} identifiers`);
            }
            ids.set(sourceId, `${prefix}-${index + 1}`);
        });
        return ids;
    };
    const mediaIdMap = canonicalIdMap(source.media, 'media');
    const clipIdMap = canonicalIdMap(source.clips, 'clip');
    const transitionIdMap = canonicalIdMap(source.transitions || [], 'transition');

    const media = source.media.map((item, index) => ({
        id: mediaIdMap.get(String(item.id ?? `media-source-${index + 1}`)),
        name: String(item.name || item.reference?.name || `Media ${index + 1}`).slice(0, 255),
        type: ['video', 'audio', 'image'].includes(item.type) ? item.type : 'video',
        duration: Math.max(0, finiteNumber(item.duration)),
        width: Math.max(0, finiteNumber(item.width)),
        height: Math.max(0, finiteNumber(item.height)),
        reference: {
            name: String(item.reference?.name || item.name || '').slice(0, 255),
            size: Math.max(0, finiteNumber(item.reference?.size)),
            lastModified: Math.max(0, finiteNumber(item.reference?.lastModified)),
            mime: String(item.reference?.mime || '').slice(0, 127),
            relativePath: String(item.reference?.relativePath || '').slice(0, 1024),
        },
        proxy: item.proxy && typeof item.proxy === 'object' ? {
            key: String(item.proxy.key || '').slice(0, 1024),
            profile: finiteNumber(item.proxy.profile),
            size: Math.max(0, finiteNumber(item.proxy.size)),
        } : null,
        file: null,
        url: null,
        proxyUrl: null,
        proxyKey: null,
        proxySize: 0,
        proxyActive: false,
        thumbnail: null,
        waveform: null,
        missing: true,
    }));
    const normalizedClips = source.clips.map((clip, index) => {
        const mediaId = mediaIdMap.get(String(clip.mediaId));
        if (!mediaId) {
            throw new Error(`Clip ${index + 1} references media that is not in the project`);
        }
        const linkedTo = clip.linkedTo == null
            ? null
            : clipIdMap.get(String(clip.linkedTo));
        if (clip.linkedTo != null && !linkedTo) {
            throw new Error(`Clip ${index + 1} links to a clip that is not in the project`);
        }
        return {
            id: clipIdMap.get(String(clip.id ?? `clip-source-${index + 1}`)),
            mediaId,
            track: ['video', 'audio', 'music'].includes(clip.track) ? clip.track : 'video',
            startTime: Math.max(0, finiteNumber(clip.startTime)),
            duration: Math.max(0, finiteNumber(clip.duration)),
            inPoint: Math.max(0, finiteNumber(clip.inPoint)),
            outPoint: Math.max(0, finiteNumber(clip.outPoint, finiteNumber(clip.duration))),
            name: String(clip.name || `Clip ${index + 1}`).slice(0, 255),
            type: ['video', 'audio', 'image'].includes(clip.type) ? clip.type : 'video',
            linkedTo,
            opacity: finiteNumber(clip.opacity, 100),
            scale: finiteNumber(clip.scale, 100),
            rotation: finiteNumber(clip.rotation),
            brightness: finiteNumber(clip.brightness),
            contrast: finiteNumber(clip.contrast),
            saturation: finiteNumber(clip.saturation),
            volume: finiteNumber(clip.volume, 100),
            thumbnail: null,
            waveform: null,
            url: null,
        };
    });
    return {
        schema: PROJECT_SCHEMA,
        version: PROJECT_SCHEMA_VERSION,
        name: String(source.name || 'Untitled Project').slice(0, 100),
        media,
        clips: normalizedClips,
        transitions: (source.transitions || []).map((transition, index) => ({
            id: transitionIdMap.get(String(transition.id ?? `transition-source-${index + 1}`)),
            time: Math.max(0, finiteNumber(transition.time)),
            duration: Math.max(0.01, finiteNumber(transition.duration, 1)),
            type: ['dissolve', 'fade', 'wipe', 'zoom'].includes(transition.type)
                ? transition.type
                : 'dissolve',
        })),
        timeline: {
            pixelsPerSecond: Math.min(200, Math.max(10, finiteNumber(source.timeline?.pixelsPerSecond, 50))),
            trackStates: source.timeline?.trackStates || {},
        },
    };
}

function releaseMediaUrls() {
    mediaItems.forEach(media => {
        if (media.url?.startsWith('blob:')) URL.revokeObjectURL(media.url);
        if (media.proxyUrl?.startsWith('blob:')) URL.revokeObjectURL(media.proxyUrl);
    });
}

function applyProjectSnapshot(raw, sourceLabel = 'project') {
    const project = normalizeProject(raw);
    projectLoading = true;
    releaseMediaUrls();
    mediaItems = project.media;
    clips = project.clips;
    transitions = project.transitions;
    selectedClips = [];
    pixelsPerSecond = project.timeline.pixelsPerSecond;
    Object.keys(trackStates).forEach(track => {
        const incoming = project.timeline.trackStates?.[track] || {};
        Object.keys(trackStates[track]).forEach(key => {
            const defaultValue = track === 'video' && key === 'visible';
            trackStates[track][key] = key in incoming ? Boolean(incoming[key]) : defaultValue;
        });
    });
    document.querySelector('.project-name').textContent = project.name;
    updateDuration();
    renderMediaList();
    renderTimeline();
    clearClipPropertiesPanel();
    projectLoading = false;
    scheduleProjectRecovery();
    toast('success', `Loaded ${sourceLabel}; relink ${mediaItems.length} local source file(s)`);
    return project;
}

async function handleProjectFileInput(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    try {
        if (file.size > 5 * 1024 * 1024) throw new Error('Project file exceeds the 5 MB safety limit');
        const raw = JSON.parse(await file.text());
        applyProjectSnapshot(raw, file.name);
    } catch (error) {
        toast('error', `Could not open project: ${error.message}`);
    }
}

async function handleRelinkFileInput(event) {
    const files = [...(event.target.files || [])];
    event.target.value = '';
    if (files.length === 0) return;
    let relinked = 0;
    for (const media of mediaItems.filter(item => item.missing)) {
        const reference = media.reference || {};
        const file = files.find(candidate => {
            if (candidate.name !== reference.name) return false;
            const sizeMatches = !reference.size || candidate.size === reference.size;
            const modifiedMatches = !reference.lastModified || candidate.lastModified === reference.lastModified;
            return sizeMatches && modifiedMatches;
        });
        if (!file) continue;
        media.file = file;
        media.url = URL.createObjectURL(file);
        media.missing = false;
        media.name = file.name;
        if (media.type === 'image') {
            media.thumbnail = media.url;
        }
        await restoreBrowserProxy(media);
        clips.filter(clip => clip.mediaId == media.id).forEach(clip => {
            clip.url = media.proxyUrl || media.url;
            clip.proxyActive = Boolean(media.proxyUrl);
            clip.name = media.name;
            if (media.thumbnail) clip.thumbnail = media.thumbnail;
        });
        relinked += 1;
    }
    renderMediaList();
    renderTimeline();
    toast(
        relinked ? 'success' : 'error',
        relinked
            ? `Relinked ${relinked} source file(s); ${mediaItems.filter(item => item.missing).length} still missing`
            : 'No selected files matched the saved name, size, and modified time',
    );
}

function openRecoveryDb() {
    if (!('indexedDB' in window)) return Promise.reject(new Error('IndexedDB is unavailable'));
    if (!recoveryDbPromise) {
        recoveryDbPromise = new Promise((resolve, reject) => {
            const request = indexedDB.open(PROJECT_DB_NAME, 2);
            request.onupgradeneeded = () => {
                if (!request.result.objectStoreNames.contains(PROJECT_STORE_NAME)) {
                    request.result.createObjectStore(PROJECT_STORE_NAME);
                }
                if (!request.result.objectStoreNames.contains('proxies')) {
                    request.result.createObjectStore('proxies');
                }
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error || new Error('Could not open recovery storage'));
        });
    }
    return recoveryDbPromise;
}

async function recoveryStore(mode, value) {
    const db = await openRecoveryDb();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction(PROJECT_STORE_NAME, mode === 'get' ? 'readonly' : 'readwrite');
        const store = transaction.objectStore(PROJECT_STORE_NAME);
        const request = mode === 'get'
            ? store.get(PROJECT_RECOVERY_KEY)
            : store.put(value, PROJECT_RECOVERY_KEY);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error('Recovery storage operation failed'));
    });
}

async function browserProxyStore(mode, key = null, value = null) {
    const db = await openRecoveryDb();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction('proxies', mode === 'get' || mode === 'all' ? 'readonly' : 'readwrite');
        const store = transaction.objectStore('proxies');
        let request;
        if (mode === 'get') request = store.get(key);
        else if (mode === 'put') request = store.put(value, key);
        else if (mode === 'delete') request = store.delete(key);
        else if (mode === 'all') request = store.getAll();
        else {
            reject(new Error(`Unsupported browser proxy operation: ${mode}`));
            return;
        }
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error('Browser proxy storage operation failed'));
    });
}

function scheduleProjectRecovery() {
    if (projectLoading || (mediaItems.length === 0 && clips.length === 0)) return;
    clearTimeout(recoverySaveTimer);
    recoverySaveTimer = setTimeout(async () => {
        try {
            const snapshot = serializeProject();
            await recoveryStore('put', snapshot);
            recoverySnapshot = snapshot;
        } catch (error) {
            console.warn('Project recovery save failed:', error);
        }
    }, 400);
}

async function initProjectRecovery() {
    try {
        recoverySnapshot = await recoveryStore('get');
        if (recoverySnapshot) {
            document.getElementById('recoveryBar').hidden = false;
            document.getElementById('recoveryText').textContent =
                `Recover "${recoverySnapshot.name || 'Untitled Project'}" from ${recoverySnapshot.savedAt || 'browser storage'}.`;
        }
    } catch (error) {
        console.warn('Project recovery is unavailable:', error);
    }
}

function recoverLastProject() {
    if (!recoverySnapshot) {
        toast('error', 'No recoverable project is available');
        return;
    }
    applyProjectSnapshot(recoverySnapshot, 'browser recovery');
    document.getElementById('recoveryBar').hidden = true;
}

function saveProject() {
    const project = serializeProject();
    const json = JSON.stringify(project, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const a = document.createElement('a');
    const blobUrl = URL.createObjectURL(blob);
    a.href = blobUrl;
    a.download = `${sanitizeDownloadName(project.name)}.clipforge`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    scheduleProjectRecovery();
    toast('success', 'Project saved');
}

function undo() {
    if (undoStack.length === 0) return;
    redoStack.push({
        clips: clips.map(c => ({ ...c })),
        transitions: transitions.map(t => ({ ...t })),
    });
    const state = undoStack.pop();
    clips = state.clips;
    transitions = state.transitions;
    selectedClips = [];
    updateDuration();
    renderTimeline();
    clearClipPropertiesPanel();
    toast('info', 'Undo');
}

function redo() {
    if (redoStack.length === 0) return;
    undoStack.push({
        clips: clips.map(c => ({ ...c })),
        transitions: transitions.map(t => ({ ...t })),
    });
    const state = redoStack.pop();
    clips = state.clips;
    transitions = state.transitions;
    selectedClips = [];
    updateDuration();
    renderTimeline();
    clearClipPropertiesPanel();
    toast('info', 'Redo');
}

function showEditMenu(e) {
    // Could show a dropdown menu
}

// ==================== UTILITIES ====================
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function formatTimecode(seconds) {
    if (!seconds || isNaN(seconds)) return '00:00:00:00';
    
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    const f = Math.floor((seconds % 1) * 30);
    
    return [h, m, s, f].map(v => String(v).padStart(2, '0')).join(':');
}

function formatTimecodeShort(seconds) {
    if (!seconds || isNaN(seconds)) return '0:00';
    
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    
    return `${m}:${String(s).padStart(2, '0')}`;
}

function formatBytes(bytes) {
    const value = Math.max(0, Number(bytes) || 0);
    if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KiB`;
    if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
    return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GiB`;
}

function toast(type, message) {
    const container = document.getElementById('toastContainer');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    const icons = { success: '✓', error: '✕', info: 'ℹ', warning: '⚠' };
    el.innerHTML = `
        <span class="toast-icon">${icons[type] || 'ℹ'}</span>
        <span class="toast-text">${escapeHtml(message)}</span>
    `;
    container.appendChild(el);

    const dismiss = () => {
        el.style.transition = 'opacity 0.2s, transform 0.2s';
        el.style.opacity = '0';
        el.style.transform = 'translateX(40px)';
        setTimeout(() => el.remove(), 250);
    };
    el.addEventListener('click', dismiss);
    setTimeout(dismiss, 3500);
}

// ==================== MODULE EXPORTS ====================
// Expose stable browser-test and event-delegation entry points.
Object.assign(window, {
    addToTimeline, togglePlay, goToStart, goToEnd, stepForward, stepBackward,
    setTool, setZoom, splitClip, deleteSelected, copyClip, cutClip, pasteClip,
    selectAllClips, addTransition, selectTransitionType, unlinkAudio,
    setVolume, toggleMute, showExportModal, hideExportModal, exportVideo,
    cancelExport, saveProject, recoverLastProject, showEditMenu,
    generateBrowserProxy, updateClipProperty, applyQuickEffect,
    cancelBrowserFfmpegJob,
    getBrowserFfmpegJobState: () => ({
        active: browserFfmpegJob ? {
            id: browserFfmpegJob.id,
            type: browserFfmpegJob.type,
            state: browserFfmpegJob.state,
            progress: browserFfmpegJob.progress,
            cancelRequested: browserFfmpegJob.cancelRequested,
        } : null,
        last: lastBrowserFfmpegJob ? { ...lastBrowserFfmpegJob } : null,
        engineReady: ffmpegLoaded,
    }),
    clipforgeCancelBrowserJob: cancelBrowserFfmpegJob,
});
