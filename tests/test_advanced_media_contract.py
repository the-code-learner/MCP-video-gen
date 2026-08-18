from pathlib import Path

from video_mcp.advanced_common import atempo_chain

ROOT = Path(__file__).resolve().parents[1]


def test_atempo_chain_stays_inside_ffmpeg_limits():
    values = [float(part.split("=", 1)[1]) for part in atempo_chain(8.0).split(",")]
    assert values
    assert all(0.5 <= value <= 2.0 for value in values)


def test_expected_advanced_tools_are_registered_in_source():
    source = "\n".join(
        (ROOT / "src" / "video_mcp" / name).read_text()
        for name in ("advanced_ffmpeg.py", "advanced_editing.py", "advanced_audio.py")
    )
    expected = {
        "detect_silence", "detect_black_frames", "detect_freeze", "analyze_loudness",
        "normalize_loudness", "crop_detect", "detect_interlacing", "extract_contact_sheet",
        "compare_ssim_psnr", "safe_crop", "loop_video", "reverse_video", "speed_ramp",
        "frame_similarity", "motion_score", "select_best_frame", "detect_duplicate_frames",
        "extract_keyframes", "make_storyboard", "detect_scenes", "split_scenes",
        "scene_thumbnails", "subtitle_create", "subtitle_shift", "subtitle_convert",
        "subtitle_style_ass", "subtitle_burn", "timeline_create", "timeline_inspect",
        "timeline_add_clip", "timeline_move_clip", "timeline_add_transition",
        "timeline_add_marker", "timeline_export", "detect_beats", "detect_tempo",
        "detect_onsets", "detect_pitch", "denoise_voice", "detect_speech_segments",
        "whisper_info", "transcribe", "transcribe_to_subtitles", "piper_info",
        "piper_import_voice_file", "tts_local",
    }
    missing = sorted(name for name in expected if f"def {name}(" not in source)
    assert not missing, missing


def test_ai_assets_are_checksum_pinned_and_excluded_tools_stay_out():
    prepare = (ROOT / "scripts" / "prepare_media_tools.sh").read_text()
    assert "SILERO_VAD_MODEL_SHA256" in prepare
    assert "WHISPER_MODEL_SHA256" in prepare

    # Check the runtime implementation only. The regression test itself names
    # excluded tools, so scanning the whole repository would self-match.
    runtime_root = ROOT / "src" / "video_mcp"
    runtime_source = "\n".join(
        path.read_text(errors="replace") for path in runtime_root.rglob("*.py")
    )
    assert "Real-ESRGAN" not in runtime_source
    assert "RIFE-ncnn" not in runtime_source
