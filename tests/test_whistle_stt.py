from pathlib import Path

from video_mcp.whistle_runtime import normalize_whistle_words, whistle_subtitle_text

ROOT = Path(__file__).resolve().parents[1]


def test_whistle_runtime_is_pinned_and_generic_stt_routes_to_whistle() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "cactus-needle==3.2.8" in requirements

    audio = (ROOT / "src" / "video_mcp" / "advanced_audio.py").read_text(encoding="utf-8")
    completion = (ROOT / "src" / "video_mcp" / "advanced_completion.py").read_text(encoding="utf-8")

    assert "async def whistle_info(" in audio
    assert "async def whistle_prepare(" in audio
    assert "run_whistle_transcription(" in audio
    assert '"whistle.transcript"' in audio
    assert '"whistle.subtitles"' in audio
    assert '"whistle.words"' in completion
    assert "keywords: list[str] | None = None" in completion


def test_whistle_word_normalization_and_threshold() -> None:
    result = {
        "text": "Lascia il manganello",
        "language": "it",
        "words": [
            {"word": "Lascia", "start": 0.1, "end": 0.4, "probability": 0.99},
            {"word": "il", "start": 0.41, "end": 0.5, "probability": 0.98},
            {"word": "manganello", "start": 0.51, "end": 1.0, "probability": 0.97},
            {"word": "rumore", "start": 1.1, "end": 1.3, "probability": 0.2},
        ],
    }
    words = normalize_whistle_words(result, probability_threshold=0.5)
    assert [word["text"] for word in words] == ["Lascia", "il", "manganello"]
    assert words[-1]["start"] == 0.51
    assert words[-1]["probability"] == 0.97


def test_whistle_subtitles_use_native_word_timestamps() -> None:
    words = [
        {"text": "Lascia", "start": 0.1, "end": 0.4, "probability": 0.99},
        {"text": "il", "start": 0.41, "end": 0.5, "probability": 0.98},
        {"text": "manganello.", "start": 0.51, "end": 1.0, "probability": 0.97},
    ]
    srt = whistle_subtitle_text(words, "srt")
    assert "00:00:00,100 --> 00:00:01,000" in srt
    assert "Lascia il manganello." in srt

    vtt = whistle_subtitle_text(words, "vtt")
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.100 --> 00:00:01.000" in vtt
