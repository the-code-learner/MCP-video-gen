# Changelog

All notable changes to this project are documented here.

The project uses Semantic Versioning. Stable application releases are tagged `vX.Y.Z`.

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
