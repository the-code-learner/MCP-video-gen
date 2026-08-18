# MCP Video Gen

A self-hosted MCP server that can expose local media-generation backends such as ComfyUI and Blender while also providing local media analysis, editing, FFmpeg, HyperFrames, timeline, subtitle, speech, and audio utilities.

The project is designed for **Portainer-only deployments**: the public repository contains a normal multi-file application, while a single `video-mcp.yml` Stack acts as a bootstrap loader for immutable GitHub Releases.

## Capabilities

- Discover ComfyUI nodes actually registered by `/object_info` when ComfyUI is available.
- Scan read-only ComfyUI `models/` and `custom_nodes/` directories when mounted.
- Inspect compatible custom-node source/documentation files.
- Submit arbitrary valid ComfyUI API workflow JSON and inspect queue/history/output state.
- Optionally control host-installed Blender through an authenticated bridge for `bpy` automation, still rendering, animation rendering, and GLB export.
- Import files from the MCP client/AI into the persistent cache using text, one-shot base64, or chunked binary transfer.
- Return cached files to the client/AI through authenticated HTTP download, inline base64, or bounded chunked base64 reads.
- Upload inputs, cache outputs, and retrieve generated image, video, audio, 3D, scene, subtitle, and other files through one `file_id` contract.
- Create and render local HyperFrames projects using HTML/CSS/media.
- Probe, transcode, concatenate, overlay, mux audio, crop, reverse, loop, speed-ramp, and extract frames with FFmpeg.
- Detect silence, black/frozen sections, loudness, interlacing, crop regions, keyframes, and objective SSIM/PSNR differences.
- Build contact sheets/storyboards and perform lightweight frame similarity, motion, duplicate-frame, and best-frame analysis.
- Detect and split scenes with PySceneDetect.
- Create, retime, convert, style, and burn subtitles with pysubs2 + FFmpeg.
- Maintain persistent OpenTimelineIO timelines with tracks, clips, transitions, markers, reordering, inspection, and export.
- Detect beats, tempo, onsets, and pitch with aubio.
- Denoise speech locally with RNNoise.
- Detect speech segments with a small Silero VAD ONNX model.
- Transcribe media, generate subtitles, and obtain word-like timestamps locally with whisper.cpp.
- Optionally synthesize speech with user-supplied Piper voices; Piper is disabled by default.
- Optionally validate Cloudflare Access JWTs at the origin and run a Cloudflare Tunnel sidecar.

This server intentionally does **not** contain fixed AI-generation workflows, long-term memory, or agent skills. It exposes execution primitives so a client or separate knowledge/skills MCP can decide how workflows should be built.

ComfyUI and Blender are **external optional backends**. If either backend is disabled, missing, or temporarily unreachable, the MCP itself remains healthy. Network-dependent tools return a model-readable `available=false` result instead of taking down the server or surfacing a backend-absence tool error.

## Architecture

```text
MCP client / AI
   |
   |<------ generic MCP file transfer ------>
   v
MCP Video Gen + persistent file_id cache
   |
   |---------------- optional ComfyUI API
   |                    |
   |                    +-- installed models
   |                    +-- custom nodes
   |                    +-- image/video/audio generation
   |
   |---------------- optional Blender bridge on VM
   |                    |
   |                    +-- bpy scene creation/editing
   |                    +-- .blend / GLB export
   |                    +-- still / animation rendering
   |
   |---------------- HyperFrames
   |---------------- OpenTimelineIO / subtitles
   |---------------- scene / frame analysis
   |---------------- whisper.cpp / Silero VAD / RNNoise / aubio
   +---------------- FFmpeg

All execution paths exchange files through the same MCP cache.
```

## Portainer deployment

Use `video-mcp.yml` as the Stack definition.

### Optional ComfyUI

Typical ComfyUI connection variables are:

```text
COMFYUI_HOST=host.docker.internal
COMFYUI_PORT=8188
COMFYUI_SCHEME=http
```

For filesystem discovery, set the host paths when ComfyUI exists:

```text
COMFYUI_MODELS_PATH=/host/path/to/ComfyUI/models
COMFYUI_CUSTOM_NODES_PATH=/host/path/to/ComfyUI/custom_nodes
```

These path variables are no longer required for MCP startup. The Stack has generic empty-directory fallbacks, so it can start before ComfyUI is installed. If ComfyUI is unreachable, its network tools report that status to the model while local MCP tools continue working.

### Optional Blender

Blender is disabled by default:

```text
BLENDER_ENABLED=false
BLENDER_BRIDGE_URL=http://host.docker.internal:9876
BLENDER_BRIDGE_TOKEN=
BLENDER_BRIDGE_TIMEOUT_SEC=7200
```

