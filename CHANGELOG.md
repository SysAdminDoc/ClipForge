# Changelog

All notable changes to ClipForge will be documented in this file.

## [Unreleased]

### Changed
- Centralized external FFmpeg, NVDEC, and Qt security decisions in a versioned runtime policy; capability probes and redacted diagnostics now report the actual runtime identity and policy state.
- Raised the PyQt6 dependency floor to the Qt 6.11.1 security baseline and made the browser runtime SBOM reject stale policy metadata.
- Desktop media workers now emit typed terminal outcomes with reason codes, cancellation/timeout state, output validation, and bounded log references while preserving existing panel signals.

## [v0.5.2] - 2026-07-29

### Added
- Headless Chromium behavior coverage for project security/migration/relinking, transitions, media failures, job cancellation, export preflight, focus, quota failure, and 900×700 layout.
- Target-bitrate conversion mode with two-pass availability limited to supported software encoder/container combinations.
- Semantic pre-commit output contracts covering duration tolerance, stream cardinality, container/codec policy, and required sidecars.
- One browser FFmpeg job coordinator for waveform, proxy, and export work, with unique virtual paths, shared progress/cancel state, terminal diagnostics, and guaranteed cleanup.
- Schema-versioned transactional state for settings, recents, and presets with typed validation, atomic writes, one last-known-good backup, bounded corrupt-data quarantine, and legacy JSON migration.
- Universal SHA-256 locks for runtime, development/release, and optional mpv environments, generated from explicit dependency inputs.
- Packaged-artifact release smoke that opens generated media and performs a validated tiny transcode before the GUI launch check.
- Pinned, self-hosted FFmpeg n5.1.10 browser runtime with artifact hashes, build provenance, dependency inventory, and bundled license texts.
- Strict browser CSP and a same-origin service-worker cache that preserves cross-origin isolation and supports offline restarts after installation.
- Optional experimental libmpv preview backend with play/pause, exact seek, frame-step, speed, volume, runtime capability detection, and a documented Qt Multimedia fallback.
- Streams panel quality comparison with duration/dimension preflight, sync offsets, VMAF/PSNR/SSIM status, and atomic JSON/CSV report export.
- Cancellable, per-metric-timeout quality worker with explicit complete, partial, unavailable, failed, timed-out, and cancelled states.
- Utility parser coverage for VMAF, PSNR, and SSIM FFmpeg output.
- Local release gate with generated audio/video, subtitle, chapter, rotation, VFR, odd-path, trim, crop, convert, audio-extract, and concat fixtures.
- Optional clean unsigned PyInstaller build and launch smoke in the same release gate.

### Changed
- Raised the supported Python floor to 3.11 and aligned desktop, web, README, and Windows executable metadata at v0.5.2.
- Unsigned release builds now use `sys.executable -m PyInstaller` from a fresh hash-enforced environment and retry cleanup of transient Windows executable locks.
- Browser runtime loading no longer depends on executable CDN content, and browser controls no longer require inline event handlers.
- Managed subprocesses now retain configurable fixed-size stdout/stderr tails, can spool full tagged logs to owned files, and apply backpressure while callbacks continue receiving complete output.
- Added a tracked Python manifest and exact runtime/development lock files.
- Removed first-launch dependency installation; source runs now require an explicit environment setup.
- Made the PyInstaller spec portable and synchronized executable, desktop, web, and README versions from `clipforge/version.py`.
- Centralized subprocess supervision with concurrent stdout/stderr draining, bounded cancellation/timeouts, and process-tree escalation.
- All desktop media exports now use validated staging files and atomic final replacement after explicit overwrite checks.
- FFprobe metadata now includes stream indexes/dispositions, rotation, time bases, color/audio layout, chapters, and actionable probe failures.
- Stream remux and audio tools now preserve real stream indexes, preflight container-copy compatibility, and expose source-stream/channel-layout choices.
- Jobs now retain bounded structured diagnostics with stable IDs, tool versions, commands, terminal state, and output-validation results.
- Console severity filters now match exact levels and both rendered/history buffers are capped; redacted JSON support export excludes media contents.
- Desktop trim ranges now provide keyboard-operable handles and synchronized numeric inputs, with a persistent live high-contrast toggle.
- Browser tabs, dialogs, progress, errors, icon controls, track states, and 900px layouts now expose complete responsive accessibility state.
- Browser projects now use a versioned schema with legacy migration, IndexedDB recovery, bounded validation, and explicit local-source relinking.
- Browser export now preflights every timeline state, blocks unsupported gaps/transitions/audio/effects, sanitizes downloads, checks FFmpeg exits, supports cancellation, and cleans run-scoped files.
- Desktop and browser editors now offer cancellable 720p preview proxies with source-metadata cache keys, atomic/recoverable generation, visible estimates, pruning, and explicit original/proxy switching while exports retain originals.
- Desktop filmstrips now decode all thumbnails in one cancellable FFmpeg pass instead of launching one process per frame.
- The AI panel now exposes pinned tool/model versions, paths, licenses, package checksums, storage estimates, and verified capability state, with resumable allowlisted Windows installers for Real-ESRGAN, SPAN, and RIFE.
- Upscale and interpolation now share a source-keyed lossless frame cache, preflight disk requirements, run managed tools from their model directory, and retain atomic failure semantics through every stage.

