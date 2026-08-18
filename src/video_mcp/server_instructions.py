from __future__ import annotations

from typing import Any


SERVER_INSTRUCTIONS = """
MCP Video Gen is an execution and media-routing server. Follow these rules when using it:

1. Treat `file_id` as the canonical identifier only for files already stored in THIS MCP Video Gen cache. File IDs, ResourceLinks, signed URLs, or resource URIs belonging to another MCP server are not local Video Gen files. MCP servers are independent; when an artifact must move from another MCP into Video Gen, the client is the bridge and must import/transfer the file into this cache first.

2. Standard ComfyUI `LoadImage` expects a filename that already exists in ComfyUI's input namespace. NEVER put an HTTP(S) URL, MCP ResourceLink, `media://` URI, `/files/...` path, ChatGPT/OpenAI file ID, another MCP's file ID, or a Video Gen cache `file_id` directly in `LoadImage.inputs.image`.

3. For an image already in the Video Gen cache, call `comfy_upload_cached_image(file_id)` and then use exactly the returned `workflow_load_image_value` in `LoadImage.inputs.image`.

4. For an external file with a real HTTPS URL that this server can retrieve, call `import_remote_file(uri)` first. It returns a Video Gen `file_id`. If the client only exposes an opaque/local file identifier and no server-retrievable URL, use the supported client upload path; do not invent a URL.

5. Prefer native file/resource references and server-side streaming over base64. `cache_file_base64`, chunked base64 upload/read, and inline base64 are compatibility fallbacks, not the normal media path. Never resize, recompress, or transcode solely to fit media through a tool result.

6. ComfyUI output should be normalized into the Video Gen cache with `cache_output`. Return cached files to the client with `get_cached_file_resource`. Pass cached files to Blender and HyperFrames using their documented `file_id` adapters rather than temporary public URLs.

7. There is no generic standard cache-audio/video-to-ComfyUI adapter. Introspect the installed ComfyUI node/API that consumes that media. Do not put URLs into unrelated nodes as a workaround.

8. Do not invent integrations or capabilities. Check `build_status`, `advanced_capabilities`, `external_backends_status`, `inventory_summary`, and node/model introspection when uncertain. ElevenLabs speech-to-speech is NOT implemented unless a dedicated ElevenLabs tool is actually present in the current tool list.

9. Cache files are persistent by default. Automatic cache deletion is disabled unless the deployment explicitly enables a retention policy. Use `cache_status` to inspect the active policy, `cache_pin` to protect important artifacts, `cache_unpin` to remove protection, and `cache_cleanup(dry_run=true)` before destructive manual cleanup. Pinned files must never be removed by retention cleanup.

10. If file routing is ambiguous, call `file_transfer_guide()` before moving bytes or building a ComfyUI workflow.
""".strip()


def install_server_instructions(mcp: Any) -> None:
    """Install instructions before the MCP server begins accepting clients.

    MCP SDK 2.x exposes instructions at construction time but no public setter.
    Video Gen registers tools in a separate entrypoint module, so we update the
    underlying low-level server before Uvicorn starts. Fail loudly if the SDK
    surface changes rather than silently running without the safety guidance.
    """
    lowlevel = getattr(mcp, "_lowlevel_server", None)
    if lowlevel is None or not hasattr(lowlevel, "instructions"):
        raise RuntimeError("MCP SDK no longer exposes the server instructions field")
    lowlevel.instructions = SERVER_INSTRUCTIONS
