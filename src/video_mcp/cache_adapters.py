from __future__ import annotations

import base64
from pathlib import Path
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
        """Upload a cached image directly to ComfyUI without routing bytes through the AI/client.

        This is useful for Blender renders and other MCP-generated images that
        should become ComfyUI input assets. If ComfyUI is absent/unreachable,
        the tool returns a structured availability result rather than an MCP
        tool error.
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
        try:
            result = await server_module.upload_image_base64(
                source.name.split("__", 1)[-1],
                base64.b64encode(source.read_bytes()).decode("ascii"),
                overwrite,
                subfolder,
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
        return {
            "ok": True,
            "available": True,
            "backend": "comfyui",
            "status": "uploaded",
            "source_file_id": file_id,
            "comfyui": result,
        }
