from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


class MediaContext:
    def __init__(self, *, data_root: Path, exports: Path, tmp: Path,
                 cached: Callable[[str], Path], target: Callable[[str], tuple[str, Path]],
                 file_meta: Callable[..., dict[str, Any]], command: Callable[[list[str], int], Any],
                 ffmpeg_timeout: int):
        self.data_root = data_root
        self.exports = exports
        self.tmp = tmp
        self.cached = cached
        self.target = target
        self.file_meta = file_meta
        self.command = command
        self.ffmpeg_timeout = ffmpeg_timeout

    async def probe(self, path: Path) -> dict[str, Any]:
        out, _ = await self.command([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
        ], 60)
        return json.loads(out)

    async def duration(self, path: Path) -> float:
        data = await self.probe(path)
        try:
            return float(data.get("format", {}).get("duration", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    async def has_audio(self, path: Path) -> bool:
        data = await self.probe(path)
        return any(s.get("codec_type") == "audio" for s in data.get("streams", []))


def safe_name(name: str, fallback: str) -> str:
    value = Path(name or fallback).name
    if not value:
        raise ValueError("Invalid filename")
    return value


def atempo_chain(speed: float) -> str:
    if speed <= 0:
        raise ValueError("speed must be > 0")
    values: list[float] = []
    while speed > 2:
        values.append(2.0); speed /= 2
    while speed < 0.5:
        values.append(0.5); speed /= 0.5
    values.append(speed)
    return ",".join(f"atempo={x:.8g}" for x in values)
