# Changelog

All notable changes to this project are documented here.

The project uses Semantic Versioning. Stable application releases are tagged `vX.Y.Z`.

## [3.0.1] - 2026-08-19

### Fixed

- Fixed v3.0.0 startup on the production `node:22-bookworm-slim` runtime, whose Debian Python is 3.11: native ChatGPT file parameters now use `typing_extensions.TypedDict`/`NotRequired`, as required by Pydantic on Python versions below 3.12.
- Declared `typing-extensions` as an explicit runtime dependency instead of relying on it only as a transitive MCP/Pydantic dependency.
- CI now runs the complete import/test/safety/YAML/Compose suite on both Python 3.11 and 3.12 so the Portainer runtime version cannot diverge silently from the CI interpreter again.

## [3.0.0] - 2026-08-19

### Added

- Native ChatGPT attachment ingress through `save_uploaded_file(file)` and `save_uploaded_files(files)`, both declaring `_meta["openai/fileParams"]` so compatible ChatGPT clients can bind attached files directly to MCP tool parameters.
- `src/video_mcp/chatgpt_upload.py`, adapted from the proven Postmaster v9.2 native-file pattern but streamed directly to the Video Gen cache for larger media rather than materializing the entire file in memory.
- HTTPS/public-address validation, redirect re-validation, streaming `MAX_UPLOAD_MB` enforcement, SHA-256 calculation, temporary `.part` staging and bounded timeout/batch configuration for native ChatGPT file downloads.
- `docs/CHATGPT_FILE_UPLOAD.md` documenting the autonomous ChatGPT attachment path, security model and routing rules.

### Changed

- ChatGPT attachments now follow `attachment -> openai/fileParams -> temporary authorized HTTPS download -> Video Gen cache -> file_id`; the model no longer needs to transport binary bytes through tool JSON.
- `file_transfer_guide`, server instructions and `build_status` now prefer native ChatGPT file parameters, keep `import_remote_file` for generic retrievable HTTPS sources, and treat WebGUI upload as a manual recovery/admin path.
- The generic `httpx`/`httpcore` INFO request loggers are suppressed because temporary authorized file URLs and other signed URLs can contain bearer-like query parameters; sanitized application/audit logging remains available.
- `cache_text_file` remains available for model-authored UTF-8 text, while `read_cached_file_chunk_base64` remains outbound compatibility/debug only.

### Removed

- Breaking change: removed model-mediated binary ingress tools `cache_file_base64`, `file_upload_begin`, `file_upload_status`, `file_upload_chunk_auto`, `file_upload_chunk`, `file_upload_finish` and `file_upload_abort`.
- Removed the obsolete `docs/PROGRAMMATIC_UPLOAD.md` guide and PTC upload guidance. Programmatic Tool Calling is no longer needed for the normal ChatGPT attachment path.

### Security

- Native ChatGPT downloads accept only HTTPS on port 443, reject URL credentials and non-public DNS results, revalidate every redirect, enforce byte limits both from `Content-Length` and while streaming, and never intentionally persist full temporary download URLs or query strings.
- Partial native downloads are deleted on failure and completed files are promoted to the canonical cache only after successful bounded streaming.

## [2.8.3] - 2026-08-19

### Added

- `file_upload_chunk_auto(upload_id, data_base64, expected_decoded_bytes=0)` as the preferred chunk uploader for new clients: the server owns the offset and validates each complete decoded chunk before appending it.
- `file_upload_status(upload_id)` for authoritative server-side progress/recovery during direct or programmatic upload loops.
- PTC-friendly upload contract in `file_upload_begin`, `file_transfer_guide`, server instructions and `build_status`, including the bounded `begin -> chunk_auto loop -> finish` stage.
- `docs/PROGRAMMATIC_UPLOAD.md` describing Programmatic Tool Calling requirements, limitations, result fields and a deterministic client-side orchestration contract.

### Changed

- Chunk validation failures in `file_upload_chunk_auto` are atomic: invalid base64, decoded-size mismatches, size-limit violations and expected-size overruns return `accepted=false` with `file_unchanged=true`; rejected chunks do not advance progress.
- The legacy explicit-offset `file_upload_chunk` remains available for backward compatibility, while new/Programmatic Tool Calling clients are directed to `file_upload_chunk_auto`.
- Successful one-shot/chunked imports now explicitly tell clients to reuse the returned canonical `file_id` and not re-encode/re-upload the same unchanged artifact.
- Server guidance now states that Programmatic Tool Calling is client/API-controlled: the MCP can expose a PTC-friendly deterministic contract but cannot activate PTC or make client-local attachment bytes available by itself.

## [2.8.2] - 2026-08-19

### Added

- Activity-tab **Download TXT** export for all retained events matching the currently selected tool/method, source and status filters.
- Read-only `/api/audit.txt` streaming endpoint with attachment filenames, UTC timestamps, event ids, action/method, status, duration, sanitized arguments/result summaries and error text.

