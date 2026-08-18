# MCP Video Gen

A self-hosted MCP server that exposes a local ComfyUI installation as a dynamic media-generation backend and adds local media analysis, editing, FFmpeg, HyperFrames, timeline, subtitle, speech, and audio utilities.

The project is designed for **Portainer-only deployments**: the public repository contains a normal multi-file application, while a single `video-mcp.yml` Stack acts as a bootstrap loader for immutable GitHub Releases.

## Capabilities

- Discover ComfyUI nodes actually registered by `/object_info`.
- Scan read-only ComfyUI `models/` and `custom_nodes/` directories.
- Inspect compatible custom-node source/documentation files.
- Submit arbitrary valid ComfyUI API workflow JSON and inspect queue/history/output state.
- Upload inputs, cache outputs, and retrieve generated image, video, or audio media.
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
- Transcribe media and generate subtitles locally with whisper.cpp.
- Optionally synthesize speech with user-supplied Piper voices; Piper is disabled by default.
- Move media between ComfyUI, HyperFrames, timelines, audio tools, and FFmpeg through one persistent MCP-side cache.
- Optionally validate Cloudflare Access JWTs at the origin and run a Cloudflare Tunnel sidecar.

This server intentionally does **not** contain fixed AI-generation workflows, long-term memory, or agent skills. It exposes execution primitives so a client or separate knowledge/skills MCP can decide how workflows should be built.

ComfyUI outputs are media-agnostic at the MCP boundary: image, video, and audio outputs can all be cached and passed into the analysis/editing tools. This also allows audio-producing ComfyUI workflows to coexist with local transcription, VAD, denoising, subtitle, and timeline operations.

## Architecture

```text
MCP client
   |
   v
MCP Video Gen
   |
   |---------------- ComfyUI API
   |                    |
   |                    +-- installed models
   |                    +-- custom nodes
   |                    +-- image/video/audio generation
   |
   |---------------- HyperFrames
   |
   |---------------- OpenTimelineIO / subtitles
   |
   |---------------- scene / frame analysis
   |
   |---------------- whisper.cpp / Silero VAD / RNNoise / aubio
   |
   +---------------- FFmpeg

Persistent media cache is shared by all execution paths.
```

## Portainer deployment

Use `video-mcp.yml` as the Stack definition.

Required Stack variables:

```text
COMFYUI_MODELS_PATH=/host/path/to/ComfyUI/models
COMFYUI_CUSTOM_NODES_PATH=/host/path/to/ComfyUI/custom_nodes
```

Typical ComfyUI connection variables:

```text
COMFYUI_HOST=host.docker.internal
COMFYUI_PORT=8188
COMFYUI_SCHEME=http
```

If you use the included Cloudflare Tunnel sidecar, also provide:

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

No real domain, audience, tunnel token, internal IP, or credential belongs in this public repository.

## Advanced local media utilities

The v2.3 runtime prepares several small local utilities in addition to FFmpeg/HyperFrames. The Python venv contains PySceneDetect, OpenTimelineIO, pysubs2, ONNX Runtime, NumPy, and headless OpenCV. Debian packages provide aubio and RNNoise.

Silero VAD and whisper.cpp assets are stored under the persistent data volume. Downloads use explicit SHA-256 validation. The default Whisper model is a small quantized model intended for lightweight local transcription; both model URL/hash and whisper.cpp ref can be overridden through Stack variables.

Relevant variables include:

```text
SILERO_VAD_ENABLED=true
SILERO_VAD_MODEL_URL=<public model URL>
SILERO_VAD_MODEL_SHA256=<expected sha256>

WHISPER_CPP_ENABLED=true
WHISPER_CPP_REF=v1.8.6
WHISPER_CPP_BUILD_JOBS=2
WHISPER_MODEL_AUTO_DOWNLOAD=true
WHISPER_MODEL_NAME=tiny-q5_1
WHISPER_MODEL_URL=<public model URL>
WHISPER_MODEL_SHA256=<expected sha256>
```

The first startup after enabling these utilities can take longer because whisper.cpp is built locally and the selected model is downloaded. The build and model remain in `/data`, so normal container recreation does not repeat that work when the persistent volume is retained.

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
VIDEO_MCP_VERSION=v2.3.0
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

The ComfyUI model and custom-node filesystem mounts are read-only. AI utility model downloads use temporary files and SHA-256 verification before replacing a cached model.

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

CI checks Python compilation, both server/entrypoint imports, tests, YAML parsing, both shell scripts, Compose rendering, version/changelog consistency, and public-repository secret/private-network guardrails.

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
