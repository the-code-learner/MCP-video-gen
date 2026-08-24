from __future__ import annotations

from pathlib import Path

from video_mcp import piper_runtime


ROOT = Path(__file__).resolve().parents[1]


def _healthy_runtime(monkeypatch) -> None:
    monkeypatch.setattr(piper_runtime, "_package_version", lambda: "1.6.0")
    monkeypatch.setattr(piper_runtime, "_module_available", lambda: True)


def test_piper_runtime_auto_enables_when_no_policy_exists(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIDEO_MCP_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("PIPER_ENABLED", raising=False)
    _healthy_runtime(monkeypatch)

    status = piper_runtime.piper_runtime_status()

    assert status["runtime_installed"] is True
    assert status["runtime_version"] == "1.6.0"
    assert status["runtime_spec"] == "piper-tts==1.6.0"
    assert status["enabled"] is True
    assert status["enabled_source"] == "auto_runtime"


def test_legacy_environment_false_remains_backward_compatible(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIDEO_MCP_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PIPER_ENABLED", "false")
    _healthy_runtime(monkeypatch)

    status = piper_runtime.piper_runtime_status()

    assert status["runtime_installed"] is True
    assert status["enabled"] is False
    assert status["enabled_source"] == "environment"


def test_piper_persistent_enable_requires_confirmation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIDEO_MCP_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PIPER_ENABLED", "false")
    _healthy_runtime(monkeypatch)

    result = piper_runtime.set_piper_enabled(True, confirm=False)

    assert result["changed"] is False
    assert result["approval_required"] is True
    assert not (tmp_path / "piper" / "runtime-enabled").exists()


def test_piper_persistent_enable_overrides_legacy_env_and_survives(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIDEO_MCP_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PIPER_ENABLED", "false")
    _healthy_runtime(monkeypatch)

    result = piper_runtime.set_piper_enabled(True, confirm=True)

    state = tmp_path / "piper" / "runtime-enabled"
    assert result["changed"] is True
    assert state.read_text(encoding="utf-8") == "true\n"
    assert result["status"]["enabled"] is True
    assert result["status"]["enabled_source"] == "persistent_state"
    assert result["status"]["persistent_value"] is True
    assert piper_runtime.piper_runtime_status()["enabled"] is True


def test_piper_persistent_disable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIDEO_MCP_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PIPER_ENABLED", "true")
    _healthy_runtime(monkeypatch)

    piper_runtime.set_piper_enabled(False, confirm=True)
    status = piper_runtime.piper_runtime_status()

    assert status["enabled"] is False
    assert status["enabled_source"] == "persistent_state"
    assert status["persistent_value"] is False


def test_piper_runtime_dependency_and_bootstrap_contract() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    start = (ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")
    advanced = (ROOT / "src" / "video_mcp" / "advanced_audio.py").read_text(encoding="utf-8")

    assert "piper-tts==1.6.0" in requirements.splitlines()
    assert "PIPER_STATE_FILE" in start
    assert "piper/runtime-enabled" in start
    assert 'importlib.metadata.version("piper-tts")' in start
    assert "piper_runtime_status" in advanced
    assert "require_piper_enabled" in advanced
    assert "piper_runtime_set_enabled" in advanced


def test_v313_preserves_existing_yaml_piper_contract() -> None:
    stack = (ROOT / "video-mcp.yml").read_text(encoding="utf-8")
    assert 'PIPER_ENABLED: "${PIPER_ENABLED:-false}"' in stack
    assert 'PIPER_PACKAGE_SPEC: "${PIPER_PACKAGE_SPEC:-piper-tts}"' in stack
    assert 'PIPER_VOICES_ROOT: "/data/piper/voices"' in stack
