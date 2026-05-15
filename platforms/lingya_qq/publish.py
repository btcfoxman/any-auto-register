from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests


logger = logging.getLogger(__name__)


VIDEO_URL_KEYS = (
    "video_url",
    "videoUrl",
    "video",
    "url",
    "download_url",
    "downloadUrl",
    "file_url",
    "fileUrl",
)
VIDEO_LIST_KEYS = ("video_urls", "videoUrls", "download_urls", "downloadUrls", "files")
COVER_URL_KEYS = (
    "cover_url",
    "coverUrl",
    "cover",
    "image_url",
    "imageUrl",
    "poster_url",
    "posterUrl",
    "thumbnail",
    "thumbnail_url",
    "thumbnailUrl",
)
COVER_LIST_KEYS = ("cover_urls", "coverUrls", "images")
DEFAULT_CREATION_PROCESS_TEXT = "Seedance 2.0 全能参考"
LEGACY_CREATION_PROCESS_TEXTS = {"sora2 tool", "sora 2 tool", "seedance 2.0 tool"}


@dataclass
class LingYaQQPublishAsset:
    title: str
    description: str
    prompt: str
    video_bytes: bytes
    video_filename: str
    video_content_type: str
    cover_bytes: bytes
    cover_filename: str
    cover_content_type: str
    duration: int
    cover_ratio: float = 0.75
    tag_infos: list[dict[str, Any]] = field(default_factory=list)
    creation_process_text: str = DEFAULT_CREATION_PROCESS_TEXT


def _proxy_map(proxy: str | None) -> dict[str, str] | None:
    text = str(proxy or "").strip()
    return {"http": text, "https": text} if text else None


def _is_url(value: Any) -> bool:
    text = str(value or "").strip()
    return text.startswith("http://") or text.startswith("https://")


def _to_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _image_size_from_bytes(data: bytes) -> tuple[int, int] | None:
    if not data:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8"):
        index = 2
        while index + 8 < len(data):
            while index < len(data) and data[index] == 0xFF:
                index += 1
            if index >= len(data):
                break
            marker = data[index]
            index += 1
            if marker in {0x01, *range(0xD0, 0xD8)}:
                continue
            if index + 2 > len(data):
                break
            length = int.from_bytes(data[index:index + 2], "big")
            if length < 2 or index + length > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 7:
                height = int.from_bytes(data[index + 3:index + 5], "big")
                width = int.from_bytes(data[index + 5:index + 7], "big")
                return width, height
            index += length
    if data.startswith(b"RIFF") and len(data) >= 30 and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            width = int.from_bytes(data[24:27] + b"\x00", "little") + 1
            height = int.from_bytes(data[27:30] + b"\x00", "little") + 1
            return width, height
        if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
            return width, height
        if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return width, height
    return None


def _cover_ratio_from_image_bytes(data: bytes, default: float) -> float:
    size = _image_size_from_bytes(data)
    if not size:
        return default
    width, height = size
    if width <= 0 or height <= 0:
        return default
    return round(width / height, 6)


def _unwrap_payload(data: Any) -> Any:
    current = data
    for _ in range(4):
        if isinstance(current, dict):
            for key in ("data", "result", "item", "work", "asset", "payload"):
                value = current.get(key)
                if isinstance(value, (dict, list)):
                    current = value
                    break
            else:
                return current
        elif isinstance(current, list) and current:
            current = current[0]
        else:
            return current
    return current


def _pick(payload: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_media_from_value(value: Any) -> str:
    if _is_url(value):
        return str(value).strip()
    if isinstance(value, dict):
        for key in (*VIDEO_URL_KEYS, *COVER_URL_KEYS):
            candidate = value.get(key)
            if _is_url(candidate):
                return str(candidate).strip()
    if isinstance(value, list):
        for item in value:
            candidate = _first_media_from_value(item)
            if candidate:
                return candidate
    return ""


def _pick_media_url(payload: Any, url_keys: tuple[str, ...], list_keys: tuple[str, ...]) -> str:
    value = _pick(payload, url_keys)
    candidate = _first_media_from_value(value)
    if candidate:
        return candidate
    if isinstance(payload, dict):
        for key in list_keys:
            candidate = _first_media_from_value(payload.get(key))
            if candidate:
                return candidate
    return ""


def _decode_base64(value: Any) -> bytes:
    text = str(value or "").strip()
    if not text:
        return b""
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text, validate=False)
    except Exception:
        return b""


