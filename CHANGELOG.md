# Changelog

All notable changes to this project are documented here.

The project uses Semantic Versioning. Stable application releases are tagged `vX.Y.Z`.

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
