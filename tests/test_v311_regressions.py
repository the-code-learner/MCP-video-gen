from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from video_mcp import openverse_music


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
STACK = ROOT / "video-mcp.yml"


def test_openverse_uses_canonical_endpoint_and_follows_redirects(monkeypatch) -> None:
    real_async_client = httpx.AsyncClient
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host or "")
        if request.url.host == "legacy.example":
            return httpx.Response(
                301,
                headers={"Location": "https://api.openverse.org/v1/audio/"},
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "track-1",
                        "title": "Test track",
                        "creator": "Test creator",
                        "url": "https://cdn.example/test.mp3",
                        "foreign_landing_url": "https://source.example/track-1",
                        "license": "by",
                        "license_version": "4.0",
                        "license_url": "https://creativecommons.org/licenses/by/4.0/",
                        "attribution": "Test track by Test creator",
                        "source": "test-source",
                        "provider": "test-provider",
                        "duration": 90_000,
                        "filetype": "mp3",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client_options: dict[str, object] = {}

    def client_factory(*args, **kwargs):
        client_options.update(kwargs)
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(openverse_music, "OPENVERSE_AUDIO_ENDPOINT", "https://legacy.example/v1/audio/")
    monkeypatch.setattr(openverse_music.httpx, "AsyncClient", client_factory)

    result = asyncio.run(
        openverse_music.search_music(
            openverse_music.OpenverseSession(),
            query="cinematic ambient",
            limit=1,
        )
    )

    assert openverse_music.__dict__["OPENVERSE_AUDIO_ENDPOINT"] == "https://legacy.example/v1/audio/"
    assert client_options["follow_redirects"] is True
    assert seen_hosts == ["legacy.example", "api.openverse.org"]
    assert result["count"] == 1
    assert result["results"][0]["duration_ms"] == 90_000
    assert result["results"][0]["license"] == "by"


def test_openverse_source_defaults_to_canonical_api() -> None:
    source = (ROOT / "src" / "video_mcp" / "openverse_music.py").read_text(encoding="utf-8")
    assert 'OPENVERSE_AUDIO_ENDPOINT = "https://api.openverse.org/v1/audio/"' in source
    assert "api.openverse.engineering" not in source
    assert "follow_redirects=True" in source


def test_release_workflow_recovers_tag_without_release_safely() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "Create or recover immutable stable release" in workflow
    assert 'TAG_EXISTS=false' in workflow
    assert 'RELEASE_EXISTS=false' in workflow
    assert 'git show-ref --verify --quiet "refs/tags/$TAG"' in workflow
    assert 'gh release view "$TAG"' in workflow
    assert 'TAG_SHA="$(git rev-list -n 1 "$TAG")"' in workflow
    assert 'if [ "$TAG_SHA" != "$GITHUB_SHA" ]; then' in workflow
    assert "refusing to move an immutable tag" in workflow
    assert "Recovering missing GitHub Release from existing tag" in workflow
    assert 'gh release create "$TAG"' in workflow


def test_v311_does_not_modify_yaml_contract() -> None:
    stack = STACK.read_text(encoding="utf-8")
    assert "driver: nvidia" in stack
    assert 'WHISPER_MODEL_PATH: "/data/models/whisper/ggml-tiny-q5_1.bin"' in stack