def _filename_from_url(url: str, default_name: str, content_type: str = "") -> str:
    path = urlparse(url).path
    name = os.path.basename(path) or default_name
    name = re.sub(r"[\\/:*?\"<>|\s]+", "_", name).strip("._") or default_name
    if "." not in name:
        ext = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip()) or ""
        if ext:
            name = f"{name}{ext}"
    return name


def _should_retry_response_error(exc: requests.HTTPError) -> bool:
    response = getattr(exc, "response", None)
    status_code = int(getattr(response, "status_code", 0) or 0)
    return status_code >= 500 or status_code in {408, 425, 429}


def _get_with_retries(
    url: str,
    *,
    timeout: int,
    proxy: str | None,
    retries: int = 3,
    retry_delay: float = 2.0,
) -> requests.Response:
    attempts = max(int(retries or 1), 1)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, timeout=max(timeout, 5), proxies=_proxy_map(proxy))
            response.raise_for_status()
            return response
        except requests.HTTPError as exc:
            last_exc = exc
            if attempt >= attempts or not _should_retry_response_error(exc):
                raise
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= attempts:
                raise
        logger.warning("LingYaQQ publish source request failed, retrying %s/%s: %s", attempt, attempts, last_exc)
        time.sleep(max(float(retry_delay or 0), 0))
    if last_exc:
        raise last_exc
    raise RuntimeError(f"LingYaQQ publish source request failed: {url}")


def _download_bytes(url: str, *, timeout: int, proxy: str | None, retries: int = 3) -> tuple[bytes, str, str]:
    response = _get_with_retries(url, timeout=timeout, proxy=proxy, retries=retries)
    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip()
    filename = _filename_from_url(url, "download.bin", content_type)
    return response.content, filename, content_type


def _title_from_payload(payload: Any, defaults: dict[str, Any]) -> str:
    value = _pick(payload, ("title", "name", "caption", "text"))
    title = str(value or defaults.get("title") or "").strip()
    return (title or "LingYaQQ Auto Publish")[:80]


def _prompt_from_payload(payload: Any, defaults: dict[str, Any], *, title: str = "", description: str = "") -> str:
    prompt = str(
        _pick(
            payload,
            (
                "prompt",
                "highlight_prompt",
                "highlightPrompt",
                "scene_prompt",
                "scenePrompt",
                "first_scene_prompt",
                "firstScenePrompt",
                "intro",
            ),
        )
        or defaults.get("prompt")
        or defaults.get("highlight_prompt")
        or description
        or title
        or ""
    ).strip()
    return prompt[:1200]


def _creation_process_text_from_payload(payload: Any, defaults: dict[str, Any]) -> str:
    value = _pick(
        payload,
        (
            "creation_process_text",
            "creationProcessText",
            "creation_process",
            "creationProcess",
            "process_text",
            "processText",
        ),
    )
    text = str(
        value
        or defaults.get("creation_process_text")
        or defaults.get("creationProcessText")
        or defaults.get("creation_process")
        or defaults.get("creationProcess")
        or DEFAULT_CREATION_PROCESS_TEXT
    ).strip()
    if not text or text.lower() in LEGACY_CREATION_PROCESS_TEXTS:
        return DEFAULT_CREATION_PROCESS_TEXT
    return text


