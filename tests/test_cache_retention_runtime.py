from __future__ import annotations

import json
import time
from pathlib import Path

from video_mcp.cache_retention import CacheRetentionManager, CacheRetentionPolicy


def make_file(exports: Path, file_id: str, name: str, size: int, created_epoch: int) -> Path:
    exports.mkdir(parents=True, exist_ok=True)
    path = exports / f"{file_id}__{name}"
    path.write_bytes(b"x" * size)
    (exports / f"{file_id}.json").write_text(
        json.dumps(
            {
                "file_id": file_id,
                "filename": name,
                "size_bytes": size,
                "created_epoch": created_epoch,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_default_policy_is_non_destructive_when_variables_are_absent(monkeypatch, tmp_path):
    for name in (
        "CACHE_CLEANUP_ENABLED",
        "CACHE_RETENTION_DAYS",
        "CACHE_MAX_SIZE_GB",
        "CACHE_CLEANUP_INTERVAL_HOURS",
    ):
        monkeypatch.delenv(name, raising=False)

    exports = tmp_path / "exports"
    old = make_file(exports, "a" * 32, "old.png", 10, int(time.time()) - 365 * 86400)
    manager = CacheRetentionManager(exports)

    assert manager.policy.cleanup_enabled is False
    assert manager.policy.retention_days == 0
    assert manager.policy.max_size_gb == 0
    assert manager.start_worker() is False
    result = manager.cleanup(dry_run=False)
    assert result["deleted_count"] == 0
    assert old.is_file()


def test_age_cleanup_never_deletes_pinned_file(tmp_path):
    now = int(time.time())
    exports = tmp_path / "exports"
    old_unpinned = make_file(exports, "a" * 32, "old-a.png", 10, now - 40 * 86400)
    old_pinned = make_file(exports, "b" * 32, "old-b.png", 20, now - 50 * 86400)
    new_file = make_file(exports, "c" * 32, "new.png", 30, now - 2 * 86400)

    manager = CacheRetentionManager(
        exports,
        CacheRetentionPolicy(cleanup_enabled=True, retention_days=30, max_size_gb=0),
    )
    manager.set_pinned("b" * 32, pinned=True, note="keep project reference")

    preview = manager.cleanup(dry_run=True)
    assert preview["candidate_count"] == 1
    assert preview["candidates"][0]["file_id"] == "a" * 32

    result = manager.cleanup(dry_run=False)
    assert result["deleted_count"] == 1
    assert not old_unpinned.exists()
    assert old_pinned.exists()
    assert new_file.exists()
    assert json.loads((exports / ("b" * 32 + ".json")).read_text())["retention"]["pinned"] is True


def test_size_limit_evicts_oldest_unpinned_first(tmp_path):
    now = int(time.time())
    exports = tmp_path / "exports"
    first = make_file(exports, "1" * 32, "first.bin", 70, now - 300)
    second = make_file(exports, "2" * 32, "second.bin", 70, now - 200)
    third = make_file(exports, "3" * 32, "third.bin", 70, now - 100)

    # 150 bytes expressed as GiB: total is 210, so exactly the oldest 70-byte
    # artifact must be removed to get below the limit.
    max_size_gb = 150 / (1024**3)
    manager = CacheRetentionManager(
        exports,
        CacheRetentionPolicy(cleanup_enabled=True, retention_days=0, max_size_gb=max_size_gb),
    )

    preview = manager.cleanup(dry_run=True)
    assert [item["file_id"] for item in preview["candidates"]] == ["1" * 32]
    manager.cleanup(dry_run=False)
    assert not first.exists()
    assert second.exists()
    assert third.exists()


def test_pin_and_unpin_are_persistent_metadata_operations(tmp_path):
    exports = tmp_path / "exports"
    make_file(exports, "d" * 32, "asset.blend", 5, int(time.time()))
    manager = CacheRetentionManager(exports)

    pinned = manager.set_pinned("d" * 32, pinned=True, note="final scene")
    assert pinned["pinned"] is True
    status = manager.status()
    assert status["pinned_count"] == 1

    unpinned = manager.set_pinned("d" * 32, pinned=False)
    assert unpinned["pinned"] is False
    meta = json.loads((exports / ("d" * 32 + ".json")).read_text())
    assert meta["retention"]["pinned"] is False
    assert "note" not in meta["retention"]
