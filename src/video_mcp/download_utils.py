from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

_REDIRECT_CODES = {301, 302, 303, 307, 308}


async def validate_public_https_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("download URL must use HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("download URL must not contain embedded credentials")
    host = (parsed.hostname or "").rstrip(".")
    if not host:
        raise ValueError("download URL has no host")
    port = parsed.port or 443
    infos = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    addresses = {info[4][0] for info in infos if info and info[4]}
    if not addresses:
        raise ValueError(f"could not resolve download host: {host}")
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not address.is_global:
            raise ValueError(f"download host resolves to non-public address: {address}")
    return host


async def download_verified(
    url: str,
    destination: Path,
    *,
    expected_sha256: str = "",
    expected_size_bytes: int = 0,
    max_bytes: int = 0,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    expected_sha256 = expected_sha256.strip().lower()
    if expected_sha256 and (len(expected_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha256)):
        raise ValueError("expected_sha256 must be a 64-character hexadecimal SHA-256")
    if expected_size_bytes < 0 or max_bytes < 0:
        raise ValueError("size limits must be >= 0")

    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + f".{os.getpid()}.{int(time.time())}.part")
    current = url.strip()
    if not current:
        raise ValueError("download URL is required")
    total = 0
    digest = hashlib.sha256()
    final_host = ""
    content_type = ""
    timeout = httpx.Timeout(max(10, timeout_seconds), connect=min(20, max(10, timeout_seconds)))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for redirect_index in range(6):
                final_host = await validate_public_https_url(current)
                async with client.stream(
                    "GET",
                    current,
                    headers={"User-Agent": "mcp-video-gen-model-manager", "Accept": "*/*"},
                ) as response:
                    if response.status_code in _REDIRECT_CODES:
                        location = response.headers.get("location", "")
                        if not location or redirect_index >= 5:
                            raise ValueError("download redirect limit exceeded")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    content_length = response.headers.get("content-length", "")
                    if content_length.isdigit():
                        length = int(content_length)
                        if max_bytes and length > max_bytes:
                            raise ValueError("download exceeds configured maximum size")
                        if expected_size_bytes and length != expected_size_bytes:
                            raise ValueError(
                                f"download Content-Length mismatch: expected {expected_size_bytes}, got {length}"
                            )
                    with part.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            if not chunk:
                                continue
                            total += len(chunk)
                            if max_bytes and total > max_bytes:
                                raise ValueError("download exceeded configured maximum while streaming")
                            handle.write(chunk)
                            digest.update(chunk)
                    break
            else:  # pragma: no cover
                raise ValueError("download redirect limit exceeded")

        if expected_size_bytes and total != expected_size_bytes:
            raise ValueError(f"download size mismatch: expected {expected_size_bytes}, got {total}")
        actual = digest.hexdigest()
        if expected_sha256 and actual != expected_sha256:
            raise ValueError(f"SHA-256 mismatch: expected {expected_sha256}, got {actual}")
        part.replace(destination)
        return {
            "path": str(destination),
            "size_bytes": total,
            "sha256": actual,
            "source_host": final_host,
            "content_type": content_type,
        }
    except Exception:
        part.unlink(missing_ok=True)
        raise