### Changed

- The Activity search box now correctly matches either tool name or method instead of only the method field.
- Audit TXT export uses stable newest-first keyset iteration so new events arriving during a download do not shift offset pagination.
- TXT exports read only the already-sanitized audit store and preserve the existing secret, signed-URL and binary/base64 redaction guarantees.

## [2.8.1] - 2026-08-19

### Changed

- Strengthened `cache_file_base64`, `file_upload_begin`, `file_upload_chunk`, `file_upload_finish` and chunked-read tool descriptions so AI clients choose one-shot/reference transfer for small files instead of creating many tiny MCP round trips.
- `file_upload_begin` now returns `max_chunk_decoded_bytes`, `recommended_next_chunk_bytes` and explicit transfer guidance. `file_upload_chunk` returns remaining bytes, a recommended next chunk size and a concrete next action.
- `file_transfer_guide` now includes a client-to-cache decision tree and explicit chunk-efficiency rules: prefer `import_remote_file`, then one-shot `cache_file_base64` when practical, and use chunked upload only as a compatibility fallback with large chunks.
- Server instructions now explicitly prohibit intentionally splitting small KB-sized files into many chunks and document the 4 MiB decoded per-call chunk ceiling plus a 1 MiB default target when client limits are unknown.

## [2.8.0] - 2026-08-18

### Added

- Lightweight same-process WebGUI at `/` with Cache and Activity tabs; no separate frontend service or build pipeline is required.
- Cache browser with previews where supported, search, direct downloads, streamed browser uploads, pin/unpin, explicit deletion, size/retention status and copyable `file_id` values.
- Persistent bounded SQLite activity audit under `/data/audit/events.sqlite3`, covering inbound MCP messages and WebGUI mutations with tool/method, status, duration and sanitized argument/result summaries.
- `comfy_upload_cached_media(file_id)` as the generic server-side cache -> ComfyUI input staging tool for images, audio, video and other files. It returns `workflow_input_value`; images additionally return `workflow_load_image_value` for standard `LoadImage`.
- Runtime replacement of `file_transfer_guide` so audio/video routing reflects generic staging while preserving the rule that custom loader semantics must be introspected.
- Stronger MCP server identity metadata and instructions so clients are reminded that MCP Video Gen is available for media/ComfyUI/FFmpeg/HyperFrames/Blender/audio tasks.

### Changed

- `comfy_upload_cached_image` remains as a backward-compatible image-only alias and now directs non-image callers to `comfy_upload_cached_media`.
- Cached media staging uses ComfyUI's current `/upload/image` endpoint only as a transport-level input-directory writer; audio/video workflow wiring is never guessed and still requires `list_loaded_nodes` / `get_node_definition` when node semantics are uncertain.
- `build_status` now reports generic ComfyUI media staging, WebGUI/audit availability and server identity metadata.
- WebGUI upload streams bytes directly to the persistent MCP cache and enforces `MAX_UPLOAD_MB`; no browser upload is converted to base64.

### Security

- WebGUI mutating routes require a same-origin custom request header in addition to the deployment's existing HTTP authentication boundary, preventing ordinary cross-site form submissions from mutating cache state.
- Audit persistence redacts secret-like keys, strips HTTP(S) query strings such as signed URL tokens, bounds large values and does not intentionally persist binary/base64 payloads.
- Pinned cache files cannot be deleted from the WebGUI without an explicit forced confirmation path.

### Configuration

- `WEBGUI_ENABLED=true` by default; set `false` to omit the `/` dashboard and `/api/*` dashboard routes.
- `AUDIT_LOG_ENABLED=true` by default.
- `AUDIT_RETENTION_DAYS=30` controls age-based audit retention; `0` disables the age rule.
- `AUDIT_MAX_ROWS=20000` bounds the persisted audit history.

## [2.7.1] - 2026-08-18

### Fixed

- Removed the hard-coded negative ElevenLabs speech-to-speech guidance introduced in v2.7.0. Third-party capabilities can be exposed dynamically by installed ComfyUI custom nodes even when MCP Video Gen has no dedicated tool for that provider.
- `advanced_capabilities` and `build_status` no longer report `elevenlabs_speech_to_speech=false`, which could incorrectly override capabilities available through ComfyUI.
- Server instructions now tell clients to inspect loaded ComfyUI nodes and node definitions before deciding whether an external integration is available, and to account for provider-specific API keys or credentials required by those nodes.

## [2.7.0] - 2026-08-18

### Added

- Opt-in persistent media-cache retention with age and/or maximum-size policies.
- `cache_status` to inspect cache size, file counts, pinned items, active retention policy, and the number/size of files that would currently be eligible for deletion.
- `cache_cleanup(dry_run=true)` for safe preview-first cleanup using the configured policy.
- `cache_pin(file_id, note)` and `cache_unpin(file_id)` for persistent protection of important cached artifacts; pinned files are never selected by retention cleanup.
- Server-level MCP instructions covering canonical file routing, cross-MCP isolation, ComfyUI `LoadImage` rules, native/reference-first transfer, cache retention, capability introspection, and the absence of ElevenLabs speech-to-speech unless a dedicated tool exists.

