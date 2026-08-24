from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_mcp.model_registry import (
    LARGE_ARTIFACT_THRESHOLD_BYTES,
    MODEL_CATALOG,
    ModelChoice,
    validate_model_catalog,
)
from video_mcp.piper_catalog import PIPER_VOICE_CATALOG
from video_mcp.qwen_runtime import QWEN_RUNTIME_ESTIMATE_BYTES
from video_mcp import openverse_music, runtime_resources


ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "video-mcp.yml"
START = ROOT / "scripts" / "start.sh"
PREPARE = ROOT / "scripts" / "prepare_media_tools.sh"


def test_every_large_model_family_has_light_and_optimal_and_is_not_preloaded() -> None:
    validate_model_catalog()
    for family, choices in MODEL_CATALOG.items():
        if any(choice.install_bytes > LARGE_ARTIFACT_THRESHOLD_BYTES for choice in choices.values()):
            assert {"light", "optimal"}.issubset(choices), family
            assert len(choices) >= 2
            assert all(choice.public()["preloaded"] is False for choice in choices.values())


def test_catalog_validation_rejects_single_large_choice() -> None:
    broken = {
        "broken": {
            "light": ModelChoice(
                family="broken",
                profile="light",
                label="broken",
                backend="test",
                size_bytes=LARGE_ARTIFACT_THRESHOLD_BYTES + 1,
                install_bytes=LARGE_ARTIFACT_THRESHOLD_BYTES + 1,
                description="test",
                source="https://example.com/model.bin",
                sha256="0" * 64,
            )
        }
    }
    with pytest.raises(ValueError, match="at least two choices"):
        validate_model_catalog(broken)


def test_qwen_hf_models_use_full_commit_pins_and_verify_large_tokenizer() -> None:
    for choice in MODEL_CATALOG["qwen3-tts"].values():
        assert len(choice.revision) == 40
        assert choice.main_file_sha256 and len(choice.main_file_sha256) == 64
        assert (
            "speech_tokenizer/model.safetensors",
            "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258",
        ) in choice.verification_files
    assert QWEN_RUNTIME_ESTIMATE_BYTES >= 8 * 1024 * 1024 * 1024


def test_gpu_is_shared_without_host_pid_or_docker_socket() -> None:
    text = STACK.read_text(encoding="utf-8")
    assert "driver: nvidia" in text
    assert "count: 1" in text
    assert "capabilities: [gpu]" in text
    assert 'NVIDIA_DRIVER_CAPABILITIES: "${NVIDIA_DRIVER_CAPABILITIES:-compute,utility}"' in text
    assert "pid: host" not in text
    assert "/var/run/docker.sock" not in text
    assert "privileged: true" not in text


def test_whisper_uses_switchable_selected_path() -> None:
    text = STACK.read_text(encoding="utf-8")
    assert 'WHISPER_MODEL_PATH: "/data/models/whisper/selected.bin"' in text
    assert 'WHISPER_MODEL_NAME: "${WHISPER_MODEL_NAME:-tiny-q5_1}"' in text


def test_whisper_bootstrap_and_runtime_selection_are_separate_on_restart() -> None:
    text = PREPARE.read_text(encoding="utf-8")
    assert 'BOOTSTRAP_MODEL_PATH="${WHISPER_BOOTSTRAP_MODEL_PATH:-$DATA_ROOT/models/whisper/ggml-$MODEL_NAME.bin}"' in text
    assert 'SELECTED_MODEL_PATH="${WHISPER_MODEL_PATH:-$BOOTSTRAP_MODEL_PATH}"' in text
    assert 'download_verified "$MODEL_URL" "$MODEL_SHA" "$BOOTSTRAP_MODEL_PATH"' in text
    assert 'download_verified "$MODEL_URL" "$MODEL_SHA" "$SELECTED_MODEL_PATH"' not in text
    assert 'if [ ! -e "$SELECTED_MODEL_PATH" ]; then' in text


def test_startup_does_not_install_qwen_or_large_models() -> None:
    text = START.read_text(encoding="utf-8")
    assert "pip install qwen-tts" not in text
    assert "Qwen/Qwen3-TTS" not in text
    assert "ggml-small-q5_1.bin" not in text
    assert "ggml-large-v3-turbo-q5_0.bin" not in text
    assert '"$DATA_ROOT/qwen3-tts/models"' in text
    assert '"$DATA_ROOT/qwen3-tts/voices"' in text


def test_qwen_runtime_install_disables_pip_cache_and_can_interrupt_busy_child() -> None:
    text = (ROOT / "src" / "video_mcp" / "qwen_runtime.py").read_text(encoding="utf-8")
    assert 'env["PIP_NO_CACHE_DIR"] = "1"' in text
    assert '"--no-cache-dir"' in text
    assert "self._lock.acquire(timeout=1.0)" in text
    assert "interrupt_registered_qwen_child_worker" in text
    assert "select.select" in text
    assert "QWEN_TTS_REQUEST_TIMEOUT_SEC" in text