### Fixed
- Desktop player controls and all eight panels now fit 1280×860 in standard and high-contrast themes using wrapped rows and vertical scrolling, with stable accessible names for inputs, sliders, editors, lists, and progress state.
- Browser media, clips, transitions, and clip actions are keyboard-operable with semantic names, roving menu focus, Escape focus restoration, WCAG AA normal-text contrast, and verified 900×700 control reachability.
- Browser project replacement now confirms dirty changes, awaits active-job cancellation, revokes old URLs, and resets playback, history, clipboard, selection, preview, tools, and modal state atomically.
- Browser recovery now offers Recover and Discard, media metadata imports have actionable decode/timeout failures, and recovery quota failures surface a visible warning.
- The browser Music track remains reachable at the supported 900×700 viewport.
- Two-pass encoding now uses a unique registered workspace per job and removes only its own pass logs after a validated atomic second-pass output.
- Verified AI tool upgrades now activate through an atomic backup and restore the prior install if post-install verification fails.
- AI upscale/interpolation now map every audio stream from the original source and explicitly transcode it, preventing silent loss of Opus, PCM, multitrack, and no-audio inputs.
- Browser export is excluded while proxy/waveform work is active; cancellation reloads the terminated ffmpeg.wasm worker in place and waits for a reusable engine before accepting another job.
- Browser FFmpeg progress now ignores invalid out-of-range events instead of briefly displaying impossible percentages.
- Persistence recovery and write failures now surface actionable console/toast details; long notifications preserve their actionable beginning and expose the full text as a tooltip and accessible name.
- Worker diagnostics no longer duplicate unbounded FFmpeg stderr, and individual diagnostic messages are capped.
- Whisper captions now commit atomically from an owned staging directory; batch templates cannot escape their output directory and are frozen for active runs; FFmpeg filter paths and chapter metadata now preserve escaped metacharacters.
- Browser project imports now remap all external identifiers and render project names, media, and clips through DOM text/data APIs instead of executable HTML or inline handlers.
- CUDA/NVDEC decoding now fails closed on unreviewed or below-policy FFmpeg builds, while NVENC encoding remains available through software decoding.
- Temp cleanup now removes only workspaces owned by the running ClipForge instance.
- Smart Cut concat manifests safely escape absolute paths, including spaces and single quotes.
- Batch preflight now blocks duplicate outputs, low-disk starts, source/output collisions, and cancellation races.
- Player load/decode failures now remain visible in the player and console instead of failing silently.
- Browser startup no longer loops when cross-origin isolation is unavailable, FFmpeg startup is time-bounded, and inline controls remain callable.
- Browser projects no longer discard all usable source identity, and export can no longer silently omit unsupported timeline state.

## [v0.5.1] - 2026-06-15