def _tag_infos_from_value(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except Exception:
            return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    tags: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source = item.get("tag_info") if isinstance(item.get("tag_info"), dict) else item
        tag_id = str(source.get("id") or "").strip()
        title = str(source.get("title") or "").strip()
        if not tag_id or not title:
            continue
        tags.append({"id": tag_id, "title": title, "alias": str(source.get("alias") or "").strip()})
    return tags


def _tag_infos_from_payload(payload: Any, defaults: dict[str, Any]) -> list[dict[str, str]]:
    return (
        _tag_infos_from_value(_pick(payload, ("tag_infos", "tagInfos", "tags", "tag_info", "tagInfo")))
        or _tag_infos_from_value(defaults.get("tag_infos") or defaults.get("tagInfos") or defaults.get("tags"))
    )


def fetch_lingya_qq_publish_asset(
    source_url: str,
    *,
    timeout: int = 60,
    proxy: str | None = None,
    retries: int = 3,
    defaults: dict[str, Any] | None = None,
) -> LingYaQQPublishAsset:
    defaults = defaults or {}
    response = _get_with_retries(source_url, timeout=timeout, proxy=proxy, retries=retries)
    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()

    payload: Any = None
    if "json" in content_type:
        payload = _unwrap_payload(response.json())
    else:
        text = ""
        if content_type.startswith("text/") or not content_type:
            try:
                text = response.text.strip()
            except Exception:
                text = ""
        if text and text[:1] in {"{", "["}:
            try:
                payload = _unwrap_payload(json.loads(text))
            except Exception:
                payload = None
        if payload is None and text and _is_url(text):
            payload = {"video_url": text}

    if payload is None:
        cover_url = str(defaults.get("cover_url") or defaults.get("coverUrl") or "").strip()
        if not cover_url:
            raise RuntimeError("LingYaQQ publish raw video source requires lingya_qq_publish_cover_url")
        cover_bytes, cover_filename, cover_type = _download_bytes(cover_url, timeout=timeout, proxy=proxy, retries=retries)
        filename = _filename_from_url(source_url, "video.mp4", content_type)
        return LingYaQQPublishAsset(
            title=(title := _title_from_payload({}, defaults)),
            description=(description := str(defaults.get("description") or defaults.get("intro") or "")),
            prompt=_prompt_from_payload({}, defaults, title=title, description=description),
            video_bytes=response.content,
            video_filename=filename,
            video_content_type=content_type or "video/mp4",
            cover_bytes=cover_bytes,
            cover_filename=cover_filename,
            cover_content_type=cover_type or "image/jpeg",
            duration=_to_int(defaults.get("duration"), 10),
            cover_ratio=_cover_ratio_from_image_bytes(cover_bytes, _to_float(defaults.get("cover_ratio"), 0.75)),
            tag_infos=_tag_infos_from_payload({}, defaults),
            creation_process_text=_creation_process_text_from_payload({}, defaults),
        )

    video_base64 = _pick(payload, ("video_base64", "videoBase64", "video_data", "videoData"))
    video_bytes = _decode_base64(video_base64)
    video_content_type = ""
    video_filename = str(_pick(payload, ("file_name", "filename", "video_filename", "videoFilename")) or "").strip()
    if not video_bytes:
        video_url = _pick_media_url(payload, VIDEO_URL_KEYS, VIDEO_LIST_KEYS)
        if not video_url:
            raise RuntimeError("LingYaQQ publish source did not provide video_url/video_base64")
        video_bytes, downloaded_name, video_content_type = _download_bytes(video_url, timeout=timeout, proxy=proxy, retries=retries)
        video_filename = video_filename or downloaded_name
    if not video_filename:
        video_filename = "video.mp4"

    cover_base64 = _pick(payload, ("cover_base64", "coverBase64", "cover_data", "coverData"))
    cover_bytes = _decode_base64(cover_base64)
    cover_content_type = ""
    cover_filename = str(_pick(payload, ("cover_filename", "coverFilename")) or "").strip()
    if not cover_bytes:
        cover_url = _pick_media_url(payload, COVER_URL_KEYS, COVER_LIST_KEYS)
        if not cover_url:
            raise RuntimeError("LingYaQQ publish source did not provide cover_url/cover_base64")
        cover_bytes, downloaded_cover_name, cover_content_type = _download_bytes(cover_url, timeout=timeout, proxy=proxy, retries=retries)
        cover_filename = cover_filename or downloaded_cover_name
    if not cover_filename:
        cover_filename = "cover.jpg"

    title = _title_from_payload(payload, {})
    description = str(_pick(payload, ("description", "intro", "desc", "summary")) or "")
    fallback_cover_ratio = _to_float(_pick(payload, ("cover_ratio", "coverRatio")), 0.75)
    return LingYaQQPublishAsset(
        title=title,
        description=description,
        prompt=_prompt_from_payload(payload, {}, title=title, description=description),
        video_bytes=video_bytes,
        video_filename=video_filename,
        video_content_type=video_content_type or "video/mp4",
        cover_bytes=cover_bytes,
        cover_filename=cover_filename,
        cover_content_type=cover_content_type or "image/jpeg",
        duration=max(_to_int(_pick(payload, ("duration", "duration_seconds", "durationSeconds")), 10), 1),
        cover_ratio=_cover_ratio_from_image_bytes(cover_bytes, fallback_cover_ratio),
        tag_infos=_tag_infos_from_payload(payload, {}),
        creation_process_text=_creation_process_text_from_payload(payload, {}),
    )
