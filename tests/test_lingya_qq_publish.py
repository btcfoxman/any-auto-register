from __future__ import annotations

import base64

import requests

from platforms.lingya_qq.publish import fetch_lingya_qq_publish_asset


class FakeResponse:
    def __init__(self, payload=None, *, content=b"", content_type="application/json", status_code=200):
        self._payload = payload
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return self._payload


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _png_header(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def test_publish_asset_retries_when_source_connection_is_aborted(monkeypatch):
    calls = []

    def fake_get(url, timeout=5, proxies=None):
        calls.append(url)
        if len(calls) == 1:
            raise requests.ConnectionError("Remote end closed connection without response")
        return FakeResponse(
            {
                "title": "retry ok",
                "video_base64": _b64(b"video"),
                "cover_base64": _b64(b"cover"),
            }
        )

    monkeypatch.setattr("platforms.lingya_qq.publish.requests.get", fake_get)
    monkeypatch.setattr("platforms.lingya_qq.publish.time.sleep", lambda seconds: None)

    asset = fetch_lingya_qq_publish_asset("https://source.example/work", retries=3)

    assert asset.title == "retry ok"
    assert asset.prompt == "retry ok"
    assert asset.video_bytes == b"video"
    assert asset.cover_bytes == b"cover"
    assert calls == ["https://source.example/work", "https://source.example/work"]


def test_publish_asset_retries_media_download_after_5xx(monkeypatch):
    calls = []

    def fake_get(url, timeout=5, proxies=None):
        calls.append(url)
        if url.endswith("/work"):
            return FakeResponse(
                {
                    "title": "media retry",
                    "video_url": "https://source.example/video.mp4",
                    "cover_base64": _b64(b"cover"),
                }
            )
        if url.endswith("/video.mp4") and calls.count(url) == 1:
            return FakeResponse(content=b"temporary", content_type="text/plain", status_code=503)
        return FakeResponse(content=b"video", content_type="video/mp4")

    monkeypatch.setattr("platforms.lingya_qq.publish.requests.get", fake_get)
    monkeypatch.setattr("platforms.lingya_qq.publish.time.sleep", lambda seconds: None)

    asset = fetch_lingya_qq_publish_asset("https://source.example/work", retries=3)

    assert asset.video_bytes == b"video"
    assert calls.count("https://source.example/video.mp4") == 2


def test_publish_asset_reads_intro_and_prompt_aliases(monkeypatch):
    def fake_get(url, timeout=5, proxies=None):
        return FakeResponse(
            {
                "title": "title",
                "intro": "intro text",
                "highlight_prompt": "scene prompt",
                "creation_process_text": "api creation process",
                "tag_infos": [{"id": "tag_2QCVIf1DjL", "title": "玄幻", "alias": ""}],
                "video_base64": _b64(b"video"),
                "cover_base64": _b64(b"cover"),
            }
        )

    monkeypatch.setattr("platforms.lingya_qq.publish.requests.get", fake_get)

    asset = fetch_lingya_qq_publish_asset("https://source.example/work")

    assert asset.description == "intro text"
    assert asset.prompt == "scene prompt"
    assert asset.creation_process_text == "api creation process"
    assert asset.tag_infos == [{"id": "tag_2QCVIf1DjL", "title": "玄幻", "alias": ""}]


def test_publish_asset_json_source_ignores_content_defaults(monkeypatch):
    def fake_get(url, timeout=5, proxies=None):
        return FakeResponse(
            {
                "title": "api title",
                "video_base64": _b64(b"video"),
                "cover_base64": _b64(b"cover"),
            }
        )

    monkeypatch.setattr("platforms.lingya_qq.publish.requests.get", fake_get)

    asset = fetch_lingya_qq_publish_asset(
        "https://source.example/work",
        defaults={
            "description": "default intro",
            "prompt": "default prompt",
            "duration": 99,
            "cover_ratio": 0.5,
            "tag_infos": [{"id": "tag_default", "title": "default"}],
            "creation_process_text": "configured creation process",
        },
    )

    assert asset.description == ""
    assert asset.prompt == "api title"
    assert asset.duration == 10
    assert asset.cover_ratio == 0.75
    assert asset.tag_infos == []
    assert asset.creation_process_text == "Seedance 2.0 全能参考"


def test_publish_asset_calculates_cover_ratio_from_cover_image(monkeypatch):
    def fake_get(url, timeout=5, proxies=None):
        return FakeResponse(
            {
                "title": "ratio title",
                "video_base64": _b64(b"video"),
                "cover_base64": _b64(_png_header(300, 400)),
                "cover_ratio": 1.0,
            }
        )

    monkeypatch.setattr("platforms.lingya_qq.publish.requests.get", fake_get)

    asset = fetch_lingya_qq_publish_asset("https://source.example/work")

    assert asset.cover_ratio == 0.75
