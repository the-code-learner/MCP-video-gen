from __future__ import annotations

import importlib.util
import os
from importlib.metadata import PackageNotFoundError, version
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


def piper_runtime_status() -> dict[str, Any]:
    package_version = _package_version()
    module_available = _module_available()
    runtime_installed = bool(package_version and module_available)

    configured = os.getenv("PIPER_ENABLED")
    normalized = configured.strip().lower() if configured is not None else "auto"
    if normalized in {"", "auto"}:
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
        raise RuntimeError("Piper is disabled by PIPER_ENABLED")
    return status
