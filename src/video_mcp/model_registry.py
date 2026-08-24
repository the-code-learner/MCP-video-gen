from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

MIB = 1024 * 1024
GIB = 1024 * MIB
LARGE_ARTIFACT_THRESHOLD_BYTES = 100 * MIB
DEFAULT_INSTALL_HEADROOM_BYTES = 2 * GIB


@dataclass(frozen=True)
class ModelChoice:
    family: str
    profile: str
    label: str
    backend: str
    size_bytes: int
    install_bytes: int
    description: str
    source: str
    sha256: str = ""
    repo_id: str = ""
    revision: str = ""
    main_file: str = ""
    main_file_sha256: str = ""
    verification_files: tuple[tuple[str, str], ...] = ()
    languages: tuple[str, ...] = ()
    requires_gpu: bool = False

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["size_mib"] = round(self.size_bytes / MIB, 1)
        value["install_gib"] = round(self.install_bytes / GIB, 2)
        value["preloaded"] = False
        return value


# Optional artifacts above 100 MiB are deliberately NOT prepared at startup.
# Every such family has at least a light and an optimal choice. CI validates this
# policy so future large integrations cannot silently become mandatory downloads.
MODEL_CATALOG: dict[str, dict[str, ModelChoice]] = {
    "whisper": {
        "light": ModelChoice(
            family="whisper",
            profile="light",
            label="Whisper small q5_1",
            backend="whisper.cpp",
            size_bytes=190_085_487,
            install_bytes=190_085_487,
            description="Lower disk/RAM footprint with much better accuracy than tiny.",
            source="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin",
            sha256="ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
            languages=("multilingual",),
        ),
        "optimal": ModelChoice(
            family="whisper",
            profile="optimal",
            label="Whisper large-v3-turbo q5_0",
            backend="whisper.cpp",
            size_bytes=574_041_195,
            install_bytes=574_041_195,
            description="Recommended transcription quality/speed balance when resources allow.",
            source="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
            sha256="394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
            languages=("multilingual",),
        ),
    },
    "qwen3-tts": {
        "light": ModelChoice(
            family="qwen3-tts",
            profile="light",
            label="Qwen3-TTS 0.6B Base",
            backend="qwen3-tts",
            size_bytes=2_520_000_000,
            install_bytes=2_520_000_000,
            description="Voice cloning/TTS with the lower storage, RAM and VRAM footprint.",
            source="huggingface",
            repo_id="Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            revision="5d83992436eae1d760afd27aff78a71d676296fc",
            main_file="model.safetensors",
            main_file_sha256="180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6",
            verification_files=(("speech_tokenizer/model.safetensors", "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"),),
            languages=("it", "en"),
        ),
        "optimal": ModelChoice(
            family="qwen3-tts",
            profile="optimal",
            label="Qwen3-TTS 1.7B Base",
            backend="qwen3-tts",
            size_bytes=4_540_000_000,
            install_bytes=4_540_000_000,
            description="Higher voice-clone fidelity and synthesis quality when resources allow.",
            source="huggingface",
            repo_id="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            revision="fd4b254389122332181a7c3db7f27e918eec64e3",
            main_file="model.safetensors",
            main_file_sha256="38fc7fc51c5e776e840414b6fd443962e9411b9654888fd7913e4da643cb857c",
            verification_files=(("speech_tokenizer/model.safetensors", "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"),),
            languages=("it", "en"),
            requires_gpu=False,
        ),
    },
}


def validate_model_catalog(catalog: dict[str, dict[str, ModelChoice]] | None = None) -> None:
    data = MODEL_CATALOG if catalog is None else catalog
    for family, choices in data.items():
        if not choices:
            raise ValueError(f"{family}: at least one model choice is required")
        large = [choice for choice in choices.values() if choice.install_bytes > LARGE_ARTIFACT_THRESHOLD_BYTES]
        if large:
            if len(choices) < 2:
                raise ValueError(f"{family}: artifacts above 100 MiB require at least two choices")
            if "light" not in choices or "optimal" not in choices:
                raise ValueError(f"{family}: artifacts above 100 MiB require light and optimal profiles")
        for profile, choice in choices.items():
            if choice.family != family or choice.profile != profile:
                raise ValueError(f"{family}/{profile}: inconsistent catalog identity")
            if choice.size_bytes <= 0 or choice.install_bytes < choice.size_bytes:
                raise ValueError(f"{family}/{profile}: invalid artifact size")
            if choice.source.startswith("https://") and len(choice.sha256) != 64:
                raise ValueError(f"{family}/{profile}: direct downloads require a SHA-256")
            if choice.source == "huggingface":
                if not choice.repo_id or not choice.revision or not choice.main_file:
                    raise ValueError(f"{family}/{profile}: Hugging Face snapshots must pin repo/revision/main file")
                if len(choice.revision) != 40:
                    raise ValueError(f"{family}/{profile}: Hugging Face revision must be a full 40-character commit SHA")
                if len(choice.main_file_sha256) != 64:
                    raise ValueError(f"{family}/{profile}: Hugging Face main weight requires a SHA-256")
                for relative_path, sha256 in choice.verification_files:
                    if not relative_path or relative_path.startswith("/") or ".." in relative_path.split("/"):
                        raise ValueError(f"{family}/{profile}: invalid verification file path")
                    if len(sha256) != 64:
                        raise ValueError(f"{family}/{profile}: verification files require SHA-256 values")


def get_choice(family: str, profile: str) -> ModelChoice:
    validate_model_catalog()
    try:
        return MODEL_CATALOG[family][profile]
    except KeyError as exc:
        raise ValueError(f"Unknown model choice: {family}/{profile}") from exc


def public_catalog(family: str = "") -> dict[str, Any]:
    validate_model_catalog()
    if family:
        if family not in MODEL_CATALOG:
            raise ValueError(f"Unknown model family: {family}")
        source = {family: MODEL_CATALOG[family]}
    else:
        source = MODEL_CATALOG
    return {
        "large_artifact_threshold_mib": LARGE_ARTIFACT_THRESHOLD_BYTES // MIB,
        "policy": "Artifacts above 100 MiB are optional, never preloaded, and must expose light and optimal choices.",
        "families": {
            name: {profile: choice.public() for profile, choice in choices.items()}
            for name, choices in source.items()
        },
    }


validate_model_catalog()
