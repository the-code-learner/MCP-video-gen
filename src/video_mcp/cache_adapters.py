from __future__ import annotations

import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any, Callable


def register_cache_adapters(
    mcp: Any,
    *,
    server_module: Any,
    cached: Callable[[str], Path],
) -> None:
    """Register zero-client-round-trip adapters between the shared cache and backends."""

    @mcp.tool()
    async def comfy_upload_cached_image(
        file_id: str,
        overwrite: bool = False,
        subfolder: str = "",
    ) -> dict[str, Any]:
        """Put one MCP-cached image into ComfyUI /input using multipart binary upload.

        This is the canonical cache -> ComfyUI image route. Pass an MCP `file_id`,
        never an HTTP(S) URL, ResourceLink, `/files/...` path or base64 payload.
        The cached file is streamed server-side as multipart/form-data directly to
        ComfyUI `/upload/image`; the bytes never route through the AI/client.

        After success, use the returned `workflow_load_image_value` as the value
        of standard ComfyUI `LoadImage.inputs.image`. Do NOT use the original URL
        or MCP file_id inside LoadImage.

        If ComfyUI is absent/unreachable, the tool returns a structured
        availability result rather than an MCP tool error.
        """
        try:
            await server_module.comfy("GET", "object_info", timeout=5)
        except Exception as exc:
            return {
                "ok": False,
                "available": False,
                "backend": "comfyui",
                "status": "unavailable",
                "message": f"ComfyUI is not currently reachable through the MCP: {exc}",
                "endpoint": server_module.COMFY_URL,
            }

        source = cached(file_id)
        filename = source.name.split("__", 1)[-1]
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if not content_type.startswith("image/"):
            return {
                "ok": False,
                "available": True,
                "backend": "comfyui",
                "status": "operation_failed",
                "message": (
                    "comfy_upload_cached_image only accepts image files. There is no generic "
                    "cache->ComfyUI audio/video adapter; use a media-specific installed node/API."
                ),
                "endpoint": server_module.COMFY_URL,
                "source_file_id": file_id,
                "content_type": content_type,
            }

        form = {
            "overwrite": "true" if overwrite else "false",
            "subfolder": subfolder,
        }

        try:
            # Keep the file handle open for the duration of the multipart request so
            # large images are not copied into an intermediate base64/string payload.
            with source.open("rb") as handle:
                result = await server_module.comfy(
                    "POST",
                    "upload/image",
                    data=form,
                    files={"image": (filename, handle, content_type)},
                )
        except Exception as exc:
            return {
                "ok": False,
                "available": True,
                "backend": "comfyui",
                "status": "operation_failed",
                "message": str(exc),
                "endpoint": server_module.COMFY_URL,
                "source_file_id": file_id,
            }

        returned_name = filename
        returned_subfolder = subfolder.strip("/")
        returned_type = "input"
        if isinstance(result, dict):
            returned_name = str(result.get("name") or returned_name)
            returned_subfolder = str(result.get("subfolder") or returned_subfolder).strip("/")
            returned_type = str(result.get("type") or returned_type)

        workflow_value = str(
            PurePosixPath(returned_subfolder) / returned_name
            if returned_subfolder
            else PurePosixPath(returned_name)
        )

        return {
            "ok": True,
            "available": True,
            "backend": "comfyui",
            "status": "uploaded",
            "source_file_id": file_id,
            "comfyui": result,
            "comfyui_input": {
                "name": returned_name,
                "subfolder": returned_subfolder,
                "type": returned_type,
            },
            "workflow_load_image_value": workflow_value,
            "next_step": (
                "Set standard LoadImage.inputs.image to workflow_load_image_value; "
                "do not pass the original URL or MCP file_id to ComfyUI."
            ),
        }
