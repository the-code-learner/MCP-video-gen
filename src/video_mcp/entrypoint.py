from __future__ import annotations

import uvicorn

from . import server
from .advanced_tools import register_advanced_tools
from .external_backends import register_external_backend_tools
from .file_transfer import register_file_transfer_tools

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

register_external_backend_tools(
    server.mcp,
    server_module=server,
    cached=server.cached,
    target=server.target,
    file_meta=server.file_meta,
)


if __name__ == "__main__":
    server.drop_privileges()
    uvicorn.run(
        server.app,
        host="0.0.0.0",
        port=server.LISTEN_PORT,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