The recommended integration runs `scripts/blender_bridge.py` directly on the VM as a dedicated low-privilege OS account. The container communicates with it over an authenticated local HTTP bridge; Blender itself runs headless on the host. This avoids mounting the host root filesystem or host executables into the MCP container.

After the bridge is installed, configure privately in Portainer:

```text
BLENDER_ENABLED=true
BLENDER_BRIDGE_URL=http://host.docker.internal:9876
BLENDER_BRIDGE_TOKEN=<same long random token used by the host bridge>
```

See **`docs/BLENDER_BRIDGE.md`** for setup, security, systemd hardening, file flow, and examples.

### Cloudflare Tunnel

If you use the included Cloudflare Tunnel sidecar, provide:

```text
CLOUDFLARED_TUNNEL_TOKEN=<set privately in Portainer>
```

Point the remote Tunnel hostname to:

```text
http://video-mcp:8000
```

The MCP endpoint is:

```text
https://your-public-host.example/mcp
```

### Cloudflare Access / Managed OAuth

The application can verify the Cloudflare Access JWT at the origin. Configure these values privately in Portainer:

```text
CF_ACCESS_VERIFY=true
CF_ACCESS_TEAM_DOMAIN=https://your-team.cloudflareaccess.com
CF_ACCESS_AUD=<Access application audience tag>
PUBLIC_BASE_URL=https://your-public-host.example
```

No real domain, audience, tunnel token, bridge token, internal IP, or credential belongs in this public repository.

## External backend availability

`external_backends_status` reports the current state of ComfyUI and Blender. `inventory_summary` includes the same backend status alongside local capabilities.

When an external backend is unavailable, calls return a structure such as:

```json
{
  "ok": false,
  "available": false,
  "backend": "blender",
  "status": "unavailable",
  "message": "Blender integration is disabled..."
}
```

This is deliberately different from an MCP server failure: the model learns that one optional execution path is unavailable and can continue with another one.

## File transfer and shared cache

Every generated/imported artifact is normalized into the MCP cache and identified by `file_id`. This is the interchange layer between the AI client, ComfyUI, Blender, FFmpeg, HyperFrames, subtitles, timelines, and audio utilities.

### Client / AI -> MCP

For small files:

```text
cache_text_file
cache_file_base64
```

For larger binary files:

```text
file_upload_begin
file_upload_chunk
file_upload_finish
file_upload_abort
```

Chunked uploads can specify expected byte length and SHA-256 before promotion into the persistent cache.

### MCP -> client / AI

Metadata:

```text
get_cached_file_info
```

Small existing compatibility path:

```text
get_output_inline_base64
```

Bounded generic reads:

```text
read_cached_file_chunk_base64
```

Every normal cache metadata object also contains `/files/{file_id}` and, when `PUBLIC_BASE_URL` is configured, a complete authenticated download URL.

This means an AI can author a Blender Python script as text, place arbitrary referenced assets into the cache, send those `file_id` values to Blender, receive `.blend`/`.glb`/renders back as new `file_id` values, and then feed those files into ComfyUI or the local post-processing stack.

## Advanced local media utilities

The runtime prepares several small local utilities in addition to FFmpeg/HyperFrames. The Python venv contains PySceneDetect, OpenTimelineIO, pysubs2, ONNX Runtime, NumPy, and headless OpenCV. Debian provides the small `aubio-tools` CLI package. RNNoise and whisper.cpp are built locally from pinned upstream source into the persistent data volume.

Silero VAD, RNNoise, and whisper.cpp model/source artifacts are stored under the persistent data volume. The RNNoise source and model plus the Silero/Whisper model downloads use explicit SHA-256 validation. The default Whisper model is a small quantized model intended for lightweight local transcription; model URLs/hashes and source refs can be overridden through Stack variables.

Relevant variables include:

```text
SILERO_VAD_ENABLED=true
SILERO_VAD_MODEL_URL=<public model URL>
SILERO_VAD_MODEL_SHA256=<expected sha256>

RNNOISE_ENABLED=true
RNNOISE_REF=<pinned upstream commit>
RNNOISE_SOURCE_URL=<public source archive URL>
RNNOISE_SOURCE_SHA256=<expected sha256>
RNNOISE_MODEL_URL=<public model URL>
RNNOISE_MODEL_SHA256=<expected sha256>

WHISPER_CPP_ENABLED=true
WHISPER_CPP_REF=v1.8.6
WHISPER_CPP_BUILD_JOBS=2
WHISPER_MODEL_AUTO_DOWNLOAD=true
WHISPER_MODEL_NAME=tiny-q5_1
WHISPER_MODEL_URL=<public model URL>
WHISPER_MODEL_SHA256=<expected sha256>
```

