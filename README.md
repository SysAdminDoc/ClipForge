# ClipForge v0.5.3

All-in-one video editor — Trim, Crop, Upscale, Interpolate, Convert, Filter, Audio, Streams, Batch — one tool, zero hassle.

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![Version](https://img.shields.io/badge/Version-0.5.3-orange)

**Web Editor:** [sysadmindoc.github.io/ClipForge](https://sysadmindoc.github.io/ClipForge/) — browser-based timeline editor with a pinned, self-hosted FFmpeg WASM runtime, undo/redo, and multi-clip export.

## Features

### Trim
- Dual-handle range slider for precise in/out point selection
- Set in/out points from video player position
- **Smart Cut** — re-encodes only at cut boundaries, stream-copies the middle (near-lossless + frame-accurate)
- Lossless mode (stream copy, no re-encode) for instant keyframe-aligned trims
- Full re-encode mode for exact frame accuracy
- Output format selection

### Crop & Rotate
- Interactive visual crop overlay on video frame
- Aspect ratio presets: 16:9, 9:16, 4:3, 1:1, 21:9
- Manual X/Y/W/H input with live preview
- Rotate: 90 CW, 90 CCW, 180
- Horizontal and vertical flip

### AI Enhance
- **Real-ESRGAN** integration for AI-powered super resolution (quality mode)
- **SPAN** integration for fast AI upscaling (~7x faster than Real-ESRGAN)
- Engine selector: choose Real-ESRGAN (quality) or SPAN (fast)
- Scale options: 2x, 3x, 4x
- Models: realesrgan-x4plus, anime-specific, animevideo, spanx2_ch48, spanx4_ch48
- **RIFE v4.25** frame interpolation for frame rate boosting (upgraded from v4.6)
- Model selector: v4.25, v4.22, v4.6, v4
- Multiplier options: 2x, 4x, 8x — converts 30fps to 60/120/240fps
- Frame extraction -> AI processing -> reassembly pipeline
- Preserves original audio
- Checksum-pinned Windows tool manager with resumable official-package downloads, safe extraction, license/version/path/capability status, and post-install executable verification
- Source-keyed reusable lossless frame cache shared by upscale and interpolation, with conservative storage preflight and invalidation when source metadata changes

### Convert
- Containers: MP4, MKV, WebM, MOV, AVI, GIF
- Video codecs: H.264, H.265, VP9, **AV1 (SVT-AV1 + libaom)**, stream copy
- Audio codecs: AAC, Opus, MP3, FLAC, stream copy, remove
- Resolution presets: 4K, 2K, 1080p, 720p, 480p, 360p
- FPS control, CRF quality slider with **codec-aware hints** (H.264/H.265 and AV1 ranges)
- Speed adjustment (0.1x - 10x) with proper atempo chaining
- Optimized single-pass GIF generation with palette
- Two-pass encoding toggle for higher quality output
- Hardware encoder auto-detection (NVENC, QSV, AMF)
- Built-in presets: YouTube 1080p/4K, Instagram Reel, TikTok, Discord 8MB/50MB, Twitter/X, Archive Lossless, Web Optimized, **Web AV1, AV1 High Quality**
- Custom preset save/load/delete with filesystem-safe naming
- **Editable FFmpeg command preview** with "Run Custom" button
- Estimated output file size
- **User-friendly error messages** for common FFmpeg failures

### Filters
- Color correction: brightness, contrast, saturation, hue, gamma (combined into single eq filter)
- Video stabilization (vidstab two-pass, **runs in background worker**)
- Noise reduction (nlmeans)
- Sharpening (unsharp)
- Deinterlacing (yadif)
- Subtitle burn-in (.srt, .ass)
- LUT import (.cube files)
- **Auto-captions via Whisper** — model/language selection, generates .srt files
- **Hardsub OCR via optional local Tesseract** — cancellable frame sampling, TSV line grouping, and atomic export to `.srt`
- Audio normalization with **loudness target presets** (YouTube -14 LUFS, Podcast -16, Broadcast -23, Spotify, Apple Music)
- **Silence detection** — adjustable threshold/duration with editable, selectable review markers showing segment count and total duration
- **Motion-tracked redaction** — linearly tracked, keyframe-editable blur regions for faces, plates, or other private content
- **Scene-change review markers** — FFmpeg scene scores become editable, jumpable markers; no cuts or keyframe placements change without user action
- **Silence auto-removal** — one-click removal of detected silent segments
- **Before/after filter preview** — side-by-side comparison (runs in background)

### Audio
- Extract audio to MP3, AAC, WAV, FLAC, OGG, or original codec
- Replace audio track with external audio file
- Mix replacement audio with original
- Remove all audio tracks (strip audio)

### Streams
- Full media info display (format, codecs, bitrate, resolution, duration)
- Stream list with checkboxes for selective remux
- Container remux without re-encoding
- Frame snapshot export at **current player position** (PNG/JPG)
- **ffprobe metadata cache** for instant re-open
- **Cancellable VMAF / PSNR / SSIM comparison** with sync offsets and JSON/CSV reports

### Batch Processing
- Add multiple files via browser or drag & drop
- Add entire folders of video files
- Operations: convert (MP4/MKV/WebM), downscale (1080p/720p), extract audio, remove audio, trim
- Custom output directory option
- Output filename template system with variables
- Per-file progress tracking with status indicators
- Durable queue processing with restart recovery, reorder/move controls, per-job priority, pause/resume, cancellation, and retry-failed support
- Configurable concurrent encodes with a CPU-safe effective cap reduced by usable hardware-encoder capability probes
- Explicit batch stream mapping, metadata/chapter policy, timestamp handling, and semantic multistream output validation
- Release-gated desktop/browser media contract matrix with VFR, multistream, relink, cancellation, recovery, and invalid-output coverage
- Post-completion actions (do nothing, open folder, notification sound)
- **Pre-flight disk-space check** with per-file size estimation
- **Overwrite confirmation dialog**

### Video Player
- Built-in video playback with play/pause, seek, volume
- Optional experimental libmpv backend with exact seek/frame-step and broader codec coverage; native libmpv versions below 0.41.0 fall back to Qt Multimedia, which remains the dependency-free default
- Frame-accurate stepping using **actual video fps** (not hardcoded 30fps)
- Playback speed control (0.25x - 2x)
- A-B loop for segment preview
- Thumbnail filmstrip with click-to-seek
- Detachable preview pane from the View menu for multi-monitor editing
- Cancellable 720p preview proxies with metadata-keyed atomic cache, source-change invalidation, size estimates, and original/proxy switching; exports always use the original
- Timecode display (current / total)

### General
- Catppuccin Mocha dark theme with premium UI polish
- **High-contrast theme** option (via `high_contrast` setting)
- Toast notifications with slide-in/slide-out animations
- Drag & drop file loading (single file or batch)
- Optional one-video URL import through local yt-dlp with destination-folder validation
- Recent files list in sidebar with double-click to reload
- Embedded console with full FFmpeg output and **placeholder guidance**
- Bounded severity-filtered console with redacted JSON diagnostics export
- Enhanced progress tracking with ETA, speed, and file size
- Schema-versioned transactional settings, recents, and presets with atomic writes, backup recovery, corrupt-data quarantine, and legacy migration
- **Capability-aware hardware encoding** — advertised FFmpeg encoders run bounded real encode probes off the GUI thread; unsupported devices are disabled with the driver error, and results are cached per FFmpeg binary/device signature
- **Accessible controls** — screen reader labels, keyboard focus states
- **Localization-ready UI** — strict English-default catalogs with named-placeholder validation and an `en-XA` pseudo-locale for long-string layout checks; set `CLIPFORGE_LOCALE=en-XA` for desktop or `window.CLIPFORGE_LOCALE = 'en-XA'` before loading the web editor
- All panels wrapped in scroll areas for small screens
- Status bar with current state
- Dependency detection with guidance for missing tools
- **Process tree cleanup** — cancelling kills child processes
- **Owned temp directory cleanup** on exit without touching another running instance
- **Atomic output safety** — validates staged media before replacing the chosen destination
- **Runtime security policy** — rejects unreviewed FFmpeg branches, keeps NVDEC fail-closed until a reviewed boundary, and requires the patched Qt 6.11.1 runtime
- **Cross-surface provenance** — release checks record exact versions, SHA-256 hashes, licenses, and lock/source metadata for browser assets, Python distributions, FFmpeg/ffprobe, optional libmpv, and managed AI tools; support diagnostics include the identities used by each desktop job
- Metadata-only GitHub release update checks run after startup when enabled (Help → Enable automatic update checks); release notices require a trusted API response and report whether digest-checked provenance/signature assets are published, without downloading or installing anything
- 228-test suite covering utilities, process safety, semantic media validation, generated media, probing, proxy/AI caching, supply-chain validation, diagnostics, persistence, browser modules/jobs, project sessions, filter graphs, OCR, URL import, scene review, localization, accessibility, batch concurrency, and update-policy contracts

### Web Editor

Try it in the browser: **[sysadmindoc.github.io/ClipForge](https://sysadmindoc.github.io/ClipForge/)**

- Timeline-based NLE with video, audio, and music tracks
- Import video, audio, and image files via drag & drop
- Full-duration video thumbnail strips with throttled hover scrubbing back to the playhead when the pointer leaves
- Clip splitting, trimming, moving, and snapping
- Transitions (cross dissolve, fade, wipe, zoom)
- Per-clip color correction and rotation
- Audio waveform visualization
- **ffmpeg.wasm 0.12.15** with a pinned, self-hosted FFmpeg n5.1.10 core
- Strict same-origin CSP with no executable runtime CDN dependency; the roughly 31 MB core is cached after first load for offline restarts
- Runtime hashes, build provenance, dependency inventory, and license files under `vendor/ffmpeg/`
- **Full undo/redo** (Ctrl+Z / Ctrl+Shift+Z, 50-deep history)
- **Truthful multi-clip export** — contiguous video clips and embedded source audio are rendered; preflight blocks unsupported gaps, overlaps, transitions, unlinked audio/music, or unrendered effects instead of silently dropping them
- **Shared browser timeline semantics** — preview and export resolve clip boundaries, source in-points, transforms, and track mute/solo state through the same tested timeline plan
- Cancellable MP4, WebM, and GIF export with sanitized filenames, cleanup, and quality/resolution options
- Serialized waveform, proxy, and export jobs with collision-proof virtual paths, shared progress/cancel state, guaranteed cleanup, and reusable-engine recovery
- Versioned `.cfproj` project save/load with external media references, atomic `.bak` backups, legacy `.clipforge` migration, IndexedDB crash recovery, and explicit local-media relinking
- IndexedDB-backed 720p browser proxies with visible estimates, cancellation, original/proxy switching, ten-entry pruning, and project relink restoration
- **Quota-aware cache lifecycle** — sampled-content identities prevent stale proxy/frame reuse; browser proxies and desktop preview/AI caches show byte usage and limits, prune by LRU, validate incomplete entries, and expose explicit purge controls
- **Cross-surface diagnostics** — desktop and browser support exports include bounded runtime/capability, storage, job, and error state with local paths, URL credentials/tokens, secret options, and private media metadata redacted by default
- **Browser module boundaries** — project schema, timeline planning, jobs, storage predicates, preview state, export preflight, and diagnostics redaction are tested without a DOM
- **Truthful timeline tools** — Edit exposes working undo/redo/clipboard actions; Slip changes source in/out points without moving a clip; Hand pans the scrollable timeline
- **Mediabunny evaluation spike** — run `python scripts/mediabunny_spike.py` to benchmark the pinned, evaluation-only Mediabunny 1.53.0 bundle against FFmpeg.wasm on a short MP4; production export remains FFmpeg.wasm
- Keyboard shortcuts: V (select), C (razor), S (split), Space (play/pause), J/K/L (transport)

## Requirements

- **Python 3.11+**
- **PyQt6 6.11+** (the locked Qt runtime is 6.11.1)
- **FFmpeg** (required for all operations)
  ```
  winget install ffmpeg        # Windows
  brew install ffmpeg           # macOS
  sudo apt install ffmpeg       # Linux
  ```
- **Real-ESRGAN** (optional, for AI upscaling — quality mode)
  - Download [realesrgan-ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN/releases)
  - Place in ClipForge directory or add to PATH
- **SPAN** (optional, for AI upscaling — fast mode)
  - Download [span-ncnn-vulkan](https://github.com/TNTwise/SPAN-ncnn-vulkan/releases)
  - Place in ClipForge directory or add to PATH
- **RIFE** (optional, for frame interpolation)
  - Download [rife-ncnn-vulkan](https://github.com/nihui/rife-ncnn-vulkan/releases) (or [TNTwise fork](https://github.com/TNTwise/rife-ncnn-vulkan) for v4.25 models)
  - Place in ClipForge directory or add to PATH
- **Whisper** (optional, for auto-captions)
  - `pip install openai-whisper`
- **Tesseract OCR** (optional, for extracting hardsubs to `.srt`)
  - Install the native Tesseract executable and its language data locally; ClipForge does not download OCR models
- **yt-dlp** (optional, for importing one video from an HTTP(S) URL)
  - Install the native `yt-dlp` executable and keep it on PATH; playlists and embedded URL credentials are rejected
- **libmpv** (optional, experimental preview backend)
  - Install the wrapper with `python -m pip install --require-hashes -r requirements-mpv.lock`, then install libmpv separately
  - On Windows, place `mpv-2.dll`/`libmpv-2.dll` beside ClipForge or set `CLIPFORGE_LIBMPV_DIR`
  - This adds a separate LGPL/GPL runtime and distribution footprint; Qt Multimedia remains the default

## Install & Run

```powershell
git clone https://github.com/SysAdminDoc/ClipForge.git
cd ClipForge
python -m venv .venv
.\.venv\Scripts\python -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python clipforge.py
```

On Linux or macOS, replace `.\.venv\Scripts\python` with `.venv/bin/python`.
ClipForge never changes the Python environment at runtime; install dependencies
explicitly before launch.

## Build and Verify

```powershell
python -m pip install --require-hashes -r requirements-dev.lock
python -m playwright install chromium
python scripts/sync_version.py --check
python scripts/release_check.py
python scripts/release_check.py --build
```

Release builds are unsigned and produced locally. `clipforge/version.py` is the
version source of truth; use `python scripts/sync_version.py --set X.Y.Z` to
update desktop, web, README, and Windows executable metadata together.
The release check runs the 228-test Python and headless-Chromium suite, then
generates disposable FFmpeg fixtures for audio/video,
subtitles, chapters, rotation, VFR, odd filenames, and core edit operations;
`--build` creates a fresh hash-locked environment, builds the unsigned
PyInstaller executable, opens fixture media in it, runs a tiny packaged
transcode, and launch-smokes the GUI.

## Usage

1. **Open a video** via button, drag & drop, or recent files
2. **Preview** using the built-in video player with frame stepping and speed control
3. **Select a tool** from the sidebar (8 panels available)
4. **Configure** options in the tool panel
5. **Preview the command** (Convert panel shows live editable FFmpeg command)
6. **Export** with the action button
7. **Monitor** progress via enhanced progress bar with ETA and speed

For batch processing, drag multiple files onto the window or use the Batch panel's Add Files/Add Folder buttons.

## Presets

ClipForge includes built-in presets optimized for popular platforms. You can also create, save, and manage custom presets from the Convert panel.

| Preset | Resolution | Codec | Bitrate Target |
| ------ | ---------- | ----- | -------------- |
| YouTube 1080p | 1920x1080 | H.264 | CRF 18 |
| YouTube 4K | 3840x2160 | H.264 | CRF 18 |
| Instagram Reel | 1080x1920 | H.264 | CRF 20 |
| TikTok | 1080x1920 | H.264 | CRF 20 |
| Discord 8MB | 1280x720 | H.264 | CRF 28 |
| Discord 50MB | 1920x1080 | H.264 | CRF 22 |
| Twitter/X | 1920x1080 | H.264 | CRF 22 |
| Web AV1 | 1920x1080 | SVT-AV1 | CRF 30 |
| AV1 High Quality | Original | SVT-AV1 | CRF 24 |
| Archive Lossless | Original | H.264 | Lossless |
| Web Optimized | 1280x720 | H.264 | CRF 23 |
| GIF | 480px wide | GIF | Palette-optimized |

## License

MIT
