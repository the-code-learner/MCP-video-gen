from __future__ import annotations

import os
from typing import Any


SERVER_DESCRIPTION = (
    "MCP Video Gen is a media-generation, media-processing and media-routing MCP server. "
    "Use it for image/video/audio generation or editing, ComfyUI workflows and installed "
    "custom nodes/models, FFmpeg processing, HyperFrames overlays, Blender/3D operations, "
    "transcription, subtitles, media analysis, and moving media through the persistent cache. "
    "The connected ComfyUI installation can expose additional dynamic capabilities through "
    "custom nodes, including third-party services that may require their own credentials."
)

SERVER_INSTRUCTIONS = """
You have access to MCP Video Gen. When a user request involves images, video, audio, media generation/editing/analysis, ComfyUI, FFmpeg, HyperFrames, Blender/3D, transcription or subtitles, consider this server before assuming the required capability is unavailable.

MCP Video Gen is an execution and media-routing server. Follow these rules when using it:

1. Treat `file_id` as the canonical identifier only for files already stored in THIS MCP Video Gen cache. File IDs, ResourceLinks, signed URLs, or resource URIs belonging to another MCP server are not local Video Gen files. MCP servers are independent; when an artifact must move from another MCP into Video Gen, the client is the bridge and must import/transfer the file into this cache first.

2. Standard ComfyUI `LoadImage` expects a filename that already exists in ComfyUI's input namespace. NEVER put an HTTP(S) URL, MCP ResourceLink, `media://` URI, `/files/...` path, ChatGPT/OpenAI file ID, another MCP's file ID, or a Video Gen cache `file_id` directly in `LoadImage.inputs.image`.

3. For an image already in the Video Gen cache, use `comfy_upload_cached_media(file_id)` (or the backward-compatible `comfy_upload_cached_image`) and then use exactly the returned `workflow_load_image_value` in standard `LoadImage.inputs.image`.

4. For cached audio/video/other files that a ComfyUI custom node needs, use `comfy_upload_cached_media(file_id)` to stage the file into ComfyUI's input namespace. Then inspect `list_loaded_nodes` / `get_node_definition` and use the returned `workflow_input_value` only for a node parameter that actually expects an input-file filename/path. Staging a file does not prove every node can consume it.

5. For an external file with a real HTTPS URL that this server can retrieve, call `import_remote_file(uri)` first. It returns a Video Gen `file_id`. If the client only exposes an opaque/local file identifier and no server-retrievable URL, use the supported client upload path; do not invent a URL.

6. Prefer native file/resource references and server-side streaming over base64. If a complete small file is already available as base64 and fits one tool call, use `cache_file_base64` once instead of starting a chunked upload. Use `file_upload_begin/chunk/finish` only when one-shot transfer is impractical. Each chunk is a full MCP/LLM round trip: never intentionally split a small KB-sized file into many tiny chunks. Use the largest practical chunk; `file_upload_chunk` accepts up to 4 MiB decoded per call, with about 1 MiB a good default when client limits are unknown. Never resize, recompress, or transcode solely to fit media through a tool result.

7. ComfyUI output should be normalized into the Video Gen cache with `cache_output`. Return cached files to the client with `get_cached_file_resource`. Pass cached files to Blender and HyperFrames using their documented `file_id` adapters rather than temporary public URLs.

8. Do not invent integrations or capabilities, but do not assume an external integration is absent merely because Video Gen has no dedicated MCP tool for it. ComfyUI custom nodes can expose third-party services and may require their own API keys or credentials. When uncertain, inspect `list_loaded_nodes`, `get_node_definition`, `inventory_summary`, `external_backends_status`, `advanced_capabilities`, and `build_status` before deciding whether a capability is available or configured.

9. Cache files are persistent by default. Automatic cache deletion is disabled unless the deployment explicitly enables a retention policy. Use `cache_status` to inspect the active policy, `cache_pin` to protect important artifacts, `cache_unpin` to remove protection, and `cache_cleanup(dry_run=true)` before destructive manual cleanup. Pinned files must never be removed by retention cleanup.

10. The WebGUI at `/` can be used to inspect/upload/download/delete/pin cached files and inspect the sanitized persistent activity log. The activity log never intentionally stores secrets, full binary/base64 payloads, or signed-URL query strings.

11. If file routing is ambiguous, call `file_transfer_guide()` before moving bytes or building a ComfyUI workflow.
""".strip()


def install_server_instructions(mcp: Any) -> None:
    """Install global MCP identity and usage guidance before clients connect.

    The current MCP SDK exposes these fields on the underlying low-level server.
    Keeping this helper lets the project register tools in a separate entrypoint
    while still publishing strong serverInfo metadata and instructions.
    """
    lowlevel = getattr(mcp, "_lowlevel_server", None)
    if lowlevel is None or not hasattr(lowlevel, "instructions"):
        raise RuntimeError("MCP SDK no longer exposes the server instructions field")
    lowlevel.title = "MCP Video Gen"
    lowlevel.description = SERVER_DESCRIPTION
    lowlevel.instructions = SERVER_INSTRUCTIONS
    runtime_version = os.getenv("VIDEO_MCP_APP_VERSION", "").strip()
    if runtime_version:
        lowlevel.version = runtime_version