### Changed

- Automatic cleanup remains fully backward compatible and disabled unless `CACHE_CLEANUP_ENABLED=true` is explicitly provided. When retention variables are absent, cached files continue to persist indefinitely as before.
- `build_status` now reports retention configuration, server-instruction presence, retention-tool availability, and an explicit `elevenlabs_speech_to_speech=false` capability flag.
- `advanced_capabilities` explicitly reports `elevenlabs_speech_to_speech=false` so clients do not infer an unimplemented external integration.
- When automatic cleanup is enabled, the MCP performs an initial maintenance pass after startup and repeats it at the configured interval. Cleanup failures never terminate the MCP process.

### Retention configuration

- `CACHE_CLEANUP_ENABLED=false` by default.
- `CACHE_RETENTION_DAYS=0` disables age-based deletion.
- `CACHE_MAX_SIZE_GB=0` disables size-based deletion.
- `CACHE_CLEANUP_INTERVAL_HOURS=24` controls the automatic maintenance interval when cleanup is enabled.

## [2.6.1] - 2026-08-18

### Added

- `build_status` MCP tool for verifying the exact deployed application version/source ref that the connected MCP client is talking to.
- Safe runtime/build diagnostics including Python and MCP SDK versions, registered tool count, enabled feature flags, transfer/runtime limits, mount presence, and selected non-secret configuration booleans.
- `VIDEO_MCP_SOURCE_REF` runtime export from the immutable bootstrap `.mcp-source-ready` marker so `build_status` can distinguish the running source ref from the application version.
- Regression tests ensuring `build_status` counts the fully registered tool set and never exposes configured secrets such as Blender bridge tokens or Cloudflare audience values.

### Security

- `build_status` intentionally reports only safe configuration state. It never returns tokens, credentials, Cloudflare audience values, signed URLs, private file contents, or secret environment values.

## [2.6.0] - 2026-08-18

### Added

- `import_remote_file` for streaming a server-retrievable HTTPS file reference directly into the persistent MCP cache without moving the binary payload through tool JSON/base64.
- Remote-ingress security checks for HTTPS-only URLs, embedded-credential rejection, public/global DNS resolution, redirect re-validation, byte limits, partial-file cleanup, and optional expected size/SHA-256 validation.
- Optional `REMOTE_IMPORT_ALLOWED_HOSTS`, `REMOTE_IMPORT_MAX_REDIRECTS`, and `REMOTE_IMPORT_TIMEOUT_SEC` deployment controls.
- `file_transfer_guide` runtime tool documenting the canonical routes between client, MCP cache, ComfyUI, Blender, HyperFrames, and outbound client delivery.
- A standard ComfyUI `LoadImage` workflow guard that rejects HTTP(S) URLs, MCP resource URIs, `/files/...` paths, and MCP `file_id` values with instructions for the correct cache -> ComfyUI upload route.

### Changed

- `comfy_upload_cached_image` now keeps the cached image as a file stream during multipart upload and returns `workflow_load_image_value`, the exact value intended for standard `LoadImage.inputs.image`.
- `comfy_upload_cached_image` explicitly rejects non-image cache artifacts instead of implying a generic audio/video-to-ComfyUI adapter exists.
- File-handoff documentation now defines routing in every supported direction and explicitly states that URLs/file IDs must not be passed directly to standard ComfyUI `LoadImage`.
- Native/reference/streaming transfer remains preferred; base64 import/read tools are compatibility fallbacks only.

## [2.5.0] - 2026-08-18

### Added

- Native MCP `ResourceLink` output through `get_cached_file_resource(file_id)`, keeping `file_id` as the canonical artifact identifier without embedding binary bytes in the tool result.
- `media://cache/{file_id}` MCP resource template as a protocol-level compatibility path when a direct public HTTP(S) file URI is not configured or cannot be used.
- Explicit `starlette>=0.39,<2` runtime dependency and regression coverage for `GET`/`HEAD` plus HTTP byte-range responses on `/files/{file_id}`.
- `docs/FILE_HANDOFF.md` documenting the preferred native-reference/streaming architecture and the remaining compatibility fallbacks.

### Changed

- When `PUBLIC_BASE_URL` is configured, `get_cached_file_resource` prefers the authenticated streaming `/files/{file_id}` URI for large images, video, audio, 3D assets and other binaries.
- `comfy_upload_cached_image` now sends cached image bytes directly to ComfyUI as multipart/form-data instead of converting them to base64 first.
- Inline and chunked base64 output tools remain available for compatibility/debugging, but are no longer the recommended media handoff path.
- Project rule: never resize, recompress, transcode or otherwise alter an artifact solely to make it fit through a tool result.

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