The first startup after enabling these utilities can take longer because RNNoise and whisper.cpp are built locally and the selected assets are downloaded. Their resulting builds and models remain in `/data`, so normal container recreation does not repeat those builds when the persistent volume is retained. The Stack gives the first startup an extended healthcheck grace period for this reason.

### Optional Piper TTS

Piper is implemented as an optional runtime and is **disabled by default**:

```text
PIPER_ENABLED=false
PIPER_PACKAGE_SPEC=piper-tts
```

When enabled, no voice is downloaded automatically. Voice `.onnx` and matching configuration files live under `/data/piper/voices`; they can be imported from the MCP media cache with `piper_import_voice_file`. This keeps TTS optional because ComfyUI itself can also host audio/TTS workflows.

See `THIRD_PARTY.md` for third-party licensing notes.

## Release selection

The Stack supports:

```text
VIDEO_MCP_VERSION=latest
VIDEO_MCP_CHECK_UPDATES_ON_START=true
VIDEO_MCP_FORCE_REFRESH=false
```

`latest` means the highest non-draft, non-prerelease GitHub Release whose tag exactly matches `vX.Y.Z`. It does **not** mean `main`.

You can also pin a release:

```text
VIDEO_MCP_VERSION=v2.4.0
```

or a commit SHA:

```text
VIDEO_MCP_VERSION=<commit-sha>
```

When update checking is disabled and a valid `/current` source exists, startup is completely cache-first. A failed release lookup, download, or archive validation falls back to the last-known-good source whenever one exists.

## Persistent volumes

The Stack separates three concerns:

```text
video_mcp_code  -> /opt/video-mcp   versioned source cache + /current
video_mcp_venv  -> /opt/venv        persistent Python virtual environment
video_mcp_data  -> /data             media, timelines, models, local tooling, HyperFrames projects/cache
```

The application runtime data root defaults to `/data`. Direct/non-Stack deployments may override it with `VIDEO_MCP_DATA_ROOT`; importing `video_mcp.server` or `video_mcp.entrypoint` does not create the directory. Runtime directories are created only when the application starts.

The Python environment is rebuilt only when `requirements.txt` changes. Rebuilding clears the **contents** of the mounted venv directory; it never removes the Docker mount point itself.

## Source bootstrap security

Source archives are downloaded from GitHub codeload into a staging directory and validated before extraction. The bootstrap rejects:

- absolute paths;
- `..` traversal;
- symbolic links;
- hard links;
- archives with more than one top-level root.

A release receives `.mcp-source-ready` only after extraction and runtime-contract checks succeed. `/current` is switched only after that point, so an interrupted or malformed update cannot replace the last-known-good source.

The ComfyUI model and custom-node filesystem mounts are read-only. AI utility source/model downloads use temporary files and SHA-256 verification before replacing cached artifacts. The optional Blender bridge uses bearer-token authentication and only transports declared job inputs/outputs, but arbitrary Blender Python remains powerful and therefore the bridge must be isolated with an unprivileged OS account.

## HyperFrames

HyperFrames runs locally in the MCP container and uses the same persistent `/data` area as the MCP media cache. Browser assets are cached persistently under `/data/hyperframes-home`.

The default package spec is pinned in the Stack for reproducibility and can be overridden privately:

```text
HYPERFRAMES_NPM_SPEC=hyperframes@0.7.111
```

HyperFrames skills are intentionally disabled in this execution server (`HYPERFRAMES_SKIP_SKILLS=1`).

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt pytest PyYAML
PYTHONPATH=src python -m pytest -q
python scripts/check_public_repo.py
```

CI checks Python compilation, server/entrypoint imports, tests, YAML parsing, shell/Python helper syntax, Compose rendering, version/changelog consistency, and public-repository secret/private-network guardrails.

## Release process

1. Develop on a branch and open a PR.
2. CI must pass.
3. Update `VERSION` and `CHANGELOG.md`.
4. Merge to `main`.
5. CI creates the immutable `vX.Y.Z` tag and matching stable GitHub Release if it does not already exist.

Application tags/releases are reserved for exact `vX.Y.Z` names so unrelated model or asset releases cannot affect `VIDEO_MCP_VERSION=latest` resolution.

## License and attribution

Licensed under the **Apache License 2.0**. See `LICENSE`.

Redistributions and derivative works must preserve the attribution notice in `NOTICE` in accordance with the Apache License 2.0. Third-party components retain their own licenses; see `THIRD_PARTY.md`.
