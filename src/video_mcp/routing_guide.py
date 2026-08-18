from __future__ import annotations

from typing import Any


def replace_file_transfer_guide(mcp: Any) -> None:
    """Replace the older routing guide after media staging becomes generic."""
    mcp.remove_tool("file_transfer_guide")

    @mcp.tool()
    async def file_transfer_guide() -> dict[str, Any]:
        """Return canonical file-routing and transfer-efficiency rules for Video Gen.

        Use this before moving attachments or building workflows when routing is
        ambiguous. `file_id` is local to this MCP cache. Prefer native/reference
        transfer, avoid model-mediated base64 when possible, and never split small
        files into many tiny MCP chunk calls.
        """
        return {
            "canonical_identifier": "file_id inside THIS MCP Video Gen cache",
            "golden_rules": [
                "MCP servers are independent. Never pass another MCP's file_id, ResourceLink or proprietary URI directly to a Video Gen backend.",
                "For client -> cache, first prefer import_remote_file(uri) when a real server-retrievable HTTPS URL exists.",
                "If the complete base64 payload already exists and comfortably fits one tool call, use cache_file_base64 once rather than starting a chunked upload.",
                "Never intentionally split a small KB-sized file into many file_upload_chunk calls. Every chunk is a separate MCP/LLM round trip.",
                "When chunking is genuinely required, use the largest practical decoded chunk. The server accepts up to 4 MiB decoded per file_upload_chunk; about 1 MiB is a reasonable default when client limits are unknown.",
                "If remaining_bytes is <= 4 MiB and the remaining bytes are available, send the entire remainder in one chunk and then call file_upload_finish immediately.",
                "Never pass HTTP(S) URLs, ResourceLinks, /files paths, or MCP file_ids directly to standard ComfyUI LoadImage.",
                "For cached media: comfy_upload_cached_media(file_id) stages the file into ComfyUI input without a client/base64 round trip.",
                "For standard LoadImage, use workflow_load_image_value returned by the staging tool.",
                "For audio/video/custom loaders, inspect list_loaded_nodes/get_node_definition and use workflow_input_value only where that node expects an input filename/path.",
                "If the client exposes only an opaque file identifier, use the supported client upload path; do not invent a URL.",
                "Prefer native references and server-side streaming over base64 compatibility tools.",
                "Never resize, recompress or transcode solely to make a file fit through a tool result.",
            ],
            "client_to_cache_decision": [
                {
                    "priority": 1,
                    "condition": "real server-retrievable HTTPS URL is available",
                    "action": "import_remote_file(uri)",
                    "why": "server-side streaming; avoids binary/base64 through the model",
                },
                {
                    "priority": 2,
                    "condition": "complete base64 payload is already available and fits one tool call",
                    "action": "cache_file_base64(filename, data_base64)",
                    "why": "one MCP round trip; strongly preferred for small files",
                },
                {
                    "priority": 3,
                    "condition": "one-shot base64 is impractical and no retrievable reference exists",
                    "action": "file_upload_begin -> large file_upload_chunk calls -> file_upload_finish",
                    "why": "compatibility fallback; minimize number of model-mediated calls",
                },
            ],
            "chunking": {
                "max_decoded_bytes_per_call": 4194304,
                "default_target_decoded_bytes": 1048576,
                "anti_pattern": "many KB-sized chunks for a small file",
                "rule": "use the largest practical chunk; if the remainder is <= 4 MiB, send it in one final chunk when available",
            },
            "routes": {
                "client_reference_to_cache": {
                    "preferred": "import_remote_file(uri)",
                    "requires": "server-retrievable public HTTPS URL",
                    "result": "file_id",
                    "fallback": "cache_file_base64 once if practical; otherwise file_upload_begin/chunk/finish with large chunks",
                },
                "cache_media_to_comfyui_input": {
                    "tool": "comfy_upload_cached_media(file_id)",
                    "transport": "server-side multipart streaming into ComfyUI input namespace",
                    "result": "workflow_input_value",
                    "image_result": "workflow_load_image_value for standard LoadImage",
                    "audio_video_rule": "staging is generic; loader semantics are node-specific and must be introspected",
                },
                "cache_image_to_comfyui_input_legacy": {
                    "tool": "comfy_upload_cached_image(file_id)",
                    "status": "backward-compatible image-only alias",
                },
                "comfyui_output_to_cache": {
                    "tool": "cache_output(filename, subfolder, output_type)",
                    "result": "file_id",
                },
                "cache_to_client": {
                    "preferred": "get_cached_file_resource(file_id)",
                    "large_file_path": "HTTPS /files/{file_id} streaming when PUBLIC_BASE_URL is configured",
                    "fallback": "resources/read, then chunked/inline base64 only for compatibility",
                },
                "cache_to_blender": {
                    "method": "pass local file_id directly to Blender MCP tools/input_files; bytes stay server-side",
                },
                "blender_to_cache": {
                    "method": "Blender MCP tools cache declared outputs and return file_ids",
                },
                "cache_to_hyperframes": {
                    "tool": "hyperframes_import_cached_media(project_id, file_id, destination)",
                },
            },
        }