### Fixed
- Web editor: FFmpeg now starts on GitHub Pages by loading the `@ffmpeg/ffmpeg` wrapper worker through a same-origin blob module instead of constructing a cross-origin CDN worker directly.

## [v0.5.0] - Unreleased

### Added (Round 2)
- Smart Cut trim mode: re-encodes only at cut boundaries, stream-copies the middle for near-lossless frame-accurate trims
- Trim panel now offers three modes: Lossless (fastest), Smart Cut (edges only), Full Re-encode (frame-accurate)
- Silence detection and auto-removal in Filters panel: detect silent segments with adjustable threshold/duration, remove them with one click
- Silence scanner shows count and total duration of detected segments

- RIFE frame interpolation upgraded from v4.6 to v4.25 default; model selector added (v4.25, v4.6, v4.22, v4)
- Batch disk-space pre-check now uses `estimate_output_size()` per file for convert/downscale operations
- SPAN upscaling engine added alongside Real-ESRGAN — ~7x faster for time-sensitive workflows; engine selector in AI Enhance panel with per-engine model lists
- Output overwrite confirmation dialog for Trim, Convert, and Filters panels (OS-independent)
- Whisper model selector now shows download sizes (e.g., "large (~3 GB)") to warn before large downloads

### Fixed (Round 3 — engineering audit)
- Smart Cut: head segment was computed but never used — now properly re-encodes the pre-keyframe section and includes it in the concat
- Smart Cut: removed dead `next_kf_after_start` variable
- Trim mode radio: unchecking the active mode no longer leaves all modes unchecked
- Silence detection: FFmpeg log output now connected to console (was running silently)
- Silence removal: added overwrite confirmation dialog (was missing)
- Convert panel: CRF spinner range now adjusts dynamically (0-63 for AV1, 0-51 for H.264/H.265)
- Web editor: blob URL memory leaks fixed — export and save now revoke blob URLs after use
- Removed dead `short` variable in HW encoder status display

### Fixed (Round 2)
- Whisper caption worker now uses `parse_progress=False` to skip FFmpeg-specific progress/error parsing for non-FFmpeg commands
- "Run Custom" button re-enabled after custom command completes (was stuck disabled)
- Whisper output file correctly found and renamed to user-chosen path (Whisper generates `{input_stem}.srt`, not user-specified filename)
- Filter preview runs in background worker thread instead of blocking UI
- Non-FFmpeg process failures now show last 3 stderr lines as error hint instead of "FFmpeg exited with code X"

### Changed
- **Split 4423-line monolith into `clipforge/` package** — 18 modules across constants, theme, settings, tools, workers, widgets, 8 panel files, and app. Each module under 575 lines. `python -m clipforge` or `python clipforge.py` both work.
- Extracted pure utility functions into `clipforge_utils.py` — no PyQt6 dependency, clean imports
- Tests now import from `clipforge_utils` instead of duplicating function implementations

### Added
- SVT-AV1 encoding with CRF quality hints (0-63 range), preset mapping, and two new built-in presets (Web AV1, AV1 High Quality)
- libaom-av1 now gets proper `-b:v 0` and `-cpu-used` settings
- FFmpeg error pattern matching: common failures (codec not found, disk full, permission denied) show friendly messages
- Process tree cleanup: cancelling a job now kills child processes (e.g., realesrgan) via taskkill /T (Windows) or killpg (Unix)
- Web editor: upgraded ffmpeg.wasm from 0.10.1 to 0.12.x (new API, multi-thread core when SharedArrayBuffer available)
- Web editor: full undo/redo system (Ctrl+Z / Ctrl+Shift+Z) with 50-deep history for all clip operations
- Web editor: multi-clip export — all video clips on timeline are now included in export via concat demuxer
- FFmpeg command preview is now editable with "Run Custom" button to execute modified commands; "Reset" button regenerates from settings
- Before/after filter preview: side-by-side original vs filtered frame comparison in Filters panel
- Accessibility: accessible names on nav buttons, player controls, file open button; high-contrast theme option via `high_contrast` setting
- Auto-caption generation: Whisper integration in Filters panel with model/language selection, generates .srt files and auto-loads them for burn-in
- Test suite: 42 tests covering utility functions, format helpers, atempo chaining, eq filter building, path validation, preset name sanitization
- ffprobe metadata cache: re-probing the same file (by path+size+mtime) returns cached results instantly
- Pre-flight disk-space check before batch processing: warns when free space is below estimated output size * 1.2
- Loudness target presets: YouTube/Streaming (-14 LUFS), Podcast (-16), Broadcast (-23), Spotify (-14), Apple Music (-16) selectable in Filters panel

