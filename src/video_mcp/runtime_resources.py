from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

MIB = 1024 * 1024
GIB = 1024 * MIB


def _gib(value: int | float) -> float:
    return round(float(value) / GIB, 3)


def _read_int(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if raw == "max":
            return None
        return int(raw)
    except (OSError, ValueError):
        return None


def _directory_bytes(path: Path, *, max_files: int = 200_000) -> tuple[int, int, bool]:
    if not path.exists():
        return 0, 0, False
    total = 0
    count = 0
    truncated = False
    try:
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            count += 1
            if count > max_files:
                truncated = True
                break
            try:
                total += item.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total, min(count, max_files), truncated


def storage_snapshot(data_root: Path, *, include_breakdown: bool = True) -> dict[str, Any]:
    data_root = data_root.resolve()
    if not data_root.exists():
        return {
            "available": False,
            "path": str(data_root),
            "reason": "data_root_not_created_yet",
        }
    usage = shutil.disk_usage(data_root)
    result: dict[str, Any] = {
        "available": True,
        "path": str(data_root),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "total_gib": _gib(usage.total),
        "used_gib": _gib(usage.used),
        "free_gib": _gib(usage.free),
        "used_percent": round((usage.used / usage.total * 100) if usage.total else 0.0, 1),
        "scope": "filesystem backing the /data Docker volume; it may be one host partition rather than every disk in the VM",
    }
    if include_breakdown:
        names = {
            "media_cache": data_root / "exports",
            "models": data_root / "models",
            "tooling": data_root / "tooling",
            "piper": data_root / "piper",
            "qwen3_tts": data_root / "qwen3-tts",
            "hyperframes": data_root / "hyperframes",
            "timelines": data_root / "timelines",
            "tmp": data_root / "tmp",
        }
        breakdown = {}
        for name, path in names.items():
            size, count, truncated = _directory_bytes(path)
            breakdown[name] = {
                "path": str(path),
                "bytes": size,
                "gib": _gib(size),
                "files_scanned": count,
                "scan_truncated": truncated,
            }
        result["breakdown"] = breakdown
    return result


def _parse_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            parts = raw.strip().split()
            if not parts:
                continue
            try:
                value = int(parts[0]) * 1024 if len(parts) > 1 and parts[1].lower() == "kb" else int(parts[0])
            except ValueError:
                continue
            values[key] = value
    except OSError:
        pass
    return values


def _cgroup_memory() -> dict[str, Any]:
    v2 = Path("/sys/fs/cgroup")
    current = _read_int(v2 / "memory.current")
    if current is not None:
        limit = _read_int(v2 / "memory.max")
        return {
            "version": 2,
            "current_bytes": current,
            "limit_bytes": limit,
            "source": "/sys/fs/cgroup/memory.current",
        }
    v1 = Path("/sys/fs/cgroup/memory")
    current = _read_int(v1 / "memory.usage_in_bytes")
    if current is not None:
        limit = _read_int(v1 / "memory.limit_in_bytes")
        return {
            "version": 1,
            "current_bytes": current,
            "limit_bytes": limit,
            "source": "/sys/fs/cgroup/memory/memory.usage_in_bytes",
        }
    return {"version": None, "current_bytes": None, "limit_bytes": None, "source": "unavailable"}


def ram_snapshot() -> dict[str, Any]:
    mem = _parse_meminfo()
    total = int(mem.get("MemTotal", 0))
    available = int(mem.get("MemAvailable", mem.get("MemFree", 0)))
    host_used = max(0, total - available)
    cgroup = _cgroup_memory()
    container_used = cgroup.get("current_bytes")
    external_estimate = None
    if isinstance(container_used, int):
        external_estimate = max(0, host_used - min(host_used, container_used))
    ratio = (host_used / total) if total else 0.0
    pressure = "unknown" if not total else (
        "high" if ratio >= 0.90 else "elevated" if ratio >= 0.75 else "normal"
    )
    return {
        "host_visible": {
            "total_bytes": total,
            "used_estimate_bytes": host_used,
            "available_bytes": available,
            "total_gib": _gib(total),
            "used_estimate_gib": _gib(host_used),
            "available_gib": _gib(available),
            "pressure": pressure,
        },
        "video_gen_container": {
            **cgroup,
            "current_gib": _gib(container_used or 0) if isinstance(container_used, int) else None,
            "limit_gib": _gib(cgroup["limit_bytes"]) if isinstance(cgroup.get("limit_bytes"), int) else None,
            "attribution": "cgroup-accounted Video Gen container memory",
        },
        "external_or_shared_estimate": {
            "bytes": external_estimate,
            "gib": _gib(external_estimate) if isinstance(external_estimate, int) else None,
            "attribution": "estimated remainder visible on the VM; includes other containers/processes plus shared/kernel/page-cache accounting",
        },
    }


def _nvidia_smi_rows() -> list[dict[str, Any]]:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return []
    query = "index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu"
    try:
        run = subprocess.run(
            [binary, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[dict[str, Any]] = []
    for line in run.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 7:
            continue
        try:
            index = int(parts[0])
            total_mib = float(parts[3])
            used_mib = float(parts[4])
            free_mib = float(parts[5])
            util = float(parts[6])
        except ValueError:
            continue
        rows.append({
            "index": index,
            "uuid": parts[1],
            "name": parts[2],
            "total_bytes": int(total_mib * MIB),
            "used_bytes": int(used_mib * MIB),
            "free_bytes": int(free_mib * MIB),
            "utilization_percent": util,
        })
    return rows


def gpu_snapshot(worker_metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    worker_metrics = worker_metrics or {}
    rows = _nvidia_smi_rows()
    owned_reserved = int(worker_metrics.get("cuda_reserved_bytes") or 0)
    owned_allocated = int(worker_metrics.get("cuda_allocated_bytes") or 0)
    worker_uuid = str(worker_metrics.get("cuda_device_uuid") or "")

    target_uuid = ""
    attribution_confidence = "none"
    if owned_reserved > 0 and worker_uuid and any(row["uuid"] == worker_uuid for row in rows):
        target_uuid = worker_uuid
        attribution_confidence = "uuid_match"
    elif owned_reserved > 0 and len(rows) == 1:
        # The supported Compose contract exposes exactly one GPU. This fallback
        # remains safe even when nvidia-smi preserves a non-zero physical index.
        target_uuid = str(rows[0]["uuid"])
        attribution_confidence = "single_visible_gpu"

    devices = []
    for row in rows:
        used = int(row["used_bytes"])
        is_target = bool(target_uuid and row["uuid"] == target_uuid)
        attributed = min(used, owned_reserved) if is_target else 0
        external = max(0, used - attributed)
        ratio = used / row["total_bytes"] if row["total_bytes"] else 0.0
        devices.append({
            **row,
            "total_gib": _gib(row["total_bytes"]),
            "used_gib": _gib(used),
            "free_gib": _gib(row["free_bytes"]),
            "pressure": "high" if ratio >= 0.90 else "elevated" if ratio >= 0.75 else "normal",
            "video_gen_worker": {
                "allocated_bytes": owned_allocated if is_target else 0,
                "reserved_bytes": attributed,
                "reserved_gib": _gib(attributed),
                "attribution": "reported by the registered Video Gen Qwen PyTorch worker",
                "confidence": attribution_confidence if is_target else "none",
            },
            "external_or_unattributed_estimate": {
                "bytes": external,
                "gib": _gib(external),
                "attribution": "GPU memory not safely attributable to the registered Video Gen worker; may belong to other VM workloads or CUDA/driver/context overhead",
            },
        })
    return {
        "available": bool(devices),
        "devices": devices,
        "ownership_policy": "Video Gen never kills PIDs discovered through NVIDIA telemetry. Only child workers explicitly started and registered by Video Gen may be stopped.",
        "pid_mapping": "host process attribution is intentionally not attempted because host PID namespace and Docker socket are not exposed",
    }


def _cache_rows(exports: Path) -> list[dict[str, Any]]:
    rows = []
    if not exports.is_dir():
        return rows
    for path in exports.glob("*__*"):
        if not path.is_file():
            continue
        file_id = path.name.split("__", 1)[0]
        meta_path = exports / f"{file_id}.json"
        meta: dict[str, Any] = {}
        try:
            if meta_path.is_file():
                raw = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    meta = raw
        except Exception:
            pass
        retention = meta.get("retention") if isinstance(meta.get("retention"), dict) else {}
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append({
            "file_id": file_id,
            "filename": path.name.split("__", 1)[1],
            "path": path,
            "meta_path": meta_path,
            "size_bytes": int(stat.st_size),
            "created_epoch": int(meta.get("created_epoch", stat.st_mtime)),
            "pinned": bool(retention.get("pinned", False)),
        })
    return rows


def cache_reclaim_plan(exports: Path, required_bytes: int, reserve_bytes: int = 0) -> dict[str, Any]:
    target = max(0, int(required_bytes)) + max(0, int(reserve_bytes))
    rows = sorted(
        (row for row in _cache_rows(exports) if not row["pinned"]),
        key=lambda row: (row["created_epoch"], row["file_id"]),
    )
    selected = []
    total = 0
    for row in rows:
        if total >= target:
            break
        total += row["size_bytes"]
        selected.append({k: v for k, v in row.items() if k not in {"path", "meta_path"}})
    return {
        "required_bytes": target,
        "required_gib": _gib(target),
        "candidate_bytes": total,
        "candidate_gib": _gib(total),
        "sufficient": total >= target,
        "candidates": selected,
        "approval_required": True,
        "note": "Preview only. No file is deleted until an explicit confirmed delete call is made after user approval.",
    }


def register_resource_tools(
    mcp: Any,
    *,
    data_root: Path,
    exports: Path,
    worker_metrics: Callable[[], dict[str, Any]],
    release_worker: Callable[[bool], dict[str, Any]],
) -> None:
    @mcp.tool()
    async def storage_info(include_breakdown: bool = True) -> dict[str, Any]:
        """Report free/used space on the filesystem backing /data and optionally its Video Gen directory breakdown."""
        return storage_snapshot(data_root, include_breakdown=include_breakdown)

    @mcp.tool()
    async def runtime_resources() -> dict[str, Any]:
        """Report RAM/GPU pressure and safely distinguish Video Gen-owned usage from external/shared VM usage where measurable."""
        metrics = worker_metrics()
        return {
            "ram": ram_snapshot(),
            "gpu": gpu_snapshot(metrics),
            "video_gen_worker": metrics,
            "isolation": {
                "host_pid_namespace": False,
                "docker_socket": False,
                "external_processes_mutable": False,
            },
        }

    @mcp.tool()
    async def release_runtime_resources(aggressive: bool = False) -> dict[str, Any]:
        """Free RAM/VRAM owned by Video Gen. Aggressive mode may stop only registered child workers; the MCP container/main process and external processes are never terminated."""
        before = {"ram": ram_snapshot(), "gpu": gpu_snapshot(worker_metrics())}
        released = release_worker(bool(aggressive))
        after = {"ram": ram_snapshot(), "gpu": gpu_snapshot(worker_metrics())}
        return {"aggressive": bool(aggressive), "released": released, "before": before, "after": after}

    @mcp.tool()
    async def cache_reclaim_preview(required_gib: float, reserve_gib: float = 0.0) -> dict[str, Any]:
        """Preview oldest unpinned Video Gen cache files that could reclaim requested space. This never deletes files."""
        if required_gib < 0 or reserve_gib < 0:
            raise ValueError("required_gib and reserve_gib must be >= 0")
        return cache_reclaim_plan(exports, int(required_gib * GIB), int(reserve_gib * GIB))

    @mcp.tool()
    async def cache_reclaim_files(file_ids: list[str], confirm: bool = False) -> dict[str, Any]:
        """Delete explicitly selected unpinned Video Gen cache files only after confirm=true. A client must preview and obtain user approval first."""
        if not confirm:
            return {
                "deleted_count": 0,
                "deleted_bytes": 0,
                "approval_required": True,
                "message": "No deletion performed. Re-run with confirm=true only after explicit user approval.",
            }
        wanted = set(file_ids)
        if not wanted:
            raise ValueError("file_ids is required")
        rows = {row["file_id"]: row for row in _cache_rows(exports)}
        missing = sorted(wanted - set(rows))
        if missing:
            raise ValueError(f"Unknown cache file_id(s): {', '.join(missing[:10])}")
        pinned = sorted(file_id for file_id in wanted if rows[file_id]["pinned"])
        if pinned:
            raise ValueError(f"Pinned cache files cannot be reclaimed: {', '.join(pinned[:10])}")
        deleted = []
        total = 0
        for file_id in sorted(wanted):
            row = rows[file_id]
            try:
                row["path"].unlink(missing_ok=True)
                row["meta_path"].unlink(missing_ok=True)
            except OSError as exc:
                deleted.append({"file_id": file_id, "error": str(exc)})
                continue
            total += row["size_bytes"]
            deleted.append({
                "file_id": file_id,
                "filename": row["filename"],
                "size_bytes": row["size_bytes"],
            })
        return {
            "deleted_count": sum(1 for row in deleted if "error" not in row),
            "deleted_bytes": total,
            "deleted_gib": _gib(total),
            "results": deleted,
            "confirmed": True,
            "completed_epoch": int(time.time()),
        }
