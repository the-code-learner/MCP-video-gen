from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .download_utils import download_verified
from .model_registry import (
    DEFAULT_INSTALL_HEADROOM_BYTES,
    GIB,
    MODEL_CATALOG,
    ModelChoice,
    get_choice,
    public_catalog,
)
from .qwen_runtime import QWEN_RUNTIME_ESTIMATE_BYTES, QwenWorker, install_qwen_runtime
from .runtime_resources import cache_reclaim_plan, gpu_snapshot, ram_snapshot, storage_snapshot


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def selection_file(data_root: Path) -> Path:
    return data_root / "models" / "registry" / "selection.json"


def load_selection(data_root: Path) -> dict[str, str]:
    path = selection_file(data_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_selection(data_root: Path, selection: dict[str, str]) -> None:
    path = selection_file(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(selection, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def whisper_path(data_root: Path, profile: str) -> Path:
    filenames = {
        "light": "ggml-small-q5_1.bin",
        "optimal": "ggml-large-v3-turbo-q5_0.bin",
    }
    try:
        return data_root / "models" / "whisper" / filenames[profile]
    except KeyError as exc:
        raise ValueError("Whisper profile must be light or optimal") from exc


def qwen_path(data_root: Path, profile: str) -> Path:
    if profile not in MODEL_CATALOG["qwen3-tts"]:
        raise ValueError("Qwen profile must be light or optimal")
    return data_root / "qwen3-tts" / "models" / profile


def _whisper_marker(path: Path) -> Path:
    return path.with_name(path.name + ".video-mcp.json")


def _read_marker(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _file_matches_marker(path: Path, *, expected_sha256: str, marker: dict[str, Any], key: str) -> bool:
    entry = marker.get(key)
    if not isinstance(entry, dict) or not path.is_file():
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return (
        str(entry.get("sha256", "")) == expected_sha256
        and int(entry.get("size_bytes", -1)) == size
        and size > 0
    )


def is_installed(data_root: Path, family: str, profile: str) -> bool:
    choice = get_choice(family, profile)
    if family == "whisper":
        path = whisper_path(data_root, profile)
        marker = _read_marker(_whisper_marker(path))
        return (
            marker.get("family") == family
            and marker.get("profile") == profile
            and marker.get("source_sha256") == choice.sha256
            and _file_matches_marker(path, expected_sha256=choice.sha256, marker=marker, key="file")
        )
    if family == "qwen3-tts":
        root = qwen_path(data_root, profile)
        marker = _read_marker(root / ".video-mcp-model.json")
        if marker.get("family") != family or marker.get("profile") != profile:
            return False
        if marker.get("revision") != choice.revision:
            return False
        if not _file_matches_marker(
            root / choice.main_file,
            expected_sha256=choice.main_file_sha256,
            marker=marker,
            key=choice.main_file,
        ):
            return False
        for relative_path, expected_sha in choice.verification_files:
            if not _file_matches_marker(
                root / relative_path,
                expected_sha256=expected_sha,
                marker=marker,
                key=relative_path,
            ):
                return False
        return True
    return False


async def verify_model(data_root: Path, family: str, profile: str) -> dict[str, Any]:
    choice = get_choice(family, profile)
    checks: list[dict[str, Any]] = []
    if family == "whisper":
        path = whisper_path(data_root, profile)
        if not path.is_file():
            return {"verified": False, "family": family, "profile": profile, "reason": "not_installed"}
        actual = await asyncio.to_thread(sha256_file, path)
        checks.append({
            "path": str(path),
            "expected_sha256": choice.sha256,
            "actual_sha256": actual,
            "ok": actual == choice.sha256,
        })
    elif family == "qwen3-tts":
        root = qwen_path(data_root, profile)
        files = ((choice.main_file, choice.main_file_sha256),) + choice.verification_files
        for relative_path, expected in files:
            path = root / relative_path
            if not path.is_file():
                checks.append({
                    "path": str(path),
                    "expected_sha256": expected,
                    "actual_sha256": "",
                    "ok": False,
                    "reason": "missing",
                })
                continue
            actual = await asyncio.to_thread(sha256_file, path)
            checks.append({
                "path": str(path),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "ok": actual == expected,
            })
    else:
        raise ValueError(f"Unsupported model family: {family}")
    return {
        "verified": bool(checks) and all(row["ok"] for row in checks),
        "family": family,
        "profile": profile,
        "checks": checks,
    }


def install_need_bytes(data_root: Path, family: str, profile: str, worker: QwenWorker) -> int:
    choice = get_choice(family, profile)
    need = 0 if is_installed(data_root, family, profile) else choice.install_bytes
    if family == "qwen3-tts" and not worker.runtime_installed():
        need += QWEN_RUNTIME_ESTIMATE_BYTES
    return need


def recommend(data_root: Path, family: str, worker: QwenWorker) -> dict[str, Any]:
    if family not in MODEL_CATALOG:
        raise ValueError(f"Unknown model family: {family}")
    storage = storage_snapshot(data_root, include_breakdown=False)
    if not storage.get("available"):
        return {
            "family": family,
            "recommended_profile": None,
            "reason": "storage_unavailable",
            "storage": storage,
        }
    ram = ram_snapshot()
    gpu = gpu_snapshot(worker.metrics())
    free = int(storage["free_bytes"])
    available_ram = int(ram["host_visible"].get("available_bytes") or 0)
    gpu_free = max((int(device["free_bytes"]) for device in gpu.get("devices", [])), default=0)
    choices: dict[str, Any] = {}
    for profile, choice in MODEL_CATALOG[family].items():
        required = install_need_bytes(data_root, family, profile, worker)
        disk_ok = free >= required + DEFAULT_INSTALL_HEADROOM_BYTES
        if family == "qwen3-tts":
            ram_hint = 8 * GIB if profile == "light" else 12 * GIB
            vram_hint = 4 * GIB if profile == "light" else 7 * GIB
        else:
            ram_hint = 2 * GIB if profile == "light" else 4 * GIB
            vram_hint = 0
        choices[profile] = {
            **choice.public(),
            "installed": is_installed(data_root, family, profile),
            "additional_install_bytes": required,
            "additional_install_gib": round(required / GIB, 2),
            "disk_ok_with_2gib_headroom": disk_ok,
            "ram_recommendation_ok": available_ram >= ram_hint if available_ram else None,
            "vram_recommendation_ok": (
                gpu_free >= vram_hint
                if vram_hint and gpu.get("available")
                else (None if vram_hint else True)
            ),
            "ram_hint_gib": round(ram_hint / GIB, 1),
            "vram_hint_gib": round(vram_hint / GIB, 1),
        }
    optimal = choices.get("optimal", {})
    light = choices.get("light", {})
    if (
        optimal.get("disk_ok_with_2gib_headroom")
        and optimal.get("ram_recommendation_ok") is not False
        and optimal.get("vram_recommendation_ok") is not False
    ):
        recommended = "optimal"
        reason = "disk and current RAM/VRAM headroom support the optimal profile"
    elif light.get("disk_ok_with_2gib_headroom"):
        recommended = "light"
        reason = "light profile better matches current disk/RAM/VRAM headroom"
    else:
        recommended = None
        reason = "insufficient disk headroom for the available profiles"
    shortage = 0
    if recommended is None:
        smallest = min(int(value["additional_install_bytes"]) for value in choices.values())
        shortage = max(0, smallest + DEFAULT_INSTALL_HEADROOM_BYTES - free)
    return {
        "family": family,
        "recommended_profile": recommended,
        "reason": reason,
        "choices": choices,
        "storage": storage,
        "ram": ram,
        "gpu": gpu,
        "cache_reclaim_preview": cache_reclaim_plan(data_root / "exports", shortage) if shortage else None,
        "notes": [
            "RAM/VRAM suitability is advisory because transient usage can change after this check.",
            "VRAM outside the registered Qwen worker is reported as external/unattributed rather than assigned to a named external process.",
            "Cache deletion is never automatic and requires separate explicit user approval.",
        ],
    }


def _write_whisper_marker(path: Path, family: str, profile: str, choice: ModelChoice) -> None:
    marker = {
        "family": family,
        "profile": profile,
        "source": choice.source,
        "source_sha256": choice.sha256,
        "installed_epoch": int(time.time()),
        "file": {"size_bytes": path.stat().st_size, "sha256": choice.sha256},
    }
    _whisper_marker(path).write_text(
        json.dumps(marker, indent=2, sort_keys=True), encoding="utf-8"
    )


async def install_whisper(data_root: Path, profile: str) -> dict[str, Any]:
    choice = get_choice("whisper", profile)
    path = whisper_path(data_root, profile)
    if is_installed(data_root, "whisper", profile):
        return {"installed": True, "changed": False, "path": str(path)}
    result = await download_verified(
        choice.source,
        path,
        expected_sha256=choice.sha256,
        expected_size_bytes=choice.size_bytes,
        max_bytes=choice.size_bytes + 8 * 1024 * 1024,
    )
    _write_whisper_marker(path, "whisper", profile, choice)
    return {"installed": True, "changed": True, **result}


async def install_qwen_model(data_root: Path, worker: QwenWorker, profile: str) -> dict[str, Any]:
    choice = get_choice("qwen3-tts", profile)
    root = qwen_path(data_root, profile)
    if is_installed(data_root, "qwen3-tts", profile):
        return {"installed": True, "changed": False, "path": str(root)}
    await install_qwen_runtime(worker)
    root.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(root, ignore_errors=True)
    env = os.environ.copy()
    env["HF_HOME"] = str(data_root / "qwen3-tts" / "hf-cache")
    code = (
        "from huggingface_hub import snapshot_download;"
        f"snapshot_download(repo_id={choice.repo_id!r}, revision={choice.revision!r}, "
        f"local_dir={str(root)!r})"
    )
    process = await asyncio.create_subprocess_exec(
        str(worker.python),
        "-c",
        code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    out, err = await process.communicate()
    if process.returncode:
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError((err or out).decode(errors="replace")[-12000:])

    verified_entries: dict[str, Any] = {}
    try:
        files = ((choice.main_file, choice.main_file_sha256),) + choice.verification_files
        for relative_path, expected_sha in files:
            path = root / relative_path
            if not path.is_file():
                raise RuntimeError(f"Qwen snapshot missing expected file: {relative_path}")
            actual = await asyncio.to_thread(sha256_file, path)
            if actual != expected_sha:
                raise RuntimeError(f"Qwen SHA-256 mismatch for {relative_path}: {actual}")
            verified_entries[relative_path] = {
                "size_bytes": path.stat().st_size,
                "sha256": actual,
            }
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise

    marker = {
        "family": "qwen3-tts",
        "profile": profile,
        "repo_id": choice.repo_id,
        "revision": choice.revision,
        "installed_epoch": int(time.time()),
        **verified_entries,
    }
    (root / ".video-mcp-model.json").write_text(
        json.dumps(marker, indent=2, sort_keys=True), encoding="utf-8"
    )
    return {
        "installed": True,
        "changed": True,
        "path": str(root),
        "revision": choice.revision,
    }


def select_model(data_root: Path, family: str, profile: str) -> dict[str, Any]:
    get_choice(family, profile)
    if not is_installed(data_root, family, profile):
        raise RuntimeError(f"{family}/{profile} is not installed")
    selection = load_selection(data_root)
    if family == "whisper":
        root = data_root / "models" / "whisper"
        selected = root / "selected.bin"
        tiny = root / "ggml-tiny-q5_1.bin"
        target = whisper_path(data_root, profile)
        if selected.exists() and not selected.is_symlink() and not tiny.exists():
            selected.replace(tiny)
        selected.unlink(missing_ok=True)
        selected.symlink_to(target.name)
        selection[family] = profile
        save_selection(data_root, selection)
        return {
            "family": family,
            "selected_profile": profile,
            "selected_path": str(selected),
            "target": str(target),
        }
    selection[family] = profile
    save_selection(data_root, selection)
    return {"family": family, "selected_profile": profile}


def remove_model(
    data_root: Path,
    worker: QwenWorker,
    family: str,
    profile: str,
    *,
    confirm: bool,
) -> dict[str, Any]:
    get_choice(family, profile)
    path = whisper_path(data_root, profile) if family == "whisper" else qwen_path(data_root, profile)
    exists = path.is_file() if family == "whisper" else path.is_dir()
    if not exists:
        return {"removed": False, "reason": "not_installed"}
    if not confirm:
        return {"removed": False, "approval_required": True, "path": str(path)}
    selection = load_selection(data_root)
    if selection.get(family) == profile:
        raise RuntimeError("Cannot remove the selected model; select another profile first")
    if family == "qwen3-tts":
        metrics = worker.metrics()
        if metrics.get("busy"):
            raise RuntimeError(
                "Cannot remove a Qwen model during active inference. Wait for completion or explicitly call release_runtime_resources(aggressive=true) if the user requested interruption."
            )
        loaded_path = str(metrics.get("model_path") or "")
        if loaded_path and Path(loaded_path).resolve() == path.resolve():
            release = worker.release(False)
            if release.get("action") == "worker_busy_no_change":
                raise RuntimeError("Qwen worker became busy; model removal was not performed")
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
        _whisper_marker(path).unlink(missing_ok=True)
    return {"removed": True, "family": family, "profile": profile, "path": str(path)}


def catalog_with_status(data_root: Path, family: str = "") -> dict[str, Any]:
    result = public_catalog(family)
    for fam, profiles in result["families"].items():
        for profile, entry in profiles.items():
            entry["installed"] = is_installed(data_root, fam, profile)
    return result
