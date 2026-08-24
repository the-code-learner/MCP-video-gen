from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

import httpx

from video_mcp import openverse_music


def test_openverse_duration_search_stays_within_anonymous_page_limit_and_paginates(monkeypatch) -> None:
    real_async_client = httpx.AsyncClient
    requests: list[tuple[int, int]] = []

    def result(result_id: str, duration_ms: int) -> dict[str, object]:
        return {
            "id": result_id,
            "title": result_id,
            "creator": "Test creator",
            "url": f"https://cdn.example/{result_id}.mp3",
            "foreign_landing_url": f"https://source.example/{result_id}",
            "license": "by",
            "license_version": "4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": f"{result_id} by Test creator",
            "source": "test-source",
            "provider": "test-provider",
            "duration": duration_ms,
            "filetype": "mp3",
        }

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        page = int(query["page"][0])
        page_size = int(query["page_size"][0])
        requests.append((page, page_size))
        if page == 1:
            # Valid API response, but outside the requested 60-120 second range.
            return httpx.Response(200, json={"results": [result("too-short", 30_000)]})
        if page == 2:
            return httpx.Response(200, json={"results": [result("in-range", 90_000)]})
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(openverse_music.httpx, "AsyncClient", client_factory)

    response = asyncio.run(
        openverse_music.search_music(
            openverse_music.OpenverseSession(),
            query="cinematic ambient",
            limit=1,
            duration_min_sec=60,
            duration_max_sec=120,
        )
    )

    assert openverse_music.OPENVERSE_ANONYMOUS_MAX_PAGE_SIZE == 20
    assert requests == [(1, 20), (2, 20)]
    assert response["count"] == 1
    assert response["results"][0]["id"] == "in-range"
    assert response["results"][0]["duration_ms"] == 90_000
    assert response["results"][0]["duration_sec"] == 90.0


def test_openverse_duration_search_never_requests_page_size_50() -> None:
    source = openverse_music.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()
    assert "page_size = 50" not in text
    assert "OPENVERSE_ANONYMOUS_MAX_PAGE_SIZE = 20" in text
