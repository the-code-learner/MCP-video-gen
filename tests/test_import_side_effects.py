import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_import(module: str, data_root: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["VIDEO_MCP_DATA_ROOT"] = str(data_root)
    subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        check=True,
        env=env,
        cwd=ROOT,
    )


def test_server_import_does_not_create_runtime_data_root(tmp_path):
    data_root = tmp_path / "server-runtime-data"
    run_import("video_mcp.server", data_root)
    assert not data_root.exists()


def test_full_entrypoint_import_does_not_create_runtime_data_root(tmp_path):
    data_root = tmp_path / "entrypoint-runtime-data"
    run_import("video_mcp.entrypoint", data_root)
    assert not data_root.exists()
