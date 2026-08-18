# MCP Video Gen

A self-hosted MCP server that exposes a local ComfyUI installation as a dynamic media-generation backend and adds local FFmpeg and HyperFrames post-production tools.

The project is designed for **Portainer-only deployments**: the public repository contains a normal multi-file application, while a single `video-mcp.yml` Stack acts as a bootstrap loader for immutable GitHub Releases.

## Capabilities

- Discover ComfyUI nodes actually registered by `/object_info`.
- Scan read-only ComfyUI `models/` and `custom_nodes/` directories.
- Inspect compatible custom-node source/documentation files.
- Submit arbitrary valid ComfyUI API workflow JSON and inspect queue/history/output state.
- Upload inputs, cache outputs, and retrieve generated media.
- Probe, transcode, concatenate, overlay, mux audio, and extract frames with FFmpeg.
- Create and render local HyperFrames projects using HTML/CSS/media.
- Move media between ComfyUI, HyperFrames, and FFmpeg through one persistent MCP-side cache.
- Optionally validate Cloudflare Access JWTs at the origin and run a Cloudflare Tunnel sidecar.

This server intentionally does **not** contain fixed AI-generation workflows, long-term memory, or agent skills. It exposes execution primitives so a client or separate knowledge/skills MCP can decide how workflows should be built.

## Architecture

```text
MCP client
   |
   v
MCP Video Gen
   |---------------- ComfyUI API
   |                    |
   |                    +-- installed models
   |                    +-- custom nodes
   |
   |---------------- HyperFrames
   |
   +---------------- FFmpeg

Persistent media cache is shared by all three execution paths.
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
VIDEO_MCP_VERSION=v2.2.0
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
video_mcp_data  -> /data             media cache + HyperFrames projects/browser cache
```

The Python environment is rebuilt only when `requirements.txt` changes. Rebuilding clears the **contents** of the mounted venv directory; it never removes the Docker mount point itself.

## Source bootstrap security

Source archives are downloaded from GitHub codeload into a staging directory and validated before extraction. The bootstrap rejects:

- absolute paths;
- `..` traversal;
- symbolic links;
- hard links;
- archives with more than one top-level root.

A release receives `.mcp-source-ready` only after extraction and runtime-contract checks succeed. `/current` is switched only after that point, so an interrupted or malformed update cannot replace the last-known-good source.

The ComfyUI model and custom-node filesystem mounts are read-only.

## HyperFrames

HyperFrames runs locally in the MCP container and uses the same persistent `/data` area as the MCP media cache. The runtime requires Node.js 22+, FFmpeg, and a compatible Chrome/headless-shell. Browser assets are cached persistently under `/data/hyperframes-home`.

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

CI checks Python compilation/import, tests, YAML parsing, shell syntax, Compose rendering, version/changelog consistency, and basic public-repository secret/private-network guardrails.

## Release process

1. Develop on a branch and open a PR.
2. CI must pass.
3. Update `VERSION` and `CHANGELOG.md`.
4. Merge to `main`.
5. CI creates the immutable `vX.Y.Z` tag and matching stable GitHub Release if it does not already exist.

Application tags/releases are reserved for exact `vX.Y.Z` names so unrelated model or asset releases cannot affect `VIDEO_MCP_VERSION=latest` resolution.

## License and attribution

Licensed under the **Apache License 2.0**. See `LICENSE`.

Redistributions and derivative works must preserve the attribution notice in `NOTICE` in accordance with the Apache License 2.0.
