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
        transfer, avoid model-mediated base64 when possible, and use a deterministic
        Programmatic Tool Calling loop for chunked transfer when the client supports
        PTC and the program can access the source bytes.
        """
        return {
            "canonical_identifier": "file_id inside THIS MCP Video Gen cache",
            "golden_rules": [
                "MCP servers are independent. Never pass another MCP's file_id, ResourceLink or proprietary URI directly to a Video Gen backend.",
                "For client -> cache, first prefer import_remote_file(uri) when a real server-retrievable HTTPS URL exists.",
                "If the complete base64 payload already exists and comfortably fits one tool call, use cache_file_base64 once rather than starting a chunked upload.",
                "If one-shot base64 is rejected or truncated, create one chunked session for the unchanged source file.",
                "For new chunked clients prefer file_upload_chunk_auto: the server owns the offset and rejects malformed chunks before writing them.",
                "When Programmatic Tool Calling is supported and its program can access the source bytes, run file_upload_begin -> repeated file_upload_chunk_auto -> file_upload_finish as one bounded programmatic stage without returning to the language model between chunks.",
                "PTC is a client/API capability; this MCP server can expose a PTC-friendly contract but cannot activate PTC on behalf of a client that does not support it.",
                "After a successful import, reuse the returned file_id and never re-encode/re-upload the same unchanged artifact.",
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
                    "why": "one MCP round trip; preferred before chunking",
                },
                {
                    "priority": 3,
                    "condition": "one-shot base64 is impractical/rejected and client supports PTC with access to source bytes",
                    "action": "one PTC stage: file_upload_begin -> loop file_upload_chunk_auto -> file_upload_finish",
                    "why": "multiple deterministic tool calls without a language-model turn between chunks",
                },
                {
                    "priority": 4,
                    "condition": "chunking required but PTC is unavailable",
                    "action": "file_upload_begin -> file_upload_chunk_auto calls -> file_upload_finish",
                    "why": "fallback; server-owned offset minimizes client/model bookkeeping",
                },
            ],
            "chunking": {
                "max_decoded_bytes_per_call": 4194304,
                "legacy_explicit_offset_tool": "file_upload_chunk",
                "preferred_tool": "file_upload_chunk_auto",
                "status_tool": "file_upload_status",
                "atomic_validation": True,
                "server_owned_offset": True,
                "rule": "a rejected auto chunk must leave server progress unchanged; retry the same source range or abort",
            },
            "programmatic_tool_calling": {
                "server_can_enable_ptc": False,
                "client_must_support_ptc": True,
                "source_bytes_must_be_accessible_to_program": True,
                "bounded_stage": [
                    "file_upload_begin",
                    "file_upload_status (optional recovery)",
                    "file_upload_chunk_auto repeated until complete_by_size=true",
                    "file_upload_finish",
                ],
                "stop_condition": "complete_by_size=true then successful file_upload_finish",
                "retry_rule": "retry a rejected chunk at most after correcting the same source range; abort on ambiguous source bytes",
                "final_output": "canonical Video Gen file_id plus verified size/SHA metadata",
            },
            "routes": {
                "client_reference_to_cache": {
                    "preferred": "import_remote_file(uri)",
                    "requires": "server-retrievable HTTPS URL",
                    "result": "file_id",
                    "fallback": "cache_file_base64 once if practical; otherwise PTC/programmatic auto-chunk loop when supported",
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
