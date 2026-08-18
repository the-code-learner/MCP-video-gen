from __future__ import annotations

import asyncio

from video_mcp.routing_guide import replace_file_transfer_guide


class FakeMCP:
    def __init__(self):
        self.tools = {"file_transfer_guide": lambda: None}

    def remove_tool(self, name: str):
        del self.tools[name]

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


def test_runtime_routing_guide_uses_generic_comfy_media_staging():
    mcp = FakeMCP()
    replace_file_transfer_guide(mcp)
    guide = asyncio.run(mcp.tools["file_transfer_guide"]())
    assert guide["canonical_identifier"] == "file_id inside THIS MCP Video Gen cache"
    route = guide["routes"]["cache_media_to_comfyui_input"]
    assert route["tool"] == "comfy_upload_cached_media(file_id)"
    assert route["result"] == "workflow_input_value"
    assert route["image_result"] == "workflow_load_image_value for standard LoadImage"
    assert "node-specific" in route["audio_video_rule"]
    assert guide["routes"]["cache_image_to_comfyui_input_legacy"]["status"].startswith(
        "backward-compatible"
    )
