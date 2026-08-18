from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .advanced_common import MediaContext, safe_name


def _word_spans_from_whisper_json(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert whisper.cpp full-JSON token timestamps into word-like spans.

    For languages/tokenizations that expose whitespace boundaries, adjacent
    subword/punctuation tokens are grouped into words. If a segment contains
    no whitespace-bearing tokens (common for CJK), each timestamped token is
    returned as one unit rather than collapsing the whole segment.
    """
    result: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(data.get("transcription", [])):
        tokens = []
        for token in segment.get("tokens", []):
            text = str(token.get("text", ""))
            offsets = token.get("offsets") or {}
            start_ms = offsets.get("from")
            end_ms = offsets.get("to")
            if not text or start_ms is None or end_ms is None:
                continue
            try:
                start_ms = int(start_ms)
                end_ms = int(end_ms)
            except (TypeError, ValueError):
                continue
            if end_ms < start_ms:
                continue
            tokens.append(
                {
                    "text": text,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "probability": token.get("p"),
                }
            )

        if not tokens:
            continue

        has_whitespace_boundaries = any(
            any(ch.isspace() for ch in token["text"]) for token in tokens
        )

        if not has_whitespace_boundaries:
            for token in tokens:
                text = token["text"].strip()
                if not text:
                    continue
                result.append(
                    {
                        "text": text,
                        "start": token["start_ms"] / 1000.0,
                        "end": token["end_ms"] / 1000.0,
                        "probability": token["probability"],
                        "token_count": 1,
                        "segment_index": segment_index,
                        "segmentation": "token-fallback",
                    }
                )
            continue

        current: dict[str, Any] | None = None
        probabilities: list[float] = []

        def flush() -> None:
            nonlocal current, probabilities
            if current is None:
                return
            current["text"] = current["text"].strip()
            if current["text"]:
                current["probability"] = (
                    sum(probabilities) / len(probabilities) if probabilities else None
                )
                result.append(current)
            current = None
            probabilities = []

        for token in tokens:
            raw = token["text"]
            starts_new = bool(raw and raw[0].isspace())
            cleaned = raw.strip()
            if not cleaned:
                continue
            if starts_new and current is not None:
                flush()
            if current is None:
                current = {
                    "text": cleaned,
                    "start": token["start_ms"] / 1000.0,
                    "end": token["end_ms"] / 1000.0,
                    "token_count": 1,
                    "segment_index": segment_index,
                    "segmentation": "whitespace-word",
                }
            else:
                current["text"] += cleaned
                current["end"] = token["end_ms"] / 1000.0
                current["token_count"] += 1
            try:
                p = float(token["probability"])
            except (TypeError, ValueError):
                pass
            else:
                probabilities.append(p)
        flush()
    return result


def register(mcp: Any, c: MediaContext) -> None:
    @mcp.tool()
    async def subtitle_retime(
        file_id: str,
        time_scale: float = 1.0,
        shift_ms: int = 0,
        output_filename: str = "retimed.srt",
    ) -> dict[str, Any]:
        """Scale all subtitle timestamps and optionally shift them in milliseconds.

        `time_scale=2.0` places every cue at twice its original timestamp;
        `time_scale=0.5` compresses timing by half. A positive `shift_ms`
        delays all cues and a negative value advances them.
        """
        import pysubs2

        if not 0.01 <= time_scale <= 100:
            raise ValueError("time_scale must be between 0.01 and 100")
        subs = pysubs2.load(str(c.cached(file_id)))
        for event in subs.events:
            start = max(0, int(round(event.start * time_scale + shift_ms)))
            end = max(start + 1, int(round(event.end * time_scale + shift_ms)))
            event.start = start
            event.end = end
        oid, out = c.target(safe_name(output_filename, "retimed.srt"))
        subs.save(str(out))
        return c.file_meta(
            oid,
            out,
            "pysubs2.retime",
            source_file_id=file_id,
            time_scale=time_scale,
            shift_ms=shift_ms,
            cue_count=len(subs.events),
        )

    whisper_binary = Path(
        os.getenv(
            "WHISPER_CPP_BINARY",
            str(c.data_root / "tooling/whisper.cpp/current/build/bin/whisper-cli"),
        )
    ).resolve()
    whisper_model = Path(
        os.getenv(
            "WHISPER_MODEL_PATH",
            str(c.data_root / "models/whisper/ggml-tiny-q5_1.bin"),
        )
    ).resolve()

    @mcp.tool()
    async def transcribe_words(
        file_id: str,
        language: str = "auto",
        translate_to_english: bool = False,
        word_threshold: float = 0.01,
        output_filename: str = "word-timestamps.json",
    ) -> dict[str, Any]:
        """Transcribe media and return word-like timestamp spans from whisper.cpp.

        whisper.cpp full JSON provides per-token millisecond offsets. Tokens are
        grouped on whitespace boundaries when available; no-space scripts fall
        back to one timestamped token per returned unit.
        """
        if not whisper_binary.is_file() or not whisper_model.is_file():
            raise RuntimeError("whisper.cpp binary/model is not prepared")

        src = c.cached(file_id)
        work = Path(tempfile.mkdtemp(prefix="whw-", dir=c.tmp))
        try:
            wav = work / "input.wav"
            prefix = work / "words"
            await c.command(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(src),
                    "-vn",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(wav),
                ],
                c.ffmpeg_timeout,
            )
            args = [
                str(whisper_binary),
                "-m",
                str(whisper_model),
                "-f",
                str(wav),
                "-ojf",
                "-sow",
                "-wt",
                str(max(0.0, min(1.0, word_threshold))),
                "-of",
                str(prefix),
                "-np",
            ]
            if language != "auto":
                args += ["-l", language]
            if translate_to_english:
                args += ["-tr"]
            await c.command(args, c.ffmpeg_timeout)

            json_path = Path(str(prefix) + ".json")
            if not json_path.is_file():
                raise RuntimeError("whisper.cpp did not produce full JSON")
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            words = _word_spans_from_whisper_json(raw)
            payload = {
                "source_file_id": file_id,
                "language": (raw.get("result") or {}).get("language", language),
                "translate_to_english": translate_to_english,
                "word_threshold": word_threshold,
                "words": words,
            }
            oid, out = c.target(safe_name(output_filename, "word-timestamps.json"))
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return {
                **payload,
                "cached_json": c.file_meta(
                    oid,
                    out,
                    "whisper.cpp.words",
                    source_file_id=file_id,
                    word_count=len(words),
                ),
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)
