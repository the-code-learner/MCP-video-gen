from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

from video_mcp.webgui_system import create_system_webgui_routes


def test_system_webgui_is_read_only_and_reports_safe_telemetry(tmp_path: Path, monkeypatch) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "models").mkdir()

    monkeypatch.setattr(
        "video_mcp.webgui_system.ram_snapshot",
        lambda: {
            "host_visible": {"total_bytes": 1000, "available_bytes": 400, "pressure": "normal"},
            "video_gen_container": {"current_bytes": 200},
            "external_or_shared_estimate": {"bytes": 400},
        },
    )
    monkeypatch.setattr(
        "video_mcp.webgui_system.gpu_snapshot",
        lambda metrics: {
            "available": True,
            "devices": [{
                "index": 2,
                "uuid": "GPU-test",
                "name": "GPU",
                "total_bytes": 10_000,
                "used_bytes": 5_000,
                "free_bytes": 5_000,
                "utilization_percent": 25.0,
                "pressure": "normal",
                "video_gen_worker": {"reserved_bytes": int(metrics.get("cuda_reserved_bytes", 0))},
                "external_or_unattributed_estimate": {"bytes": 4_000},
            }],
        },
    )

    routes = create_system_webgui_routes(
        data_root=data_root,
        worker_metrics=lambda: {
            "running": True,
            "model_loaded": True,
            "rss_bytes": 123,
            "cuda_reserved_bytes": 1000,
        },
        runtime_installed=lambda: True,
    )
    client = TestClient(Starlette(routes=routes))

    page = client.get("/system")
    assert page.status_code == 200
    assert "read-only telemetry" in page.text
    assert "External/unattributed" in page.text

    status = client.get("/api/system")
    assert status.status_code == 200
    body = status.json()
    assert body["qwen_runtime_installed"] is True
    assert body["qwen_worker"]["running"] is True
    assert body["safety"] == {
        "read_only": True,
        "host_pid_namespace": False,
        "docker_socket": False,
        "external_processes_mutable": False,
    }

    assert client.post("/api/system").status_code == 405
    assert client.delete("/api/system").status_code == 405
