# Changelog

All notable changes to this project are documented here.

The project uses Semantic Versioning. Stable application releases are tagged `vX.Y.Z`.

## [2.4.3] - 2026-08-18

### Fixed

- Corrected the checksum pin for the official Silero VAD v6.2.1 `silero_vad.onnx` asset to `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
- Added backward compatibility for already-deployed v2.4.2 Portainer stacks that still pass the previous incorrect default checksum; the compatibility applies only to the exact official v6.2.1 model URL and does not weaken verification for custom URLs or custom pins.
- Prevented the resulting checksum mismatch from causing an endless container restart loop after an otherwise successful first-start dependency/HyperFrames bootstrap.

## [2.4.2] - 2026-08-18

### Fixed

- Escaped inline bootstrap shell variables as `$$...` so Docker Compose passes them through to `/bin/sh` instead of interpolating them at Stack-render time.
- Fixed repeated startup failures such as `mkdir: cannot create directory '': No such file or directory` when Portainer rendered unset shell-local variables inside the `command:` block.
- Preserved shell PID expansion by encoding `$$` as `$$$$` in the Compose source.
- Added a regression contract that rejects unescaped dollar signs inside the inline bootstrap command.

## [2.4.1] - 2026-08-18

### Added

- `comfy_upload_cached_image` directly uploads an existing MCP cache image to ComfyUI without routing the file bytes through the AI/client context.
- The adapter preserves the optional-backend contract: unreachable ComfyUI returns a structured `available=false` result instead of an MCP tool error.

## [2.4.0] - 2026-08-18

### Added

- Optional authenticated host-side Blender bridge integration. Blender remains disabled by default and is never required for MCP startup.
- `external_backends_status` and `blender_info` availability/introspection tools.
- `blender_execute_python` for general `bpy` automation with MCP-cache inputs and declared outputs copied back into the cache.
- Convenience Blender tools for still rendering, H.264 animation rendering, and GLB export from cached `.blend` files.
- Host bridge reference implementation in `scripts/blender_bridge.py` plus a hardened systemd unit example for running it as a dedicated low-privilege OS account.
- Generic MCP client-to-server file import tools for text, one-shot base64 binary files, and bounded chunked binary uploads with optional SHA-256 validation.
- Generic server-to-client chunked base64 reads plus cache metadata inspection, complementing the existing authenticated `/files/{file_id}` download route.

### Changed

- Network-dependent ComfyUI tools are wrapped as an optional external backend: when ComfyUI is absent/unreachable they return a structured `available=false` result for the model instead of producing an MCP tool error.
- `inventory_summary` now reports external backend availability separately from local/internal capabilities.
- ComfyUI filesystem bind mounts use generic empty-directory fallbacks so the Portainer Stack can start even before ComfyUI is installed or its paths are configured.
- Files entering through the generic transfer layer become normal MCP cache entries and can flow to/from ComfyUI, Blender, FFmpeg, HyperFrames, subtitles, timelines, and audio tools through the same `file_id` contract.

## [2.3.1] - 2026-08-18

### Added

- Explicit `subtitle_retime` tool for proportional subtitle timing changes plus millisecond shifts while preserving subtitle structure/styles.
- Explicit `transcribe_words` tool based on whisper.cpp full JSON token timestamps. Whitespace languages are grouped into word-like spans; no-space scripts safely fall back to timestamped token units.
- Regression tests for whisper.cpp token-to-word grouping and no-space-script fallback behavior.

## [2.3.0] - 2026-08-18

### Added

- Advanced FFmpeg analysis and finishing tools: silence/black/freeze detection, loudness analysis and normalization, crop/interlace detection, contact sheets, SSIM/PSNR comparison, safe crop, finite looping, reverse, speed ramps, keyframe extraction, and storyboards.
- Lightweight OpenCV frame utilities for frame similarity, motion scoring, duplicate-frame detection, and best-frame selection.
- PySceneDetect-based scene detection, scene splitting, and per-scene thumbnails.
- pysubs2 subtitle creation, retiming, conversion, ASS styling, and FFmpeg subtitle burn-in.
- Persistent OpenTimelineIO timelines with tracks, clips, transitions, markers, inspection, reordering, and export.
- aubio beat, tempo, onset, and pitch analysis.
- RNNoise local speech denoising using a checksum-pinned upstream source/model build stored persistently under `/data/tooling/rnnoise`.
- Silero VAD ONNX speech-segment detection with a checksum-pinned small model stored under `/data/models`.
- whisper.cpp local transcription and subtitle generation using a persistent checksum-pinned quantized model.
- Optional Piper TTS integration, disabled by default, with persistent user-supplied voice assets.
- `advanced_capabilities` runtime introspection and a side-effect-free `video_mcp.entrypoint` that registers all advanced tools.
- Persistent media-tool preparation script; models/tooling survive container recreation in the existing data volume.

### Changed

- Runtime system dependencies now include aubio plus the small build-tool set required to prepare RNNoise and whisper.cpp locally.
- Extended the initial Docker healthcheck grace period to accommodate first-start local builds/downloads on slower hosts.
- ComfyUI remains the model-generation backend and can provide video, image, or audio outputs to the same media cache; advanced utilities are independent post-processing/analysis primitives.

## [2.2.2] - 2026-08-18

### Fixed

- Removed filesystem writes from `import video_mcp.server`, allowing CI and tooling to import the module without requiring a writable `/data` root.
- Added configurable `VIDEO_MCP_DATA_ROOT` with `/data` as the production default.
- Deferred runtime-directory creation and Cloudflare runtime configuration validation to application startup.
- Added a regression test that verifies importing the MCP server does not create the configured runtime data directory.

## [2.2.1] - 2026-08-18

### Changed

- Switched the project license from MIT to Apache License 2.0.
- Added the required `NOTICE` attribution for redistributed copies and derivative works.
- Updated README licensing and attribution documentation.

## [2.2.0] - 2026-08-18

### Added

- Thin MCP gateway for a ComfyUI instance reachable from the same Docker host.
- ComfyUI node and model discovery through both the HTTP API and read-only filesystem scans.
- Dynamic ComfyUI workflow submission, queue/history inspection, output retrieval, upload, and interrupt controls.
- Persistent media cache shared by ComfyUI output handling, HyperFrames, and FFmpeg operations.
- Structured FFmpeg tools for probing, frame extraction, transcoding, concatenation, audio muxing, and video overlays.
- Local HyperFrames rendering with persistent projects and browser cache, including controlled text-file authoring, media import, lint/check, and render operations.
- Optional Cloudflare Tunnel sidecar and Cloudflare Access JWT validation suitable for Managed OAuth deployments.
- Persistent Python virtual environment rebuilt only when `requirements.txt` changes.
- Single-YAML Portainer bootstrap with version pinning, stable-release `latest` resolution, update checks, force refresh, atomic staging, and last-known-good fallback.
- CI validation and automatic immutable `vX.Y.Z` GitHub Release creation after successful tests on `main`.
