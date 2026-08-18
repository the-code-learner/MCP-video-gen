import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_import_does_not_create_runtime_data_root(tmp_path):
    data_root = tmp_path / "runtime-data"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["VIDEO_MCP_DATA_ROOT"] = str(data_root)

    subprocess.run(
        [sys.executable, "-c", "import video_mcp.server"],
        check=True,
        env=env,
        cwd=ROOT,
    )

    assert not data_root.exists()
