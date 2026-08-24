from __future__ import annotations

from typing import Any


PIPER_VOICE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "key": "it_IT-paola-medium",
        "language": "it",
        "locale": "it_IT",
        "speaker": "paola",
        "quality": "medium",
        "size_bytes": 63_511_038,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/paola/medium/it_IT-paola-medium.onnx",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/paola/medium/it_IT-paola-medium.onnx.json",
        "model_sha256": "6fc918b5a0ea6137382833dddfa567bffbe6a5060c02043c87192ee59c04210c",
        "source": "rhasspy/piper-voices",
        "license_note": "Repository-level metadata is MIT; users must still review the individual voice/model card and dataset terms.",
    },
    {
        "key": "en_US-lessac-medium",
        "language": "en",
        "locale": "en_US",
        "speaker": "lessac",
        "quality": "medium",
        "size_bytes": 63_201_294,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
        "model_sha256": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
        "source": "rhasspy/piper-voices",
        "license_note": "Repository-level metadata is MIT; users must still review the individual voice/model card and dataset terms.",
    },
)


def register_piper_catalog_tools(mcp: Any) -> None:
    @mcp.tool()
    async def piper_voice_catalog(language: str = "") -> dict[str, Any]:
        """Return curated lightweight Piper voice choices for Italian/English. These voices are below the 100 MiB large-artifact threshold and are not preloaded."""
        normalized = language.lower().strip()
        if normalized and normalized not in {"it", "en"}:
            raise ValueError("language must be empty, it or en")
        voices = [dict(row) for row in PIPER_VOICE_CATALOG if not normalized or row["language"] == normalized]
        for row in voices:
            row["preloaded"] = False
            row["install_with"] = {
                "tool": "piper_voice_install",
                "voice_name": row["key"],
                "model_url": row["model_url"],
                "config_url": row["config_url"],
                "expected_model_sha256": row["model_sha256"],
                "confirm": True,
            }
        return {
            "voices": voices,
            "count": len(voices),
            "large_artifact_threshold_mib": 100,
            "note": "No voice is downloaded by this catalog call. piper_voice_install remains approval-gated and capped at 100 MiB per ONNX voice.",
        }
