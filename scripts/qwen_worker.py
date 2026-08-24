#!/usr/bin/env python3
from __future__ import annotations

import gc
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

_MODEL = None
_MODEL_PATH = ""
_TORCH = None


def _rss_bytes() -> int:
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return 0


def _visible_gpu_uuid() -> str:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return ""
    try:
        run = subprocess.run(
            [binary, "--query-gpu=uuid", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    rows = [row.strip() for row in run.stdout.splitlines() if row.strip()]
    # The Compose contract exposes one GPU. When more than one is visible in a
    # nonstandard deployment, do not guess which physical UUID backs cuda:0.
    return rows[0] if len(rows) == 1 else ""


def _metrics() -> dict[str, Any]:
    result: dict[str, Any] = {
        "rss_bytes": _rss_bytes(),
        "model_loaded": _MODEL is not None,
        "model_path": _MODEL_PATH,
        "cuda_available": False,
        "cuda_device_index": None,
        "cuda_device_uuid": "",
        "cuda_allocated_bytes": 0,
        "cuda_reserved_bytes": 0,
        "cuda_global_free_bytes": 0,
        "cuda_global_total_bytes": 0,
    }
    torch = _TORCH
    if torch is None:
        try:
            import torch as imported_torch
            torch = imported_torch
        except Exception:
            return result
    try:
        result["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info(0)
            result.update({
                "cuda_device_index": 0,
                "cuda_device_uuid": _visible_gpu_uuid(),
                "cuda_allocated_bytes": int(torch.cuda.memory_allocated(0)),
                "cuda_reserved_bytes": int(torch.cuda.memory_reserved(0)),
                "cuda_global_free_bytes": int(free),
                "cuda_global_total_bytes": int(total),
            })
    except Exception:
        pass
    return result


def _unload() -> None:
    global _MODEL, _MODEL_PATH
    _MODEL = None
    _MODEL_PATH = ""
    gc.collect()
    torch = _TORCH
    if torch is not None:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                try:
                    torch.cuda.ipc_collect()
                except Exception:
                    pass
        except Exception:
            pass
    gc.collect()


def _load_model(model_path: str):
    global _MODEL, _MODEL_PATH, _TORCH
    path = str(Path(model_path).resolve())
    if _MODEL is not None and _MODEL_PATH == path:
        return _MODEL
    _unload()

    import torch
    from qwen_tts import Qwen3TTSModel

    _TORCH = torch
    device_env = os.getenv("QWEN_TTS_DEVICE", "auto").strip().lower()
    device = "cuda:0" if device_env == "auto" and torch.cuda.is_available() else (
        "cpu" if device_env == "auto" else device_env
    )
    dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
    attention = os.getenv("QWEN_TTS_ATTENTION", "sdpa").strip() or "sdpa"

    _MODEL = Qwen3TTSModel.from_pretrained(
        path,
        device_map=device,
        dtype=dtype,
        attn_implementation=attention,
        local_files_only=True,
    )
    _MODEL_PATH = path
    return _MODEL


def _handle(payload: dict[str, Any]) -> dict[str, Any]:
    cmd = str(payload.get("cmd", "")).strip().lower()
    if cmd == "ping":
        return {"ok": True, "pong": True, "metrics": _metrics()}
    if cmd == "metrics":
        return {"ok": True, "metrics": _metrics()}
    if cmd == "unload":
        _unload()
        return {"ok": True, "unloaded": True, "metrics": _metrics()}
    if cmd == "shutdown":
        _unload()
        return {"ok": True, "shutdown": True, "metrics": _metrics(), "_shutdown": True}
    if cmd == "synthesize":
        model_path = str(payload.get("model_path", ""))
        text = str(payload.get("text", "")).strip()
        language = str(payload.get("language", "")).strip()
        ref_audio = str(payload.get("ref_audio", ""))
        ref_text = str(payload.get("ref_text", "")).strip()
        output_raw = str(payload.get("output_path", ""))
        if not model_path or not text or not language or not ref_audio or not ref_text or not output_raw:
            raise ValueError("model_path, text, language, ref_audio, ref_text and output_path are required")
        output_path = Path(output_raw).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        model = _load_model(model_path)
        configured_max = max(256, min(8192, int(os.getenv("QWEN_TTS_MAX_NEW_TOKENS", "4096"))))
        requested_max = int(payload.get("max_new_tokens") or configured_max)
        max_new_tokens = max(256, min(configured_max, requested_max))
        wavs, sample_rate = model.generate_voice_clone(
            text=text,
            language=language,
            ref_audio=ref_audio,
            ref_text=ref_text,
            max_new_tokens=max_new_tokens,
        )
        if not wavs:
            raise RuntimeError("Qwen3-TTS returned no waveform")
        import soundfile as sf
        sf.write(str(output_path), wavs[0], int(sample_rate))
        return {
            "ok": True,
            "output_path": str(output_path),
            "sample_rate": int(sample_rate),
            "max_new_tokens": max_new_tokens,
            "metrics": _metrics(),
        }
    raise ValueError(f"unknown command: {cmd}")


def main() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        shutdown = False
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("request must be a JSON object")
            response = _handle(payload)
            shutdown = bool(response.pop("_shutdown", False))
        except Exception as exc:
            response = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=8),
                "metrics": _metrics(),
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if shutdown:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
