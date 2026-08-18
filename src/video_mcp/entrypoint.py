from __future__ import annotations

import uvicorn

from . import server
from .advanced_tools import register_advanced_tools
from .build_status import register_build_status_tool
from .cache_adapters import register_cache_adapters
from .external_backends import register_external_backend_tools
from .file_ingress import install_comfy_workflow_input_guard, register_file_ingress_tools
from .file_transfer import register_file_transfer_tools
from .native_handoff import register_native_file_handoff

register_file_transfer_tools(
    server.mcp,
    exports=server.EXPORTS,
    tmp=server.TMP,
    cached=server.cached,
    target=server.target,
    file_meta=server.file_meta,
    max_upload_mb=server.MAX_UPLOAD_MB,
    max_inline_mb=server.MAX_INLINE_MB,
)

register_native_file_handoff(
    server.mcp,
    cached=server.cached,
    public_base_url=server.PUBLIC_BASE_URL,
)

register_file_ingress_tools(
    server.mcp,
    tmp=server.TMP,
    target=server.target,
    file_meta=server.file_meta,
    max_upload_mb=server.MAX_UPLOAD_MB,
)

register_advanced_tools(
    server.mcp,
    data_root=server.DATA_ROOT,
    exports=server.EXPORTS,
    tmp=server.TMP,
    cached=server.cached,
    target=server.target,
    file_meta=server.file_meta,
    command=server.command,
    ffmpeg_timeout=server.FFMPEG_TIMEOUT,
)

# Standard ComfyUI LoadImage accepts a ComfyUI input filename, not an MCP
# file_id or URL. Install this before external-backend wrappers capture the
# submit_workflow implementation so AI-generated workflows get a clear routing
# error instead of sending unusable links to ComfyUI.
install_comfy_workflow_input_guard(server)

register_external_backend_tools(
    server.mcp,
    server_module=server,
    cached=server.cached,
    target=server.target,
    file_meta=server.file_meta,
)

register_cache_adapters(
    server.mcp,
    server_module=server,
    cached=server.cached,
)

# Register last so build_status sees the complete runtime tool set, including
# itself, and can report the same count a connected MCP client should discover.
register_build_status_tool(server.mcp, server_module=server)


if __name__ == "__main__":
    server.drop_privileges()
    uvicorn.run(
        server.app,
        host="0.0.0.0",
        port=server.LISTEN_PORT,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
