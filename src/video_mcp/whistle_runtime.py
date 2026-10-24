from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from typing import Any

WHISTLE_PACKAGE = "cactus-needle"
WHISTLE_PACKAGE_SPEC = "cactus-needle==3.2.8"
WHISTLE_LANGUAGES = {"auto", "en", "de", "fr", "es", "it", "nl", "pl"}


def whistle_home(data_root: Path) -> Path:
    return data_root / "whistle-runtime"


def whistle_cache_root(data_root: Path) -> Path:
    return whistle_home(data_root) / ".cache" / "cactus-needle"


def whistle_status(data_root: Path) -> dict[str, Any]:
    try:
        version = importlib.metadata.version(WHISTLE_PACKAGE)
        runtime_installed = True
    except importlib.metadata.PackageNotFoundError:
        version = ""
        runtime_installed = False

    root = whistle_cache_root(data_root)
    model_files = []
    if root.is_dir():
        model_files = sorted(
            str(path.relative_to(root))
            for path in root.rglob("*.cact")
            if path.is_file()
        )

    return {
        "runtime_installed": runtime_installed,
        "package": WHISTLE_PACKAGE,
        "package_spec": WHISTLE_PACKAGE_SPEC,
        "version": version,
        "cache_root": str(root),
        "model_cached": any(Path(path).name == "whistle.cact" for path in model_files),
        "model_files": model_files[:100],
        "languages": sorted(WHISTLE_LANGUAGES - {"auto"}),
        "word_timestamps": True,
        "keyword_biasing": True,
    }


def _whistle_env(data_root: Path) -> dict[str, str]:
    home = whistle_home(data_root)
    home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONUNBUFFERED"] = "1"
    return env


async def _run_python(
    data_root: Path,
    code: str,
    args: list[str],
    timeout: int,
) -> tuple[str, str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_whistle_env(data_root),
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("Whistle transcription timed out")
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    if process.returncode:
        raise RuntimeError((err or out or "Whistle process failed")[-12000:])
    return out, err


async def prepare_whistle(data_root: Path, timeout: int) -> dict[str, Any]:
    code = (
        "import needle;"
        "needle.Whistle();"
        "print('ready')"
    )
    await _run_python(data_root, code, [], timeout)
    return whistle_status(data_root)


async def run_whistle_transcription(
    audio_path: Path,
    data_root: Path,
    *,
    language: str = "auto",
    keywords: list[str] | None = None,
    word_timestamps: bool = False,
    timeout: int = 1800,
) -> dict[str, Any]:
    if language not in WHISTLE_LANGUAGES:
        raise ValueError(
            "Whistle language must be auto, en, de, fr, es, it, nl or pl"
        )
    clean_keywords = [str(value).strip() for value in (keywords or []) if str(value).strip()]
    code = """
import json
import needle
import sys

audio = sys.argv[1]
language = None if sys.argv[2] == "auto" else sys.argv[2]
keywords = json.loads(sys.argv[3])
word_timestamps = sys.argv[4] == "1"
result = needle.transcribe(
    audio,
    language=language,
    keywords=keywords or None,
    word_timestamps=word_timestamps,
)
print(json.dumps(result, ensure_ascii=False))
""".strip()
    out, _ = await _run_python(
        data_root,
        code,
        [
            str(audio_path),
            language,
            json.dumps(clean_keywords, ensure_ascii=False),
            "1" if word_timestamps else "0",
        ],
        timeout,
    )
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Whistle produced no JSON output")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Whistle returned invalid JSON: {lines[-1][-2000:]}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Whistle returned an unexpected result")
    return result


def normalize_whistle_words(
    result: dict[str, Any],
    *,
    probability_threshold: float = 0.0,
) -> list[dict[str, Any]]:
    threshold = max(0.0, min(1.0, float(probability_threshold)))
    normalized = []
    for item in result.get("words") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("word", "")).strip()
        if not text:
            continue
        probability = float(item.get("probability", 1.0))
        if probability < threshold:
            continue
        normalized.append(
            {
                "text": text,
                "start": float(item.get("start", 0.0)),
                "end": float(item.get("end", 0.0)),
                "probability": probability,
            }
        )
    return normalized


def whistle_subtitle_text(words: list[dict[str, Any]], output_format: str) -> str:
    if output_format not in {"srt", "vtt"}:
        raise ValueError("output_format must be srt or vtt")

    cues: list[tuple[float, float, str]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        current.append(word)
        duration = float(current[-1]["end"]) - float(current[0]["start"])
        terminal = str(word["text"]).endswith((".", "!", "?", ":", ";"))
        if terminal or len(current) >= 10 or duration >= 4.0:
            cues.append(
                (
                    float(current[0]["start"]),
                    float(current[-1]["end"]),
                    " ".join(str(item["text"]) for item in current),
                )
            )
            current = []
    if current:
        cues.append(
            (
                float(current[0]["start"]),
                float(current[-1]["end"]),
                " ".join(str(item["text"]) for item in current),
            )
        )

    def stamp(seconds: float, *, vtt: bool) -> str:
        milliseconds = max(0, int(round(seconds * 1000)))
        hours, rem = divmod(milliseconds, 3_600_000)
        minutes, rem = divmod(rem, 60_000)
        secs, ms = divmod(rem, 1000)
        sep = "." if vtt else ","
        return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"

    lines: list[str] = []
    if output_format == "vtt":
        lines.extend(["WEBVTT", ""])
    for index, (start, end, text) in enumerate(cues, 1):
        if output_format == "srt":
            lines.append(str(index))
        lines.append(
            f"{stamp(start, vtt=output_format == 'vtt')} --> "
            f"{stamp(end, vtt=output_format == 'vtt')}"
        )
        lines.extend([text, ""])
    return "\n".join(lines)
