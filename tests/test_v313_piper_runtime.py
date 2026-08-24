from __future__ import annotations

from pathlib import Path

from video_mcp import piper_runtime


ROOT = Path(__file__).resolve().parents[1]


def test_piper_runtime_auto_enables_when_installed(monkeypatch) -> None:
    monkeypatch.delenv("PIPER_ENABLED", raising=False)
    monkeypatch.setattr(piper_runtime, "_package_version", lambda: "1.6.0")
    monkeypatch.setattr(piper_runtime, "_module_available", lambda: True)

    status = piper_runtime.piper_runtime_status()

    assert status["runtime_installed"] is True
    assert status["runtime_version"] == "1.6.0"
    assert status["runtime_spec"] == "piper-tts==1.6.0"
    assert status["enabled"] is True
    assert status["enabled_source"] == "auto_runtime"


def test_piper_runtime_auto_stays_disabled_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("PIPER_ENABLED", raising=False)
    monkeypatch.setattr(piper_runtime, "_package_version", lambda: "")
    monkeypatch.setattr(piper_runtime, "_module_available", lambda: False)

    status = piper_runtime.piper_runtime_status()

    assert status["runtime_installed"] is False
    assert status["enabled"] is False
    assert status["enabled_source"] == "auto_runtime"


def test_piper_explicit_false_overrides_installed_runtime(monkeypatch) -> None:
    monkeypatch.setenv("PIPER_ENABLED", "false")
    monkeypatch.setattr(piper_runtime, "_package_version", lambda: "1.6.0")
    monkeypatch.setattr(piper_runtime, "_module_available", lambda: True)

    status = piper_runtime.piper_runtime_status()

    assert status["runtime_installed"] is True
    assert status["enabled"] is False
    assert status["enabled_source"] == "environment"


def test_piper_invalid_env_value_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("PIPER_ENABLED", "maybe")
    monkeypatch.setattr(piper_runtime, "_package_version", lambda: "1.6.0")
    monkeypatch.setattr(piper_runtime, "_module_available", lambda: True)

    status = piper_runtime.piper_runtime_status()

    assert status["enabled"] is False
    assert status["enabled_source"] == "invalid_environment_value"


def test_piper_runtime_dependency_and_bootstrap_contract() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    start = (ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")
    advanced = (ROOT / "src" / "video_mcp" / "advanced_audio.py").read_text(encoding="utf-8")

    assert "piper-tts==1.6.0" in requirements.splitlines()
    assert 'importlib.metadata.version("piper-tts")' in start
    assert "PIPER_ENABLED=true" in start
    assert "PIPER_ENABLED=false" in start
    assert "piper_runtime_status" in advanced
    assert "require_piper_enabled" in advanced


def test_v313_does_not_require_yaml_piper_configuration() -> None:
    stack = (ROOT / "video-mcp.yml").read_text(encoding="utf-8")
    assert "PIPER_ENABLED" not in stack
