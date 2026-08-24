from __future__ import annotations

import asyncio
import collections
import json
import os
import select
import subprocess
import threading
import venv
from pathlib import Path
from typing import Any

QWEN_PACKAGE_SPEC = "qwen-tts==0.1.1"
# Conservative allowance for torch/CUDA/runtime wheels and their installed files.
QWEN_RUNTIME_ESTIMATE_BYTES = 8 * 1024 * 1024 * 1024


class QwenWorker:
    def __init__(self, *, data_root: Path) -> None:
        self.data_root = data_root
        self.runtime_root = data_root / "tooling" / "qwen3-tts"
        self.venv_root = self.runtime_root / "venv"
        self.python = self.venv_root / "bin" / "python"
        self.worker_script = Path(
            os.getenv(
                "QWEN_TTS_WORKER_SCRIPT",
                str(Path(os.getenv("VIDEO_MCP_APP_DIR", "/opt/video-mcp/current")) / "scripts" / "qwen_worker.py"),
            )
        )
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._last_metrics: dict[str, Any] = {}
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=80)
        self._stderr_thread: threading.Thread | None = None

    def runtime_installed(self) -> bool:
        marker = self.runtime_root / ".runtime-spec"
        try:
            return (
                self.python.is_file()
                and marker.is_file()
                and marker.read_text(encoding="utf-8").strip() == QWEN_PACKAGE_SPEC
            )
        except OSError:
            return False

    def _running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def _drain_stderr(self, proc: subprocess.Popen[str]) -> None:
        stream = proc.stderr
        if stream is None:
            return
        try:
            for line in stream:
                self._stderr_tail.append(line.rstrip()[-1500:])
        except Exception:
            pass

    def _start(self) -> None:
        if self._running():
            return
        if not self.runtime_installed():
            raise RuntimeError("Qwen3-TTS runtime is not installed. Install a qwen3-tts model first.")
        if not self.worker_script.is_file():
            raise RuntimeError(f"Qwen worker script not found: {self.worker_script}")
        env = os.environ.copy()
        env.setdefault("HF_HUB_OFFLINE", "1")
        env.setdefault("TRANSFORMERS_OFFLINE", "1")
        env["HF_HOME"] = str(self.data_root / "qwen3-tts" / "hf-cache")
        self._stderr_tail.clear()
        proc = subprocess.Popen(
            [str(self.python), "-u", str(self.worker_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._proc = proc
        thread = threading.Thread(
            target=self._drain_stderr,
            args=(proc,),
            daemon=True,
            name="qwen-stderr",
        )
        self._stderr_thread = thread
        thread.start()

    def _stderr_summary(self) -> str:
        return "\n".join(self._stderr_tail)[-6000:]

    @staticmethod
    def _request_timeout(payload: dict[str, Any]) -> int:
        cmd = str(payload.get("cmd", "")).lower()
        if cmd in {"metrics", "unload", "shutdown", "ping"}:
            default = 30
        else:
            default = 1800
        try:
            configured = int(os.getenv("QWEN_TTS_REQUEST_TIMEOUT_SEC", str(default)))
        except ValueError:
            configured = default
        return max(5, min(configured, 7200))

    @staticmethod
    def _stop_proc(proc: subprocess.Popen[str], *, graceful_seconds: float = 0.0) -> None:
        if proc.poll() is not None:
            return
        if graceful_seconds > 0:
            try:
                proc.wait(timeout=graceful_seconds)
                return
            except subprocess.TimeoutExpired:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def _clear_if_same(self, proc: subprocess.Popen[str]) -> None:
        if self._proc is proc:
            self._proc = None
            self._last_metrics = {}
            self._stderr_tail.clear()

    def request(self, payload: dict[str, Any], *, start: bool = True) -> dict[str, Any]:
        with self._lock:
            if start:
                self._start()
            proc = self._proc
            if (
                not self._running()
                or proc is None
                or proc.stdin is None
                or proc.stdout is None
            ):
                raise RuntimeError("Qwen worker is not running")
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            timeout = self._request_timeout(payload)
            try:
                readable, _, _ = select.select([proc.stdout.fileno()], [], [], timeout)
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"Unable to wait for Qwen worker response: {exc}") from exc
            if not readable:
                self._stop_proc(proc)
                self._clear_if_same(proc)
                raise TimeoutError(
                    f"Qwen worker exceeded {timeout}s request timeout and the registered child was stopped"
                )
            line = proc.stdout.readline()
            if not line:
                detail = self._stderr_summary()
                self._clear_if_same(proc)
                raise RuntimeError(
                    "Qwen worker exited without a protocol response"
                    + (f": {detail}" if detail else "")
                )
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid Qwen worker protocol response: {line[:500]}") from exc
            metrics = response.get("metrics")
            if isinstance(metrics, dict):
                self._last_metrics = metrics
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error", "Qwen worker request failed")))
            return response

    def metrics(self) -> dict[str, Any]:
        acquired = self._lock.acquire(timeout=0.05)
        if not acquired:
            proc = self._proc
            metrics = dict(self._last_metrics)
            metrics.update(
                {
                    "running": proc is not None and proc.poll() is None,
                    "pid": proc.pid if proc is not None and proc.poll() is None else None,
                    "runtime_installed": self.runtime_installed(),
                    "ownership": "registered child process created by MCP Video Gen",
                    "busy": True,
                    "metrics_stale": True,
                }
            )
            return metrics
        try:
            if not self._running():
                return {
                    "running": False,
                    "pid": None,
                    "runtime_installed": self.runtime_installed(),
                    "cuda_allocated_bytes": 0,
                    "cuda_reserved_bytes": 0,
                    "rss_bytes": 0,
                    "model_loaded": False,
                    "busy": False,
                    "ownership": "no active Video Gen Qwen child worker",
                }
            try:
                response = self.request({"cmd": "metrics"}, start=False)
                metrics = dict(response.get("metrics") or {})
            except Exception:
                metrics = dict(self._last_metrics)
                metrics["metrics_stale"] = True
            metrics.update(
                {
                    "running": self._running(),
                    "pid": self._proc.pid if self._proc else None,
                    "runtime_installed": self.runtime_installed(),
                    "ownership": "registered child process created by MCP Video Gen",
                    "busy": False,
                }
            )
            return metrics
        finally:
            self._lock.release()

    def release(self, aggressive: bool = False) -> dict[str, Any]:
        # Aggressive mode must be able to interrupt a hung inference rather than
        # waiting forever for the protocol lock. It still targets only the exact
        # Popen child created by this QwenWorker instance.
        acquired = self._lock.acquire(timeout=1.0)
        if not acquired:
            if not aggressive:
                return {
                    "worker_running": self._running(),
                    "action": "worker_busy_no_change",
                    "retry_with_aggressive_if_user_requested_interrupt": True,
                    "external_processes_touched": False,
                    "container_terminated": False,
                }
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return {
                    "worker_running": False,
                    "action": "none",
                    "external_processes_touched": False,
                    "container_terminated": False,
                }
            pid = proc.pid
            self._stop_proc(proc)
            # The request thread will observe EOF and release the lock. Clearing
            # this exact process reference is safe; no PID discovered externally
            # is ever accepted by this API.
            self._clear_if_same(proc)
            return {
                "worker_running": False,
                "worker_pid": pid,
                "action": "interrupt_registered_qwen_child_worker",
                "external_processes_touched": False,
                "container_terminated": False,
            }

        try:
            if not self._running():
                return {
                    "worker_running": False,
                    "action": "none",
                    "external_processes_touched": False,
                    "container_terminated": False,
                }
            proc = self._proc
            pid = proc.pid if proc else None
            if not aggressive:
                response = self.request({"cmd": "unload"}, start=False)
                return {
                    "worker_running": True,
                    "worker_pid": pid,
                    "action": "unload_model_and_clear_cuda_cache",
                    "metrics": response.get("metrics", {}),
                    "external_processes_touched": False,
                    "container_terminated": False,
                }

            try:
                self.request({"cmd": "shutdown"}, start=False)
            except Exception:
                pass
            if proc is not None:
                self._stop_proc(proc, graceful_seconds=2)
                self._clear_if_same(proc)
            return {
                "worker_running": False,
                "worker_pid": pid,
                "action": "stop_registered_qwen_child_worker",
                "external_processes_touched": False,
                "container_terminated": False,
            }
        finally:
            self._lock.release()


async def install_qwen_runtime(worker: QwenWorker) -> dict[str, Any]:
    if worker.runtime_installed():
        return {"installed": True, "changed": False, "spec": QWEN_PACKAGE_SPEC}
    worker.runtime_root.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(venv.EnvBuilder(with_pip=True, clear=True).create, worker.venv_root)

    async def run_pip(*args: str) -> None:
        env = os.environ.copy()
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        env["PIP_NO_CACHE_DIR"] = "1"
        process = await asyncio.create_subprocess_exec(
            str(worker.python),
            "-m",
            "pip",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        out, err = await process.communicate()
        if process.returncode:
            detail = (err or out).decode(errors="replace")[-12000:]
            raise RuntimeError(f"Qwen runtime pip install failed: {detail}")

    await run_pip("install", "--no-cache-dir", "--upgrade", "pip")
    await run_pip("install", "--no-cache-dir", QWEN_PACKAGE_SPEC)
    (worker.runtime_root / ".runtime-spec").write_text(QWEN_PACKAGE_SPEC + "\n", encoding="utf-8")
    return {"installed": True, "changed": True, "spec": QWEN_PACKAGE_SPEC}
