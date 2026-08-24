from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from .download_utils import download_verified
from .model_manager import (
    catalog_with_status,
    install_qwen_model,
    install_whisper,
    is_installed,
    load_selection,
    qwen_path,
    recommend,
    remove_model,
    save_selection,
    select_model,
    sha256_file,
    verify_model,
)
from .model_registry import MODEL_CATALOG, MIB, get_choice, public_catalog
from .openverse_music import OpenverseSession, import_music, search_music
from .qwen_runtime import QWEN_PACKAGE_SPEC, QwenWorker
from .runtime_resources import gpu_snapshot, ram_snapshot, register_resource_tools, storage_snapshot


def _safe_component(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError("name may contain only letters, numbers, dot, underscore and dash")
    return value


def _voice_root(data_root: Path) -> Path:
    return data_root / "qwen3-tts" / "voices"


def _voice_profile(data_root: Path, name: str) -> tuple[Path, dict[str, Any]]:
    root = _voice_root(data_root) / _safe_component(name)
    meta_path = root / "profile.json"
    if not meta_path.is_file():
        raise ValueError(f"Unknown Qwen voice: {name}")
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid Qwen voice profile")
    return root, data


def _piper_root(data_root: Path) -> Path:
    return Path(os.getenv("PIPER_VOICES_ROOT", str(data_root / "piper" / "voices"))).resolve()


def _piper_enabled() -> bool:
    return os.getenv("PIPER_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def register_local_ai_tools(
    mcp: Any,
    *,
    data_root: Path,
    exports: Path,
    tmp: Path,
    cached: Callable[[str], Path],
    target: Callable[[str], tuple[str, Path]],
    file_meta: Callable[..., dict[str, Any]],
    command: Callable[[list[str], int], Any],
    ffmpeg_timeout: int,
) -> QwenWorker:
    worker = QwenWorker(data_root=data_root)
    openverse = OpenverseSession()

    register_resource_tools(
        mcp,
        data_root=data_root,
        exports=exports,
        worker_metrics=worker.metrics,
        release_worker=worker.release,
    )

    @mcp.tool()
    async def model_catalog(family: str = "") -> dict[str, Any]:
        """List optional local model choices and installation state. Models above 100 MiB are never preloaded and expose light/optimal profiles."""
        return catalog_with_status(data_root, family)

    @mcp.tool()
    async def model_recommend(family: str) -> dict[str, Any]:
        """Recommend light or optimal using current disk/RAM/VRAM headroom. This performs no download, deletion, or model load."""
        return recommend(data_root, family, worker)

    @mcp.tool()
    async def model_verify(family: str, profile: str) -> dict[str, Any]:
        """Perform an explicit full SHA-256 integrity check of an installed optional model. This may read several GiB from disk."""
        return await verify_model(data_root, family, profile)

    @mcp.tool()
    async def model_install(family: str, profile: str, confirm: bool = False) -> dict[str, Any]:
        """Install one optional model only after confirm=true. Insufficient storage returns a reclaim preview; cache is never deleted automatically."""
        choice = get_choice(family, profile)
        recommendation = recommend(data_root, family, worker)
        details = recommendation.get("choices", {}).get(profile, {})
        if details.get("installed"):
            return {
                "installed": True,
                "changed": False,
                "choice": choice.public(),
                "recommendation": recommendation,
            }
        if not confirm:
            return {
                "installed": False,
                "approval_required": True,
                "choice": choice.public(),
                "additional_install_gib": details.get("additional_install_gib"),
                "recommendation": recommendation,
            }
        if details.get("disk_ok_with_2gib_headroom") is not True:
            return {
                "installed": False,
                "reason": "insufficient_disk_headroom",
                "choice": choice.public(),
                "recommendation": recommendation,
                "cache_cleanup_requires_separate_user_approval": True,
            }
        if family == "whisper":
            result = await install_whisper(data_root, profile)
        elif family == "qwen3-tts":
            result = await install_qwen_model(data_root, worker, profile)
        else:
            raise ValueError(f"Unsupported model family: {family}")
        result["next_action"] = f"model_select('{family}', '{profile}') if you want this profile to become the selected default"
        return {"choice": choice.public(), **result}

    @mcp.tool()
    async def model_select(family: str, profile: str) -> dict[str, Any]:
        """Select an already-installed model profile without downloading anything."""
        return select_model(data_root, family, profile)

    @mcp.tool()
    async def model_remove(family: str, profile: str, confirm: bool = False) -> dict[str, Any]:
        """Remove one optional model only after confirm=true. Selected models must be deselected by selecting another profile first."""
        return remove_model(data_root, worker, family, profile, confirm=confirm)

    @mcp.tool()
    async def qwen_tts_info() -> dict[str, Any]:
        """Report Qwen runtime, installed profiles, selected profile, cloned voices and worker resource state."""
        voices = []
        root = _voice_root(data_root)
        if root.is_dir():
            voices = sorted(path.parent.name for path in root.glob("*/profile.json"))
        return {
            "runtime_installed": worker.runtime_installed(),
            "runtime_spec": QWEN_PACKAGE_SPEC,
            "models": {
                profile: is_installed(data_root, "qwen3-tts", profile)
                for profile in MODEL_CATALOG["qwen3-tts"]
            },
            "selected_profile": load_selection(data_root).get("qwen3-tts", ""),
            "voices": voices,
            "worker": worker.metrics(),
        }

    @mcp.tool()
    async def qwen_voice_clone_create(
        file_id: str,
        name: str,
        reference_text: str,
        language: str = "it",
        consent_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Create a persistent IT/EN voice-clone profile from cached reference audio. consent_confirmed=true is mandatory and must reflect explicit authorization for this voice."""
        if not consent_confirmed:
            raise ValueError("consent_confirmed=true is required to create a voice clone")
        language = language.lower().strip()
        if language not in {"it", "en"}:
            raise ValueError("initial voice-clone language must be it or en")
        reference_text = reference_text.strip()
        if not reference_text:
            raise ValueError("reference_text is required and should match the spoken reference audio")
        voice_name = _safe_component(name)
        src = cached(file_id)
        root = _voice_root(data_root) / voice_name
        if root.exists():
            raise ValueError(f"voice already exists: {voice_name}")
        root.mkdir(parents=True, exist_ok=False)
        reference = root / "reference.wav"
        try:
            await command(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(src),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-c:a",
                    "pcm_s16le",
                    str(reference),
                ],
                ffmpeg_timeout,
            )
            meta = {
                "name": voice_name,
                "language": language,
                "reference_text": reference_text,
                "reference_file_id": file_id,
                "reference_sha256": await asyncio.to_thread(sha256_file, reference),
                "consent_confirmed": True,
                "created_epoch": int(time.time()),
            }
            (root / "profile.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            return {
                "name": voice_name,
                "language": language,
                "reference_file_id": file_id,
                "reference_sha256": meta["reference_sha256"],
                "reference_text_present": True,
                "consent_confirmed": True,
                "created_epoch": meta["created_epoch"],
            }
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise

    @mcp.tool()
    async def qwen_voice_clone_list() -> dict[str, Any]:
        """List persistent Qwen voice-clone profiles without exposing reference audio or stored reference transcripts."""
        result = []
        root = _voice_root(data_root)
        if root.is_dir():
            for path in sorted(root.glob("*/profile.json")):
                try:
                    meta = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                result.append(
                    {
                        "name": meta.get("name", path.parent.name),
                        "language": meta.get("language"),
                        "created_epoch": meta.get("created_epoch"),
                        "reference_sha256": meta.get("reference_sha256"),
                    }
                )
        return {"voices": result, "count": len(result)}

    @mcp.tool()
    async def qwen_voice_clone_info(name: str) -> dict[str, Any]:
        """Inspect one persistent Qwen voice profile without returning its reference transcript or audio bytes."""
        _, meta = _voice_profile(data_root, name)
        return {
            "name": meta.get("name", name),
            "language": meta.get("language"),
            "reference_file_id": meta.get("reference_file_id"),
            "reference_sha256": meta.get("reference_sha256"),
            "created_epoch": meta.get("created_epoch"),
            "consent_confirmed": bool(meta.get("consent_confirmed")),
            "reference_text_present": bool(meta.get("reference_text")),
        }

    @mcp.tool()
    async def qwen_voice_clone_delete(name: str, confirm: bool = False) -> dict[str, Any]:
        """Delete one persistent cloned voice only after confirm=true."""
        root, _ = _voice_profile(data_root, name)
        if not confirm:
            return {"deleted": False, "approval_required": True, "name": name}
        shutil.rmtree(root)
        return {"deleted": True, "name": name}

    async def qwen_synthesize(
        text: str,
        voice: str,
        language: str,
        profile: str,
        output_filename: str,
        max_new_tokens: int = 0,
    ) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise ValueError("text is required")
        language = language.lower().strip()
        if language not in {"it", "en"}:
            raise ValueError("language must be it or en")
        if profile not in MODEL_CATALOG["qwen3-tts"]:
            raise ValueError("model_profile must be light or optimal")
        if not is_installed(data_root, "qwen3-tts", profile):
            raise RuntimeError(f"Qwen {profile} is not installed; use model_recommend/model_install first")
        root, meta = _voice_profile(data_root, voice)
        output_name = Path(output_filename or "qwen-tts.wav").name
        file_id, out = target(output_name)
        payload: dict[str, Any] = {
            "cmd": "synthesize",
            "model_path": str(qwen_path(data_root, profile)),
            "text": text,
            "language": "Italian" if language == "it" else "English",
            "ref_audio": str(root / "reference.wav"),
            "ref_text": str(meta["reference_text"]),
            "output_path": str(out),
        }
        if max_new_tokens > 0:
            payload["max_new_tokens"] = int(max_new_tokens)
        try:
            response = await asyncio.to_thread(worker.request, payload)
            if not out.is_file() or out.stat().st_size <= 0:
                raise RuntimeError("Qwen worker did not produce a non-empty output")
        except Exception:
            out.unlink(missing_ok=True)
            raise
        selection = load_selection(data_root)
        selection["qwen3-tts"] = profile
        save_selection(data_root, selection)
        return file_meta(
            file_id,
            out,
            "qwen3-tts.voice-clone",
            voice=voice,
            language=language,
            model_profile=profile,
            sample_rate=response.get("sample_rate"),
            max_new_tokens=response.get("max_new_tokens"),
        )

    @mcp.tool()
    async def qwen_tts_synthesize(
        text: str,
        voice: str,
        language: str = "it",
        model_profile: str = "light",
        output_filename: str = "qwen-tts.wav",
        max_new_tokens: int = 0,
    ) -> dict[str, Any]:
        """Synthesize IT/EN speech locally with a persistent cloned voice and an installed Qwen light/optimal model."""
        return await qwen_synthesize(
            text, voice, language, model_profile, output_filename, max_new_tokens
        )

    @mcp.tool()
    async def tts_generate(
        text: str,
        engine: str = "qwen3",
        voice: str = "",
        language: str = "it",
        model_profile: str = "light",
        output_filename: str = "tts.wav",
        length_scale: float = 1.0,
    ) -> dict[str, Any]:
        """Unified local TTS entry point: qwen3 for cloned voices or Piper for lightweight ONNX voices."""
        engine = engine.lower().strip()
        if engine == "qwen3":
            return await qwen_synthesize(text, voice, language, model_profile, output_filename)
        if engine != "piper":
            raise ValueError("engine must be qwen3 or piper")
        if not _piper_enabled():
            raise RuntimeError("Piper is disabled")
        voices = _piper_root(data_root)
        model = (voices / voice).resolve()
        if not model.is_relative_to(voices) or model.suffix.lower() != ".onnx" or not model.is_file():
            raise ValueError("invalid Piper voice model")
        file_id, out = target(Path(output_filename or "tts.wav").name)

        def synthesize_piper() -> None:
            import wave
            from piper import PiperVoice, SynthesisConfig

            loaded = PiperVoice.load(str(model))
            config = SynthesisConfig(length_scale=max(0.1, min(5.0, length_scale)))
            with wave.open(str(out), "wb") as handle:
                loaded.synthesize_wav(text, handle, syn_config=config)

        await asyncio.to_thread(synthesize_piper)
        return file_meta(
            file_id,
            out,
            "piper.tts",
            voice_model=voice,
            length_scale=length_scale,
        )

    @mcp.tool()
    async def piper_voice_install(
        model_url: str,
        config_url: str,
        voice_name: str,
        confirm: bool = False,
        expected_model_sha256: str = "",
        expected_config_sha256: str = "",
    ) -> dict[str, Any]:
        """Install a user-selected Piper voice from public HTTPS sources. Piper convenience installs are capped at 100 MiB so larger managed artifacts must use the light/optimal model policy."""
        voice_name = _safe_component(voice_name)
        if not confirm:
            return {"installed": False, "approval_required": True, "voice_name": voice_name}
        voices = _piper_root(data_root)
        root = voices / voice_name
        root.mkdir(parents=True, exist_ok=True)
        model = root / f"{voice_name}.onnx"
        config = root / f"{voice_name}.onnx.json"
        try:
            await download_verified(
                model_url,
                model,
                expected_sha256=expected_model_sha256,
                max_bytes=100 * MIB,
            )
            await download_verified(
                config_url,
                config,
                expected_sha256=expected_config_sha256,
                max_bytes=8 * MIB,
            )
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise
        return {
            "installed": True,
            "voice_name": voice_name,
            "model": str(model.relative_to(voices)),
            "config": str(config.relative_to(voices)),
        }

    @mcp.tool()
    async def piper_voice_remove(voice_name: str, confirm: bool = False) -> dict[str, Any]:
        """Remove one Piper voice directory only after confirm=true."""
        voice_name = _safe_component(voice_name)
        voices = _piper_root(data_root)
        root = (voices / voice_name).resolve()
        if not root.is_relative_to(voices) or not root.is_dir():
            return {"removed": False, "reason": "not_installed"}
        if not confirm:
            return {"removed": False, "approval_required": True, "voice_name": voice_name}
        shutil.rmtree(root)
        return {"removed": True, "voice_name": voice_name}

    @mcp.tool()
    async def music_search(
        query: str,
        limit: int = 10,
        commercial_use: bool = True,
        duration_min_sec: int = 0,
        duration_max_sec: int = 0,
    ) -> dict[str, Any]:
        """Search Openverse music without downloading media. Exact duration bounds are expressed in seconds and applied to Openverse millisecond metadata."""
        return await search_music(
            openverse,
            query=query,
            limit=limit,
            commercial_use=commercial_use,
            duration_min_sec=duration_min_sec,
            duration_max_sec=duration_max_sec,
        )

    @mcp.tool()
    async def music_import(result_id: str, filename: str = "") -> dict[str, Any]:
        """Import a result from a recent Openverse search into the Video Gen cache while preserving license and attribution metadata."""
        return await import_music(
            openverse,
            result_id=result_id,
            filename=filename,
            tmp=tmp,
            target=target,
            file_meta=file_meta,
        )

    @mcp.tool()
    async def local_ai_status() -> dict[str, Any]:
        """Summarize local AI models, voices, storage and current Video Gen RAM/VRAM ownership."""
        voices = []
        root = _voice_root(data_root)
        if root.is_dir():
            voices = sorted(path.parent.name for path in root.glob("*/profile.json"))
        metrics = worker.metrics()
        return {
            "model_policy": public_catalog(),
            "installed": {
                family: {
                    profile: is_installed(data_root, family, profile)
                    for profile in profiles
                }
                for family, profiles in MODEL_CATALOG.items()
            },
            "selection": load_selection(data_root),
            "qwen": {
                "runtime_installed": worker.runtime_installed(),
                "runtime_spec": QWEN_PACKAGE_SPEC,
                "worker": metrics,
                "voice_count": len(voices),
            },
            "storage": storage_snapshot(data_root, include_breakdown=False),
            "ram": ram_snapshot(),
            "gpu": gpu_snapshot(metrics),
            "openverse": {
                "endpoint": "https://api.openverse.engineering/v1/audio/",
                "authentication_required": False,
            },
        }

    return worker
