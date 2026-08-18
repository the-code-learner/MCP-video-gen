from __future__ import annotations

import ctypes.util
import os
import shutil
from pathlib import Path
from typing import Any, Callable

from .advanced_common import MediaContext
from . import advanced_audio, advanced_completion, advanced_editing, advanced_ffmpeg

_REGISTERED = False


def register_advanced_tools(
    mcp: Any,
    *,
    data_root: Path,
    exports: Path,
    tmp: Path,
    cached: Callable[[str], Path],
    target: Callable[[str], tuple[str, Path]],
    file_meta: Callable[..., dict[str, Any]],
    command: Callable[[list[str], int], Any],
    ffmpeg_timeout: int,
) -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True
    c = MediaContext(data_root=data_root, exports=exports, tmp=tmp, cached=cached,
                     target=target, file_meta=file_meta, command=command,
                     ffmpeg_timeout=ffmpeg_timeout)

    @mcp.tool()
    async def advanced_capabilities() -> dict[str, Any]:
        return {
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "aubio": all(bool(shutil.which(x)) for x in ("aubiotrack", "aubioonset", "aubiopitch")),
            "rnnoise": bool(ctypes.util.find_library("rnnoise")),
            "silero_vad_model": Path(os.getenv("SILERO_VAD_MODEL_PATH", str(data_root / "models/silero-vad/silero_vad.onnx"))).is_file(),
            "whisper_cpp": Path(os.getenv("WHISPER_CPP_BINARY", str(data_root / "tooling/whisper.cpp/current/build/bin/whisper-cli"))).is_file(),
            "whisper_model": Path(os.getenv("WHISPER_MODEL_PATH", str(data_root / "models/whisper/ggml-tiny-q5_1.bin"))).is_file(),
            "piper_enabled": os.getenv("PIPER_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        }

    advanced_ffmpeg.register(mcp, c)
    advanced_editing.register(mcp, c)
    advanced_audio.register(mcp, c)
    advanced_completion.register(mcp, c)
