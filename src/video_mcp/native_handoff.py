from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Callable

from mcp.types import ResourceLink


MEDIA_RESOURCE_PREFIX = "media://cache/"


def _filename(path: Path) -> str:
    return path.name.split("__", 1)[-1]


def _mime_type(path: Path) -> str:
    return mimetypes.guess_type(_filename(path))[0] or "application/octet-stream"


def _resource_uri(file_id: str) -> str:
    return f"{MEDIA_RESOURCE_PREFIX}{file_id}"


def _preferred_uri(file_id: str, public_base_url: str) -> str:
    base = public_base_url.rstrip("/")
    if base.startswith(("https://", "http://")):
        return f"{base}/files/{file_id}"
    return _resource_uri(file_id)


def register_native_file_handoff(
    mcp: Any,
    *,
    cached: Callable[[str], Path],
    public_base_url: str = "",
) -> None:
    """Register native-reference file handoff for artifacts in the shared cache.

    The preferred output path is a ResourceLink. When PUBLIC_BASE_URL is
    configured, the link points at the streaming /files/{file_id} HTTP route.
    Otherwise it points at the MCP media:// cache resource.

    Binary resources/read remains a compatibility path: the MCP protocol
    represents binary resource contents as a base64 blob. It must not be used
    as the normal transfer path for large media when the HTTP resource link is
    available.
    """

    @mcp.tool()
    async def get_cached_file_resource(file_id: str) -> ResourceLink:
        """Return the preferred native resource/file reference for a cached artifact.

        Use this instead of inline/chunked base64 whenever the client supports
        ResourceLink or direct HTTP file retrieval. The canonical artifact ID
        remains file_id and the bytes are not placed in the tool result.
        """
        path = cached(file_id)
        name = _filename(path)
        return ResourceLink(
            type="resource_link",
            uri=_preferred_uri(file_id, public_base_url),
            name=name,
            title=name,
            mime_type=_mime_type(path),
            size=path.stat().st_size,
        )

    @mcp.resource(
        "media://cache/{file_id}",
        name="Cached media artifact",
        description=(
            "Compatibility MCP resource for a cached artifact. Prefer the HTTPS "
            "ResourceLink returned by get_cached_file_resource for large binary files."
        ),
        mime_type="application/octet-stream",
    )
    async def cached_media_resource(file_id: str) -> bytes:
        """Read a cached artifact through MCP resources/read as a compatibility fallback."""
        return cached(file_id).read_bytes()