### Fixed
- Frame stepping now uses actual video fps instead of hardcoded 30fps
- Filters panel: combined eq parameters into single FFmpeg filter (was generating invalid stacked eq= calls)
- Stabilization analysis no longer blocks the UI thread (runs in background worker)
- Snapshot export captures at current player position instead of always frame 0
- Audio atempo chaining now handles speeds above 2.0x correctly (e.g., 4x = atempo=2.0,atempo=2.0)
- Bootstrap no longer uses `--break-system-packages`; uses `--user` on system Python, skips on venv
- Temp directories from upscale/interpolation are tracked and cleaned on exit or crash
- Preset names are sanitized for filesystem safety before saving
- File paths are validated before passing to FFmpeg
- Web editor: media filenames and clip names are HTML-escaped to prevent XSS
- All bare `except Exception:` blocks replaced with specific exception types (OSError, json.JSONDecodeError, etc.)

## [v0.4.0] - %Y->- (HEAD -> main, origin/main, origin/HEAD)

- Added: Add files via upload

## Roadmap archive — 2026-08-10 — ROADMAP.md

<details>
<summary>Original roadmap snapshot</summary>

```markdown
# ClipForge Roadmap

All-in-one PyQt6 FFmpeg front-end with AI upscale (Real-ESRGAN) and frame interpolation (RIFE). This roadmap tracks what's next after v0.4.0.

## Planned Features

### Core / Pipeline
- Job queue: reorder, pause, resume, per-job priority, retry-failed; persist queue to disk so crashes don't lose work. Research constraint (2026-07-25): define queued/running/cancelling/succeeded/failed/interrupted states, atomic journal writes, output validation, and race-free cancellation before adding parallel workers. Research note (2026-07-29): snapshot batch inputs/configuration at start, disable mutations while running, and expose cancellation consistently across panels (`clipforge/panels/batch.py:244-330`).
- Add project/session files (`.cfproj`) that snapshot inputs, in/out points, filters, and preset so a complex trim can be reopened. Research constraint (2026-07-25): use a versioned schema with external media references, relinking, autosave/backups, and an explicit unsupported-feature policy.

### Filters & Effects
- Chained filter stack with drag-reorder, live preview of the `-filter_complex` graph
- Motion-tracked blur/redaction region for faces/plates
- Audio waveform + silence-cut filter (`silencedetect` + `select`) for fast talking-head edits. Research constraint (2026-07-25): expose detected ranges as reviewable, editable markers before destructive removal; the current detector only parses FFmpeg output into segments.

### UI/UX
- Timeline with frame thumbnails across the full duration (not just the filmstrip row), scrub preview on hover
- Detachable preview pane for multi-monitor setups

### Integrations
- SubRip OCR pipeline for hardsubs (Tesseract) → exportable `.srt`
- Optional yt-dlp integration: paste a URL to pull source video into the workspace

### Performance
- Scene-change detection for smarter two-pass keyframe placement. Research constraint (2026-07-25): retain scene markers for user review instead of silently changing cut placement.
- Parallel batch worker pool with a configurable thread cap tied to encoder concurrency. Research constraint (2026-07-25): land durable queue state, cancellation, output checks, and measured encoder capability limits first.

### Packaging
- Portable `.zip` bundle with `ffmpeg`, `realesrgan-ncnn-vulkan`, `rife-ncnn-vulkan` vendored. Research constraint (2026-07-25): build from a clean local checkout because hosted cross-platform GitHub Actions are blocked; include version, checksum, license, and supported-platform metadata. Research note (2026-07-29): use a fully hash-locked environment, invoke PyInstaller through the active interpreter, and smoke-test a real media operation.
- Auto-update check against GitHub releases API with opt-out setting. Research constraint (2026-07-25): publish a single version source and signed-by-policy manifest first; no update client should consume the currently stale deployment/version signals.

## Competitive Research

- **HandBrake** — Best-in-class preset system and queue UX; ClipForge should steal the "add to queue" button placement and the side-by-side preset tree.
- **Shutter Encoder** — Covers a huge surface area (subs, DVD, broadcast) with a single UI; useful reference for tool grouping and sidebar taxonomy.
- **LosslessCut** — The gold standard for frame-accurate lossless trims with explicit cutpoints and markers; mirror its visible segment workflow. Keyboard shortcuts are blocked by repository policy; use visible transport/marker controls.
- **FFmpeg Batch AV Converter** — Text-driven power tool; the "FFmpeg command preview" idea is validated there and worth making editable, not just read-only.

## Nice-to-Haves

- Optional GPU upscaler swap (waifu2x-ncnn-vulkan, anime4k) alongside Real-ESRGAN
- Built-in "Discord-safe" auto-bitrate solver that hits an exact target file size in one pass via VBR bitrate math
- Drag-and-drop `.srt` onto the player to preview burned subs before export
- Color grade panel with 1D/3D LUT stacking and side-by-side before/after split
- Project watermark presets (logo PNG with position, opacity, fade-in/out)
- Mini web UI mode for headless render boxes (Flask + the same FFmpeg core)

## Open-Source Research (Round 2)

### Related OSS Projects
- **Zulko/moviepy** — https://github.com/Zulko/moviepy — MIT Python video editor backed by ffmpeg; reference for concat/composite/timing primitives.
- **TNTwise/REAL-Video-Enhancer** — https://github.com/TNTwise/REAL-Video-Enhancer — Historical reference for upscale / interpolation / decompress / denoise UX. Research update (2026-07-29): upstream was archived on 2026-07-13; do not make it a runtime, update, or distribution dependency.
- **valkjsaaa/ffmpeg-smart-trim** — https://github.com/valkjsaaa/ffmpeg-smart-trim — Precise trim with minimum re-encoding at segment boundaries only.
- **addyosmani/video-compress** — https://github.com/addyosmani/video-compress — React + ffmpeg.wasm client-side compression; reference for a future browser companion.
- **dinoosauro/ffmpeg-web** — https://github.com/dinoosauro/ffmpeg-web — Web + Electron UI; two-engine strategy (wasm vs native).
- **EncodeGUI / encode-gooey** — reference for AI-transcoder UX with upscaling features built in.
- **ffmpeg-gui topic** — https://github.com/topics/ffmpeg-gui — broader catalog of GUI wrappers.
- **topic: video-crop** — https://github.com/topics/video-crop — includes GUIs that emit 22 presets across 16:9 / 4:3 / 1:1 aspect groups.

### Features to Borrow
- **Upscale + interpolation on the same job graph** (REAL-Video-Enhancer) — a source video runs through `decompress → RIFE (2x/4x) → Real-ESRGAN (2x/4x) → encode` as a single chained job with shared frame cache, not three separate passes that re-extract frames.
- **NCNN/Vulkan backend path** (REAL-Video-Enhancer) — portable binaries for non-CUDA users (AMD/Intel Arc/Apple); avoids the CUDA gating that limits ClipForge's AI features as of 2026-07-25.
- **Decompress / denoise model pre-pass** (REAL-Video-Enhancer) — a `hqdn3d` + `scxvid`-style denoise *before* upscale dramatically improves final quality; expose as a toggle on the Upscale card.
- **Segment-precise trim** (ffmpeg-smart-trim) — re-encode only the 1–2 sec around the cut points and stream-copy the middle; gives both "frame precise" and "fast" in one mode.
- **22-preset aspect-ratio crop grid** (video-crop topic) — single pane with 22 crop presets grouped by aspect; cleaner than the current 5-preset row.
- **Live FFmpeg command preview + copyable** (already in ClipForge) — extend by adding a "Generate bash script" button that emits the whole batch as a self-contained `.sh`/`.ps1`, so power users can rerun on a server.
- **Two-engine strategy** (dinoosauro/ffmpeg-web) — same UI, WASM fallback, for the future web-companion deliverable.
- **MoviePy-style composition timeline** (Zulko/moviepy) — expose a mini-timeline where Trim + Fade + Overlay can be stacked as a pipeline, not just one-op-at-a-time; important for the "combine filters" use case.
- **Real-ESRGAN-Video and RIFE-v4.22 updates** — upstream RIFE has moved to 4.22/4.25 with better motion handling; pin + document the active version and an upgrade lane.

### Patterns & Architectures Worth Studying
- **Frame-cache directory shared across AI passes** (REAL-Video-Enhancer) — `workdir/{jobid}/frames/` reused by upscale then interpolation, so the extract step doesn't happen twice.
- **Executable auto-detection** (REAL-Video-Enhancer, EncodeGUI) — at startup, probe for `ffmpeg`/`ffprobe`/`realesrgan-ncnn-vulkan`/`rife-ncnn-vulkan` on PATH; if missing, auto-download platform-specific binaries from BtbN/FFmpeg-Builds + the NCNN upstream releases.
- **Qt worker with queue + cancel tokens** — ClipForge already uses PyQt threads; borrowing the MoviePy subprocess-supervisor pattern (stdout tailing + periodic progress parse) simplifies cancel semantics.
- **Resumable frame pipeline** (REAL-Video-Enhancer) — cache per-frame outputs with content-hash keys so interrupted upscale jobs resume where they stopped; matches ClipForge's existing "resume interrupted jobs" ethos.
- **JSON-schema'd preset format shared with CLI** — define the schema and headless contract first; as of 2026-07-25 no `clipforge-cli` entry point or manifest-backed schema is present in the repository.

## Research-Driven Additions

### P1 — Trustworthy media workflows

- [ ] P1 — Keep every desktop operation responsive and cancellable
  Why: Probing/frame extraction/hardware detection can block the GUI for 10–15 seconds, most panels expose no Cancel, and shutdown does not verify workers/processes actually stopped.
  Evidence: `clipforge/tools.py:138-211`, `clipforge/tools.py:279-500`, `clipforge/widgets.py:976`, `clipforge/app.py:479-505`
  Touches: `clipforge/tools.py`, `clipforge/app.py`, `clipforge/widgets.py`, all long-running panels, worker/shutdown tests
  Acceptance: Media inspection and capability checks run off the GUI thread with timeout/status; every long operation exposes a race-free Cancel; close waits for confirmed termination or reports forced cleanup; responsiveness and stubborn-process tests pass.
  Complexity: L

- [ ] P1 — Unify browser timeline preview and export semantics
  Why: Preview plays one source without clip in-points, boundaries, transforms, or track state, while export consumes a different subset of project state.
  Evidence: `editor.js:1112`, `editor.js:1413-1516`, `editor.js:1553-1830`; Mediabunny/WebCodecs composition practice
  Touches: browser timeline model, preview compositor, export planner, `editor.js`, tests
  Acceptance: One resolved timeline function maps global time to active clips/source time/transforms/mute/solo and is consumed by both preview and export; unsupported state is blocked explicitly; golden projects prove preview/export frame and audio-state parity.
  Complexity: XL

### P2 — Controlled expansion

- [ ] P2 — Add quota-aware cache identity and lifecycle controls
  Why: Browser proxies can collide on name/size/mtime and prune by count, while desktop frame caches lack user-visible usage, integrity depth, limits, and purge.
  Evidence: `editor.js:551-651`, `editor.js:2085-2145`, `clipforge/ai_tools.py:299-390`; MDN Storage API/OPFS
  Touches: browser storage/proxy code, `clipforge/ai_tools.py`, settings UI, diagnostics, tests
  Acceptance: Bounded sampled-content fingerprints prevent false relinks without hashing entire large files; browser storage preflights quota and prunes byte-based LRU; desktop/browser surfaces show usage/limits and explicit purge; corrupt/incomplete entries are detected and recoverable.
  Complexity: M

- [ ] P2 — Export browser diagnostics and strengthen redaction
  Why: Browser failures are limited to console/toasts, while desktop redaction does not cover URL credentials/query tokens, sensitive options, or private media metadata.
  Evidence: `clipforge/diagnostics.py:33-51`, `editor.js` IndexedDB/runtime error paths
  Touches: `clipforge/diagnostics.py`, browser diagnostics module/UI, tests
  Acceptance: Both clients export bounded version/capability/job/storage/error reports; default redaction removes local paths, URL credentials/tokens, configured secret options, and identified private metadata; fixtures prove secrets do not appear.
  Complexity: M

- [ ] P2 — Probe real FFmpeg hardware capability
  Why: After the P1 asynchronous inspection boundary exists, presence, advertised encoder names, and wrapper versions still do not prove device usability, required filter/codec combinations, or a supported native libmpv.
  Evidence: `clipforge/tools.py:68-160`, `clipforge/mpv_backend.py:106`; HandBrake capability-aware presets; mpv GHSA-546v-22c3-7927
  Touches: `clipforge/tools.py`, `clipforge/panels/convert.py`, `clipforge/mpv_backend.py`, diagnostics, capability cache/tests
  Acceptance: Tiny probes reuse the P1 worker boundary to verify each exposed hardware path; results are cached per binary/device, visible in diagnostics, and disable unsupported choices with a reason; native libmpv version is reported and versions below 0.41.0 warn or fall back.
  Complexity: M

- [ ] P2 — Split the browser editor into explicit modules
  Why: Project schema, storage, jobs, preview, export, actions, and DOM rendering share mutable globals in a 2,328-line file, making security and state invariants hard to test.
  Evidence: `editor.js`; seven ffmpeg.wasm loader follow-up commits on 2026-06-15
  Touches: `editor.js`, new JavaScript modules, `index.html`, browser tests
  Acceptance: Separate project/store, media/storage, jobs, timeline/actions, preview, export, and view modules expose narrow APIs; no behavior regresses; direct unit tests cover reducers/schema/planning without a DOM.
  Complexity: L

- [ ] P2 — Remove or complete misleading browser controls
  Why: The visible Edit menu is empty and Slip/Hand tools are selectable but have no behavior.
  Evidence: `editor.js:857`, `editor.js:2259`, `index.html:1168`
  Touches: `editor.js`, `index.html`, browser tests
  Acceptance: Each visible tool/menu action either performs its labeled operation with undo/keyboard/pointer coverage or is removed/disabled with explanatory copy; no selectable no-op remains.
  Complexity: M

### P3 — Future

- [ ] P3 — Evaluate Mediabunny as ffmpeg.wasm replacement for web editor
  Why: Mediabunny is an active pure-TypeScript, progressive-I/O WebCodecs toolkit that can avoid ffmpeg.wasm whole-file memory costs for supported codecs
  Evidence: https://mediabunny.dev/; https://github.com/Vanilagy/mediabunny; research refresh 2026-07-29
  Touches: `editor.js`, `index.html`
  Acceptance: Spike browser export using Mediabunny + WebCodecs; benchmark peak memory, time, output parity, codec coverage, lossless-trim limitations, and Safari/Firefox/Chromium fallback against current FFmpeg.wasm
  Complexity: L

- [ ] P3 — Add i18n framework for future localization
  Why: ClipForge has no internationalization; all strings are hardcoded in English
  Evidence: OpenShot and Shotcut release histories; Qt6 translation tooling
  Touches: User-visible strings in `clipforge/`, `index.html`, and `editor.js`
  Acceptance: All user-visible strings use validated placeholders and translatable resources; English remains default; one proof language passes long-string rendered-layout tests
  Complexity: L
```

</details>