def test_cache_reclaim_preview_never_selects_pinned_files(tmp_path: Path) -> None:
    exports = tmp_path / "exports"
    exports.mkdir()
    pinned_id = "a" * 32
    free_id = "b" * 32
    pinned = exports / f"{pinned_id}__pinned.wav"
    free = exports / f"{free_id}__free.wav"
    pinned.write_bytes(b"x" * 100)
    free.write_bytes(b"y" * 200)
    (exports / f"{pinned_id}.json").write_text(
        json.dumps({"created_epoch": 1, "retention": {"pinned": True}}),
        encoding="utf-8",
    )
    (exports / f"{free_id}.json").write_text(json.dumps({"created_epoch": 2}), encoding="utf-8")
    plan = runtime_resources.cache_reclaim_plan(exports, 50)
    ids = [row["file_id"] for row in plan["candidates"]]
    assert pinned_id not in ids
    assert free_id in ids
    assert plan["approval_required"] is True


def test_gpu_attribution_matches_worker_uuid_even_for_nonzero_physical_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime_resources,
        "_nvidia_smi_rows",
        lambda: [{
            "index": 3,
            "uuid": "GPU-test",
            "name": "Test GPU",
            "total_bytes": 10_000,
            "used_bytes": 8_000,
            "free_bytes": 2_000,
            "utilization_percent": 50.0,
        }],
    )
    result = runtime_resources.gpu_snapshot({
        "cuda_device_uuid": "GPU-test",
        "cuda_allocated_bytes": 2_000,
        "cuda_reserved_bytes": 3_000,
    })
    device = result["devices"][0]
    assert device["video_gen_worker"]["reserved_bytes"] == 3_000
    assert device["video_gen_worker"]["confidence"] == "uuid_match"
    assert device["external_or_unattributed_estimate"]["bytes"] == 5_000
    assert result["ownership_policy"].startswith("Video Gen never kills")


def test_gpu_attribution_single_visible_device_fallback_does_not_require_index_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime_resources,
        "_nvidia_smi_rows",
        lambda: [{
            "index": 7,
            "uuid": "GPU-only",
            "name": "Only GPU",
            "total_bytes": 20_000,
            "used_bytes": 10_000,
            "free_bytes": 10_000,
            "utilization_percent": 20.0,
        }],
    )
    result = runtime_resources.gpu_snapshot({
        "cuda_allocated_bytes": 1_000,
        "cuda_reserved_bytes": 2_000,
    })
    assert result["devices"][0]["video_gen_worker"]["reserved_bytes"] == 2_000
    assert result["devices"][0]["video_gen_worker"]["confidence"] == "single_visible_gpu"


def test_openverse_duration_is_filtered_in_milliseconds() -> None:
    assert openverse_music._duration_matches(90_000, 60, 120) is True
    assert openverse_music._duration_matches(59_999, 60, 120) is False
    assert openverse_music._duration_matches(120_001, 60, 120) is False
    assert openverse_music._duration_matches(None, 60, 120) is False
    text = (ROOT / "src" / "video_mcp" / "openverse_music.py").read_text(encoding="utf-8")
    assert 'params["length"]' not in text
    assert '"duration_ms"' in text


def test_qwen_worker_is_the_only_explicit_process_termination_target() -> None:
    text = (ROOT / "src" / "video_mcp" / "qwen_runtime.py").read_text(encoding="utf-8")
    assert "proc.terminate()" in text
    assert "proc.kill()" in text
    for forbidden in ("killall", "pkill", "nvidia-smi --gpu-reset", "docker.sock", "pid: host"):
        assert forbidden not in text


def test_qwen_worker_stderr_is_drained_in_background() -> None:
    text = (ROOT / "src" / "video_mcp" / "qwen_runtime.py").read_text(encoding="utf-8")
    assert "_drain_stderr" in text
    assert "daemon=True" in text
    assert "collections.deque(maxlen=80)" in text


def test_piper_catalog_is_it_en_curated_and_below_large_threshold() -> None:
    assert {row["language"] for row in PIPER_VOICE_CATALOG} == {"it", "en"}
    assert all(0 < row["size_bytes"] <= LARGE_ARTIFACT_THRESHOLD_BYTES for row in PIPER_VOICE_CATALOG)
    assert all(len(row["model_sha256"]) == 64 for row in PIPER_VOICE_CATALOG)
    assert all(row["model_url"].startswith("https://") for row in PIPER_VOICE_CATALOG)


def test_piper_convenience_install_cannot_bypass_large_artifact_policy() -> None:
    text = (ROOT / "src" / "video_mcp" / "local_ai_v31.py").read_text(encoding="utf-8")
    assert "max_bytes=100 * MIB" in text
