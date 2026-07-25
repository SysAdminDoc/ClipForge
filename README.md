# ClipForge v0.5.1

All-in-one video editor — Trim, Crop, Upscale, Interpolate, Convert, Filter, Audio, Streams, Batch — one tool, zero hassle.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![Version](https://img.shields.io/badge/Version-0.5.1-orange)

**Web Editor:** [sysadmindoc.github.io/ClipForge](https://sysadmindoc.github.io/ClipForge/) — browser-based timeline editor with ffmpeg.wasm 0.12, undo/redo, and multi-clip export.

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
- Models: realesrgan-x4plus, anime-specific, animevideo, spanx4_ch48, ClearRealityV1
- **RIFE v4.25** frame interpolation for frame rate boosting (upgraded from v4.6)
- Model selector: v4.25, v4.22, v4.6, v4
- Multiplier options: 2x, 4x, 8x — converts 30fps to 60/120/240fps
- Frame extraction -> AI processing -> reassembly pipeline
- Preserves original audio

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
- Audio normalization with **loudness target presets** (YouTube -14 LUFS, Podcast -16, Broadcast -23, Spotify, Apple Music)
- **Silence detection** — adjustable threshold/duration, shows segment count and total duration
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
- Queue processing with cancel support
- Post-completion actions (do nothing, open folder, notification sound)
- **Pre-flight disk-space check** with per-file size estimation
- **Overwrite confirmation dialog**

### Video Player
- Built-in video playback with play/pause, seek, volume
- Frame-accurate stepping using **actual video fps** (not hardcoded 30fps)
- Playback speed control (0.25x - 2x)
- A-B loop for segment preview
- Thumbnail filmstrip with click-to-seek
- Timecode display (current / total)

### General
- Catppuccin Mocha dark theme with premium UI polish
- **High-contrast theme** option (via `high_contrast` setting)
- Toast notifications with slide-in/slide-out animations
- Drag & drop file loading (single file or batch)
- Recent files list in sidebar with double-click to reload
- Embedded console with full FFmpeg output and **placeholder guidance**
- Enhanced progress tracking with ETA, speed, and file size
- Settings persistence (window geometry, last directory, preferences)
- Hardware encoder status display in sidebar
- **Accessible controls** — screen reader labels, keyboard focus states
- All panels wrapped in scroll areas for small screens
- Status bar with current state
- Dependency detection with guidance for missing tools
- **Process tree cleanup** — cancelling kills child processes
- **Owned temp directory cleanup** on exit without touching another running instance
- **Atomic output safety** — validates staged media before replacing the chosen destination
- 67-test suite covering utilities, process safety, generated media, and quality diagnostics

### Web Editor

Try it in the browser: **[sysadmindoc.github.io/ClipForge](https://sysadmindoc.github.io/ClipForge/)**

- Timeline-based NLE with video, audio, and music tracks
- Import video, audio, and image files via drag & drop
- Clip splitting, trimming, moving, and snapping
- Transitions (cross dissolve, fade, wipe, zoom)
- Per-clip color correction and rotation
- Audio waveform visualization
- **ffmpeg.wasm 0.12** with multi-threaded core
- **Full undo/redo** (Ctrl+Z / Ctrl+Shift+Z, 50-deep history)
- **Multi-clip export** — all timeline clips concatenated via FFmpeg concat demuxer
- Export to MP4, WebM, GIF with quality/resolution options
- Project save/load (.clipforge format)
- Keyboard shortcuts: V (select), C (razor), S (split), Space (play/pause), J/K/L (transport)

## Requirements

- **Python 3.10+**
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

## Install & Run

```powershell
git clone https://github.com/SysAdminDoc/ClipForge.git
cd ClipForge
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
.\.venv\Scripts\python clipforge.py
```

On Linux or macOS, replace `.\.venv\Scripts\python` with `.venv/bin/python`.
ClipForge never changes the Python environment at runtime; install dependencies
explicitly before launch.

## Build and Verify

```powershell
python -m pip install -r requirements-dev.lock
python scripts/sync_version.py --check
python scripts/release_check.py
python scripts/release_check.py --build
```

Release builds are unsigned and produced locally. `clipforge/version.py` is the
version source of truth; use `python scripts/sync_version.py --set X.Y.Z` to
update desktop, web, README, and Windows executable metadata together.
The release check generates disposable FFmpeg fixtures for audio/video,
subtitles, chapters, rotation, VFR, odd filenames, and core edit operations;
`--build` also creates and launch-smokes the unsigned PyInstaller executable.

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
