from __future__ import annotations

import importlib.util
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


PIPER_PACKAGE_SPEC = "piper-tts==1.6.0"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _package_version() -> str:
    try:
        return version("piper-tts")
    except PackageNotFoundError:
        return ""


def _module_available() -> bool:
    try:
        return importlib.util.find_spec("piper") is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _state_path() -> Path:
    data_root = Path(os.getenv("VIDEO_MCP_DATA_ROOT", "/data")).resolve()
    return data_root / "piper" / "runtime-enabled"


def _persistent_state() -> tuple[bool | None, str | None]:
    path = _state_path()
    if not path.is_file():
        return None, None
    try:
        raw = path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None, "unreadable"
    if raw in _TRUE_VALUES:
        return True, raw
    if raw in _FALSE_VALUES:
        return False, raw
    return None, raw or "invalid"


def piper_runtime_status() -> dict[str, Any]:
    package_version = _package_version()
    module_available = _module_available()
    runtime_installed = bool(package_version and module_available)

    persistent_enabled, persistent_raw = _persistent_state()
    configured = os.getenv("PIPER_ENABLED")
    normalized = configured.strip().lower() if configured is not None else "auto"

    if persistent_raw is not None:
        if persistent_enabled is None:
            enabled = False
            enabled_source = "invalid_persistent_state"
        else:
            enabled = persistent_enabled
            enabled_source = "persistent_state"
    elif normalized in {"", "auto"}:
        enabled = runtime_installed
        enabled_source = "auto_runtime"
    elif normalized in _TRUE_VALUES:
        enabled = True
        enabled_source = "environment"
    elif normalized in _FALSE_VALUES:
        enabled = False
        enabled_source = "environment"
    else:
        enabled = False
        enabled_source = "invalid_environment_value"

    return {
        "enabled": enabled,
        "enabled_source": enabled_source,
        "runtime_installed": runtime_installed,
        "runtime_version": package_version or None,
        "runtime_spec": PIPER_PACKAGE_SPEC,
        "module_available": module_available,
        "configured_value": configured,
        "persistent_value": persistent_enabled,
        "persistent_state_path": str(_state_path()),
    }


def set_piper_enabled(enabled: bool, *, confirm: bool = False) -> dict[str, Any]:
    current = piper_runtime_status()
    if enabled and not current["runtime_installed"]:
        raise RuntimeError(
            f"Piper runtime is not installed or importable; expected {PIPER_PACKAGE_SPEC}"
        )
    if not confirm:
        return {
            "changed": False,
            "approval_required": True,
            "requested_enabled": bool(enabled),
            "current": current,
        }

    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text("true\n" if enabled else "false\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)

    # Apply the explicit user choice immediately to this MCP process as well as
    # persisting it for the next container/release restart.
    os.environ["PIPER_ENABLED"] = "true" if enabled else "false"
    return {
        "changed": True,
        "approval_required": False,
        "requested_enabled": bool(enabled),
        "status": piper_runtime_status(),
    }


def require_piper_enabled() -> dict[str, Any]:
    status = piper_runtime_status()
    if not status["runtime_installed"]:
        raise RuntimeError(
            f"Piper runtime is not installed or importable; expected {PIPER_PACKAGE_SPEC}"
        )
    if not status["enabled"]:
        if status["enabled_source"] == "invalid_environment_value":
            raise RuntimeError(
                "Piper is disabled because PIPER_ENABLED has an invalid value; use true, false, or auto"
            )
        if status["enabled_source"] == "invalid_persistent_state":
            raise RuntimeError("Piper is disabled because its persistent runtime state is invalid")
        raise RuntimeError("Piper is disabled")
    return status
