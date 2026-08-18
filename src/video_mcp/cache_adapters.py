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

    async def _stage_cached_file(
        file_id: str,
        *,
        overwrite: bool,
        subfolder: str,
    ) -> dict[str, Any]:
        """Stage any cached file into ComfyUI's input namespace.

        ComfyUI's current `/upload/image` implementation is the stable upload
        endpoint and writes the received multipart file into the selected
        input/output/temp directory without decoding it as an image. Video Gen
        therefore uses it as a transport-level staging primitive for arbitrary
        cached media. Whether a particular audio/video/custom node accepts the
        resulting filename is node-specific and must be verified separately.
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
        media_kind = content_type.split("/", 1)[0] if "/" in content_type else "other"
        if media_kind not in {"image", "audio", "video"}:
            media_kind = "other"

        form = {
            "overwrite": "true" if overwrite else "false",
            "subfolder": subfolder,
            "type": "input",
        }

        try:
            # Keep the file handle open for the duration of the multipart request so
            # large media never needs an intermediate base64/string representation.
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
                "content_type": content_type,
                "media_kind": media_kind,
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

        payload: dict[str, Any] = {
            "ok": True,
            "available": True,
            "backend": "comfyui",
            "status": "staged",
            "source_file_id": file_id,
            "content_type": content_type,
            "media_kind": media_kind,
            "comfyui": result,
            "comfyui_input": {
                "name": returned_name,
                "subfolder": returned_subfolder,
                "type": returned_type,
            },
            "workflow_input_value": workflow_value,
        }
        if media_kind == "image":
            payload["workflow_load_image_value"] = workflow_value
            payload["next_step"] = (
                "For standard LoadImage, set LoadImage.inputs.image to "
                "workflow_load_image_value. Do not pass the original URL or MCP file_id."
            )
        else:
            payload["next_step"] = (
                "The file now exists in ComfyUI's input namespace. Before wiring it into "
                "a workflow, inspect the installed audio/video/custom loader with "
                "list_loaded_nodes/get_node_definition and use workflow_input_value only "
                "for a parameter that expects an input-file filename/path."
            )
        return payload

    @mcp.tool()
    async def comfy_upload_cached_media(
        file_id: str,
        overwrite: bool = False,
        subfolder: str = "",
    ) -> dict[str, Any]:
        """Stage a cached image, audio, video, or other file into ComfyUI `/input`.

        This is the generic cache -> ComfyUI staging route. The file is streamed
        server-side as multipart/form-data and never passes through the AI/client
        as base64. The returned `workflow_input_value` is the ComfyUI input-relative
        filename/path.

        For images, `workflow_load_image_value` is also returned and can be used
        directly in standard `LoadImage.inputs.image`. For audio/video/other media,
        staging does NOT imply that every node accepts the file: inspect the installed
        loader node with `list_loaded_nodes` and `get_node_definition`, then use
        `workflow_input_value` only where that node expects an input filename/path.
        """
        return await _stage_cached_file(
            file_id,
            overwrite=overwrite,
            subfolder=subfolder,
        )

    @mcp.tool()
    async def comfy_upload_cached_image(
        file_id: str,
        overwrite: bool = False,
        subfolder: str = "",
    ) -> dict[str, Any]:
        """Backward-compatible image-only cache -> ComfyUI adapter.

        Prefer `comfy_upload_cached_media` for new code. This legacy tool remains
        available because standard ComfyUI `LoadImage` has a well-defined filename
        contract. It rejects non-image MIME types and returns
        `workflow_load_image_value` for `LoadImage.inputs.image`.
        """
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
                    "comfy_upload_cached_image only accepts image files. Use "
                    "comfy_upload_cached_media(file_id) to stage audio/video/other media, "
                    "then inspect the installed ComfyUI loader node before wiring the path."
                ),
                "endpoint": server_module.COMFY_URL,
                "source_file_id": file_id,
                "content_type": content_type,
            }
        result = await _stage_cached_file(
            file_id,
            overwrite=overwrite,
            subfolder=subfolder,
        )
        if result.get("ok"):
            result["status"] = "uploaded"
        return result
