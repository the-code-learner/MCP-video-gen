from __future__ import annotations

from typing import Any


def replace_file_transfer_guide(mcp: Any) -> None:
    """Replace the older routing guide after native ChatGPT file ingress is installed."""
    mcp.remove_tool("file_transfer_guide")

    @mcp.tool()
    async def file_transfer_guide() -> dict[str, Any]:
        """Return canonical file-routing rules for MCP Video Gen.

        Use this before moving attachments or building workflows when routing is
        ambiguous. `file_id` is local to this MCP cache. ChatGPT attachments should
        use native `openai/fileParams` tools; arbitrary retrievable HTTPS sources use
        server-side streaming. Model-mediated binary base64/chunk uploads are not
        exposed.
        """
        return {
            "canonical_identifier": "file_id inside THIS MCP Video Gen cache",
            "golden_rules": [
                "MCP servers are independent. Never pass another MCP's file_id, ResourceLink or proprietary URI directly to a Video Gen backend.",
                "For a file attached in ChatGPT, use save_uploaded_file (or save_uploaded_files for a batch). ChatGPT binds the attachment through openai/fileParams and supplies a temporary authorized download object to the MCP tool.",
                "Do not manually base64-encode or chunk a ChatGPT attachment. Video Gen intentionally does not expose binary client-upload base64/chunk tools.",
                "For a real HTTPS URL that Video Gen can retrieve directly, use import_remote_file(uri).",
                "After a successful import, reuse the returned Video Gen file_id and never re-upload the same unchanged artifact.",
                "Never pass HTTP(S) URLs, ResourceLinks, /files paths, ChatGPT/OpenAI file IDs, or Video Gen file_ids directly to standard ComfyUI LoadImage.",
                "For cached media, comfy_upload_cached_media(file_id) stages the file into ComfyUI input without a client/base64 round trip.",
                "For standard LoadImage, use workflow_load_image_value returned by the staging tool.",
                "For audio/video/custom loaders, inspect list_loaded_nodes/get_node_definition and use workflow_input_value only where that node expects an input filename/path.",
                "Prefer native references and server-side streaming for cache -> client delivery through get_cached_file_resource.",
                "Never resize, recompress or transcode solely to make a file fit through a tool result.",
            ],
            "client_to_cache_decision": [
                {
                    "priority": 1,
                    "condition": "file is attached in ChatGPT and the client supports openai/fileParams",
                    "action": "save_uploaded_file(file) or save_uploaded_files(files)",
                    "why": "ChatGPT supplies a temporary authorized download object directly to the MCP tool; original bytes stream server-side without model-context base64",
                },
                {
                    "priority": 2,
                    "condition": "a real server-retrievable HTTPS URL is available",
                    "action": "import_remote_file(uri)",
                    "why": "server-side streaming for generic remote sources",
                },
                {
                    "priority": 3,
                    "condition": "client cannot provide openai/fileParams and has no retrievable HTTPS URL",
                    "action": "use a client-native upload/reference mechanism or the authenticated WebGUI as manual recovery",
                    "why": "Video Gen intentionally does not expose model-mediated binary base64/chunk upload tools",
                },
            ],
            "native_chatgpt_upload": {
                "single_tool": "save_uploaded_file",
                "batch_tool": "save_uploaded_files",
                "tool_meta": "openai/fileParams",
                "file_object_fields": ["download_url", "file_id", "mime_type?", "file_name?"],
                "transport": "temporary authorized HTTPS download streamed by Video Gen",
                "binary_through_model_context": False,
                "size_limit": "MAX_UPLOAD_MB per file",
                "security": [
                    "HTTPS only",
                    "no URL credentials",
                    "port 443 only",
                    "public/global DNS addresses only",
                    "redirect re-validation",
                    "bounded redirects/timeouts",
                    "Content-Length and streaming size enforcement",
                    "temporary download URL query strings are not intentionally logged",
                ],
            },
            "removed_binary_upload_tools": [
                "cache_file_base64",
                "file_upload_begin",
                "file_upload_status",
                "file_upload_chunk_auto",
                "file_upload_chunk",
                "file_upload_finish",
                "file_upload_abort",
            ],
            "routes": {
                "chatgpt_attachment_to_cache": {
                    "preferred": "save_uploaded_file(file)",
                    "batch": "save_uploaded_files(files)",
                    "result": "file_id",
                },
                "generic_https_to_cache": {
                    "tool": "import_remote_file(uri)",
                    "requires": "server-retrievable public HTTPS URL",
                    "result": "file_id",
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
                    "fallback": "resources/read, then outbound base64 only for compatibility/debugging",
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
