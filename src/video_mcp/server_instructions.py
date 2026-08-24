from __future__ import annotations

import os
from typing import Any


SERVER_DESCRIPTION = (
    "MCP Video Gen is a media-generation, media-processing and media-routing MCP server. "
    "Use it for image/video/audio generation or editing, local TTS and voice cloning, "
    "ComfyUI workflows and installed custom nodes/models, FFmpeg processing, HyperFrames "
    "overlays, Blender/3D operations, transcription, subtitles, royalty-free music search, "
    "resource/model management, and moving media through the persistent cache. The connected "
    "ComfyUI installation can expose additional dynamic capabilities through custom nodes, "
    "including third-party services that may require their own credentials."
)

SERVER_INSTRUCTIONS = """
You have access to MCP Video Gen. When a user request involves images, video, audio, media generation/editing/analysis, local TTS/voice cloning, music, ComfyUI, FFmpeg, HyperFrames, Blender/3D, transcription or subtitles, consider this server before assuming the required capability is unavailable.

MCP Video Gen is an execution and media-routing server. Follow these rules when using it:

1. Treat `file_id` as the canonical identifier only for files already stored in THIS MCP Video Gen cache. File IDs, ResourceLinks, signed URLs, or resource URIs belonging to another MCP server are not local Video Gen files. MCP servers are independent; when an artifact must move from another MCP into Video Gen, it must first be imported into this cache through a supported transfer route.

2. For a file attached directly in ChatGPT, use `save_uploaded_file(file)` or `save_uploaded_files(files)`. These tools declare `_meta["openai/fileParams"]`, so a compatible ChatGPT client can bind the attachment and pass a temporary authorized download object (`download_url`, `file_id`, optional MIME/name) directly to the MCP tool. Do NOT manually base64-encode or chunk the attachment. Video Gen intentionally does not expose binary client-upload base64/chunk tools.

3. For an external artifact that already has a real HTTPS URL this server can retrieve, use `import_remote_file(uri)`. Do not invent an HTTPS URL from an opaque file ID. After either native ChatGPT upload or remote import succeeds, reuse the returned local `file_id` for all later Video Gen operations; never re-upload the same unchanged artifact.

4. Standard ComfyUI `LoadImage` expects a filename that already exists in ComfyUI's input namespace. NEVER put an HTTP(S) URL, MCP ResourceLink, `media://` URI, `/files/...` path, ChatGPT/OpenAI file ID, another MCP's file ID, or a Video Gen cache `file_id` directly in `LoadImage.inputs.image`.

5. For an image already in the Video Gen cache, use `comfy_upload_cached_media(file_id)` (or the backward-compatible `comfy_upload_cached_image`) and then use exactly the returned `workflow_load_image_value` in standard `LoadImage.inputs.image`.

6. For cached audio/video/other files that a ComfyUI custom node needs, use `comfy_upload_cached_media(file_id)` to stage the file into ComfyUI's input namespace. Then inspect `list_loaded_nodes` / `get_node_definition` and use the returned `workflow_input_value` only for a node parameter that actually expects an input-file filename/path. Staging a file does not prove every node can consume it.

7. ComfyUI output should be normalized into the Video Gen cache with `cache_output`. Return cached files to the client with `get_cached_file_resource`. Pass cached files to Blender and HyperFrames using their documented `file_id` adapters rather than temporary public URLs. `read_cached_file_chunk_base64` is outbound compatibility/debug only, not an upload mechanism.

8. Do not invent integrations or capabilities, but do not assume an external integration is absent merely because Video Gen has no dedicated MCP tool for it. ComfyUI custom nodes can expose third-party services and may require their own API keys or credentials. When uncertain, inspect `list_loaded_nodes`, `get_node_definition`, `inventory_summary`, `external_backends_status`, `advanced_capabilities`, `local_ai_status`, and `build_status` before deciding whether a capability is available or configured.

9. Cache files are persistent by default. Automatic cache deletion is disabled unless the deployment explicitly enables a retention policy. Use `cache_status` to inspect the active policy, `cache_pin` to protect important artifacts, `cache_unpin` to remove protection, and `cache_cleanup(dry_run=true)` before destructive manual cleanup. Pinned files must never be removed by retention cleanup.

10. Large optional local-AI artifacts are not preloaded. If an artifact family exceeds 100 MiB, use `model_catalog` / `model_recommend` and offer at least the light and optimal profiles. Before a large install, inspect current disk, RAM and VRAM. Treat RAM/VRAM recommendations as point-in-time guidance because external workloads may change concurrently.

11. Never delete Video Gen cache files merely to make an installation fit. If disk space is insufficient, use `cache_reclaim_preview` to show exactly which unpinned cache files could be reclaimed, explain the tradeoff, obtain explicit user approval, and only then call `cache_reclaim_files(..., confirm=true)` with the approved file IDs. Do not infer approval from the earlier request to install a model. Pinned files remain protected.

12. `runtime_resources` separates cgroup-accounted Video Gen RAM from an estimated remainder on the VM. GPU memory reserved by the registered Qwen worker is attributable to Video Gen; the remaining GPU usage is `external_or_unattributed` because host PID namespace and Docker socket are deliberately not exposed. Never claim that residual VRAM belongs to a particular external tool unless independently proven.

13. `release_runtime_resources` may unload Qwen and clear CUDA caches. Aggressive mode may stop only child workers explicitly created and registered by this Video Gen process. Never kill arbitrary PIDs found through `nvidia-smi`, never use `pkill`/`killall`/GPU reset, never terminate the MCP main process/container, and never attempt to stop ComfyUI, Blender, other containers, or host processes.

14. Voice cloning must be used only with an authorized reference voice. `qwen_voice_clone_create` requires `consent_confirmed=true`; do not set that flag unless the user has explicitly indicated they have authorization to clone that voice. Persistent voice profiles are local under `/data` and should not be exposed outside the requested workflow.

15. Openverse music search returns license metadata from aggregated sources. Preserve title, creator, source, attribution, landing page and license metadata when importing audio, and remind the user to verify the source landing page/license before publication when licensing matters.

16. The WebGUI at `/` can be used to inspect/upload/download/delete/pin cached files and inspect/export the sanitized persistent activity log. Browser upload is a manual recovery/admin path; native ChatGPT attachment upload is the preferred autonomous path for ChatGPT clients.

17. Temporary authorized file-download URLs are bearer-like capabilities. Never expose their query strings, signatures, or full URLs in user-visible output, logs, memory, or generated workflows. The server validates HTTPS/public addressing and suppresses generic httpx/httpcore INFO request logging for these transfers.

18. If file routing is ambiguous, call `file_transfer_guide()` before moving bytes or building a ComfyUI workflow.
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
