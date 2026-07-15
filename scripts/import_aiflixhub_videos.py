from __future__ import annotations

import argparse
import concurrent.futures
from html import unescape
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
from typing import Any
from urllib.parse import urljoin

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from platforms.lingya_qq.publish import build_creation_process_text
from scripts.import_huanzhou_videos import locate_binary, run_checked, sha256_file
from scripts.split_huanzhou_long_videos import segment_plan


BASE_URL = "https://aiflixhub.com"
LIST_URL = f"{BASE_URL}/movies/auxList/other/{{page}}"
DEFAULT_OUTPUT = Path(r"D:\tmp\tg-videos\video_files")
APPLICATION_RE = re.compile(r"^application \((\d+)\)\.mp4$", re.IGNORECASE)
GRID_RE = re.compile(
    r'<div\s+class=["\']grid-item\s+(?P<category>[^"\']*)["\']\s*>(?P<body>.*?)'
    r'(?=<div\s+class=["\']grid-item\s+|\Z)',
    re.IGNORECASE | re.DOTALL,
)
ATTR_RE = re.compile(r'([:\w-]+)\s*=\s*(["\'])(.*?)\2', re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")

REQUEST_HEADERS = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}

CATEGORY_TAGS = {
    "action": "都市现代",
    "adventure": "奇幻",
    "animation": "娱乐",
    "documentary": "知识科普",
    "drama": "情感",
    "fantasy": "奇幻",
    "horror": "悬疑惊悚",
    "musical": "娱乐",
    "science-fiction": "科幻",
    "thriller": "悬疑惊悚",
}


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _attrs(tag: str) -> dict[str, str]:
    return {match.group(1).lower(): unescape(match.group(3)) for match in ATTR_RE.finditer(tag)}


def _clean_html(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(TAG_RE.sub(" ", value or ""))).strip()


def _first_text(body: str, *, class_name: str = "", element_id: str = "") -> str:
    if class_name:
        pattern = re.compile(
            rf'<(?P<tag>\w+)[^>]*class=["\'][^"\']*\b{re.escape(class_name)}\b[^"\']*["\'][^>]*>'
            rf'(?P<value>.*?)</(?P=tag)>',
            re.IGNORECASE | re.DOTALL,
        )
    else:
        pattern = re.compile(
            rf'<(?P<tag>\w+)[^>]*id=["\']{re.escape(element_id)}["\'][^>]*>'
            rf'(?P<value>.*?)</(?P=tag)>',
            re.IGNORECASE | re.DOTALL,
        )
    match = pattern.search(body)
    return _clean_html(match.group("value")) if match else ""


def parse_list_html(payload: str, *, page: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for grid in GRID_RE.finditer(payload or ""):
        body = grid.group("body")
        card_match = re.search(r'<div[^>]*\bid=["\']cardMovie(\d+)["\']', body, re.IGNORECASE)
        if not card_match:
            continue
        link = ""
        for anchor in re.finditer(r"<a\b[^>]*>", body, re.IGNORECASE | re.DOTALL):
            attributes = _attrs(anchor.group(0))
            if "btnDetail" in attributes.get("class", "").split():
                link = attributes.get("href", "")
                break
        image = ""
        image_fallback = ""
        image_match = re.search(r"<img\b[^>]*>", body, re.IGNORECASE | re.DOTALL)
        if image_match:
            image_attrs = _attrs(image_match.group(0))
            image = image_attrs.get("data-original") or image_attrs.get("src") or ""
            image_fallback = image_attrs.get("src") or ""
        duration_text = _first_text(body, class_name="duration")
        try:
            duration = max(int(float(duration_text)), 0)
        except (TypeError, ValueError):
            duration = 0
        rows.append(
            {
                "movie_id": int(card_match.group(1)),
                "page": page,
                "category": grid.group("category").strip().split()[0] if grid.group("category").strip() else "",
                "title": _first_text(body, class_name="title-shadow"),
                "description": _first_text(body, element_id="searchContent"),
                "duration_seconds": duration,
                "date": _first_text(body, class_name="date"),
                "views": _first_text(body, class_name="views"),
                "likes": _first_text(body, class_name="likes"),
                "detail_url": urljoin(BASE_URL, link),
                "cover_url": urljoin(BASE_URL, image),
                "cover_fallback_url": urljoin(BASE_URL, image_fallback),
            }
        )
    return rows


def parse_detail_video_url(payload: str, detail_url: str) -> str:
    match = re.search(
        r'<source\b[^>]*\bsrc=["\']([^"\']+)["\']',
        payload or "",
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        match = re.search(
            r'"contentUrl"\s*:\s*"([^"]+\.mp4[^"]*)"',
            payload or "",
            re.IGNORECASE,
        )
    if not match:
        return ""
    value = unescape(match.group(1)).replace("https://aiflixhub.com//", "https://aiflixhub.com/")
    return urljoin(detail_url, value)


class AiflixhubImporter:
    def __init__(self, *, output: Path, first_page: int, last_page: int, workers: int, retries: int) -> None:
        self.output = output.resolve()
        self.first_page = max(first_page, 1)
        self.last_page = max(last_page, self.first_page)
        self.workers = max(workers, 1)
        self.retries = max(retries, 1)
        self.staging = self.output / ".aiflixhub_import"
        self.manifest_path = self.output / "aiflixhub_import_manifest.json"
        self.lock = threading.RLock()
        self.local = threading.local()
        self.reserved_hashes: dict[str, str] = {}
        self.ffmpeg = locate_binary(
            "ffmpeg",
            Path(r"D:\workspace_github\ffmpeg-win64-v4.2.1\bin\ffmpeg.exe"),
        )
        self.ffprobe = locate_binary(
            "ffprobe",
            Path(r"D:\workspace_github\ffmpeg-win64-v4.2.1\bin\ffprobe.exe"),
        )
        self.output.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        self.manifest = self._load_manifest()
        self.tag_catalog = self._load_tag_catalog()
        self.existing_sizes: dict[int, list[Path]] = {}
        self.known_hashes: dict[str, str] = {}
        self._index_existing_materials()
        self.next_id = self._next_application_id()

    def _session(self) -> requests.Session:
        session = getattr(self.local, "session", None)
        if session is None:
            session = requests.Session()
            # A stale process-wide proxy makes this host time out. This importer
            # intentionally uses its own direct session and does not affect account proxies.
            session.trust_env = False
            session.headers.update(REQUEST_HEADERS)
            self.local.session = session
        return session

    def _load_manifest(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            try:
                value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    value.setdefault("jobs", {})
                    return value
            except Exception:
                pass
        return {
            "schema_version": 1,
            "source": BASE_URL,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "jobs": {},
        }

    def _save_manifest_locked(self) -> None:
        self.manifest["updated_at"] = utc_now()
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.manifest_path)

    def _load_tag_catalog(self) -> dict[str, dict[str, str]]:
        catalog: dict[str, dict[str, str]] = {}
        for path in self.output.glob("application (*).mp4.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            for item in payload.get("tag_infos") or []:
                if not isinstance(item, dict):
                    continue
                tag_id = str(item.get("id") or "").strip()
                title = str(item.get("title") or "").strip()
                if tag_id and title:
                    catalog[title] = {"id": tag_id, "title": title, "alias": str(item.get("alias") or "")}
        if "视觉艺术" not in catalog:
            raise RuntimeError("Existing material tag catalog is missing 视觉艺术")
        return catalog

    def _index_existing_materials(self) -> None:
        incomplete_reserved = {
            int(value)
            for job in (self.manifest.get("jobs") or {}).values()
            if job.get("status") != "completed"
            for value in (job.get("reserved_ids") or [])
        }
        for path in self.output.glob("application (*).mp4"):
            match = APPLICATION_RE.match(path.name)
            if match and int(match.group(1)) in incomplete_reserved:
                # This belongs to an interrupted transaction and will be
                # overwritten when that job resumes; it is not a duplicate.
                continue
            self.existing_sizes.setdefault(path.stat().st_size, []).append(path)

        huanzhou_path = self.output / "huanzhou_import_manifest.json"
        if huanzhou_path.exists():
            try:
                huanzhou = json.loads(huanzhou_path.read_text(encoding="utf-8"))
            except Exception:
                huanzhou = {}
            for filename, item in (huanzhou.get("existing_hashes") or {}).items():
                if isinstance(item, dict) and item.get("sha256"):
                    path = self.output / filename
                    if path.exists() and path.stat().st_size == item.get("size"):
                        self.known_hashes[path.name] = str(item["sha256"])
            for job in (huanzhou.get("jobs") or {}).values():
                segments = job.get("segments") if job.get("split_status") == "completed" else None
                if isinstance(segments, list):
                    for segment in segments:
                        filename = str((segment.get("files") or {}).get("video") or "")
                        if filename and segment.get("sha256") and (self.output / filename).exists():
                            self.known_hashes[filename] = str(segment["sha256"])
                elif job.get("status") == "completed" and job.get("sha256"):
                    filename = str((job.get("files") or {}).get("video") or "")
                    if filename and (self.output / filename).exists():
                        self.known_hashes[filename] = str(job["sha256"])

        for job in (self.manifest.get("jobs") or {}).values():
            for segment in job.get("segments") or []:
                filename = str((segment.get("files") or {}).get("video") or "")
                if filename and segment.get("sha256") and (self.output / filename).exists():
                    self.known_hashes[filename] = str(segment["sha256"])

    def _next_application_id(self) -> int:
        ids = []
        for path in self.output.glob("application (*).mp4"):
            match = APPLICATION_RE.match(path.name)
            if match:
                ids.append(int(match.group(1)))
        for job in (self.manifest.get("jobs") or {}).values():
            ids.extend(int(value) for value in (job.get("reserved_ids") or []))
        return max(ids, default=0) + 1

    def _request(
        self,
        url: str,
        *,
        referer: str = "",
        stream: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> requests.Response:
        headers = dict(extra_headers or {})
        if referer:
            headers["referer"] = referer
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self._session().get(
                    url,
                    headers=headers,
                    timeout=(30, 300),
                    stream=stream,
                )
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise
                time.sleep(min(2 ** (attempt - 1), 8))
        raise RuntimeError(f"Request failed: {url}: {last_error}")

    def crawl(self) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        page_counts: dict[str, int] = {}
        for page in range(self.first_page, self.last_page + 1):
            response = self._request(
                LIST_URL.format(page=page),
                referer=f"{BASE_URL}/movies/list",
                extra_headers={"x-requested-with": "XMLHttpRequest"},
            )
            page_rows = parse_list_html(response.text, page=page)
            page_counts[str(page)] = len(page_rows)
            for row in page_rows:
                rows[str(row["movie_id"])] = row
            print(f"crawl page={page} rows={len(page_rows)} unique={len(rows)}", flush=True)

        eligible = [row for row in rows.values() if int(row.get("duration_seconds") or 0) >= 60]
        with self.lock:
            jobs = self.manifest.setdefault("jobs", {})
            for movie_id, row in rows.items():
                job = jobs.setdefault(movie_id, {})
                job["movie_id"] = int(movie_id)
                job["source"] = row
                if row["duration_seconds"] < 60 and not job.get("status"):
                    job["status"] = "filtered_short"
            self.manifest["crawl"] = {
                "first_page": self.first_page,
                "last_page": self.last_page,
                "page_counts": page_counts,
                "unique_rows": len(rows),
                "eligible_rows": len(eligible),
                "crawled_at": utc_now(),
            }
            self._save_manifest_locked()
        return eligible

    def _download(self, url: str, destination: Path, *, referer: str) -> None:
        if destination.exists() and destination.stat().st_size > 0:
            return
        partial = destination.with_suffix(destination.suffix + ".part")
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            response: requests.Response | None = None
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                headers = {"range": f"bytes={offset}-"} if offset else {}
                response = self._request(url, referer=referer, stream=True, extra_headers=headers)
                append = offset > 0 and response.status_code == 206
                with partial.open("ab" if append else "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
                if partial.stat().st_size <= 0:
                    raise RuntimeError(f"Downloaded an empty file: {url}")
                os.replace(partial, destination)
                return
            except (OSError, requests.RequestException, RuntimeError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise
                time.sleep(min(2 ** (attempt - 1), 8))
            finally:
                if response is not None:
                    response.close()
        raise RuntimeError(f"Download failed: {url}: {last_error}")

    def _probe(self, path: Path) -> float:
        result = run_checked(
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ]
        )
        return float(result.stdout.strip())

    def _extract_segment(self, source: Path, destination: Path, start: float, duration: float) -> None:
        run_checked(
            [
                self.ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{start:.6f}",
                "-i",
                str(source),
                "-t",
                f"{duration:.6f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                "-movflags",
                "+faststart",
                str(destination),
            ]
        )
        actual = self._probe(destination)
        if actual >= 59.5 and actual <= 120.2:
            return
        destination.unlink(missing_ok=True)
        run_checked(
            [
                self.ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{start:.6f}",
                "-i",
                str(source),
                "-t",
                f"{duration:.6f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(destination),
            ]
        )
        actual = self._probe(destination)
        if actual < 59.5 or actual > 120.2:
            raise RuntimeError(f"Segment duration is outside 60-120 seconds: {actual:.3f}")

    def _make_cover(self, video: Path, destination: Path, *, remote_url: str, referer: str) -> str:
        mode = "video_first_frame"
        raw_cover = destination.with_name("remote_cover")
        if remote_url:
            try:
                self._download(remote_url, raw_cover, referer=referer)
                run_checked(
                    [
                        self.ffmpeg,
                        "-y",
                        "-loglevel",
                        "error",
                        "-i",
                        str(raw_cover),
                        "-frames:v",
                        "1",
                        "-q:v",
                        "2",
                        str(destination),
                    ]
                )
                mode = "remote"
            except Exception:
                destination.unlink(missing_ok=True)
        if not destination.exists() or destination.stat().st_size <= 2 or destination.read_bytes()[:2] != b"\xff\xd8":
            mode = "video_first_frame"
            run_checked(
                [
                    self.ffmpeg,
                    "-y",
                    "-loglevel",
                    "error",
                    "-ss",
                    "0.5",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(destination),
                ]
            )
        if destination.read_bytes()[:2] != b"\xff\xd8":
            raise RuntimeError("Cover conversion did not produce JPEG")
        return mode

    def _tag_for(self, category: str) -> dict[str, str]:
        title = CATEGORY_TAGS.get(category.lower(), "视觉艺术")
        return dict(self.tag_catalog.get(title) or self.tag_catalog["视觉艺术"])

    def _metadata(
        self,
        row: dict[str, Any],
        *,
        local_id: int,
        duration: float,
        index: int,
        count: int,
    ) -> dict[str, Any]:
        source_title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
        base_title = source_title or f"AIFLIXHUB Movie {row['movie_id']}"
        title = f"{base_title}（{index}/{count}）" if count > 1 else base_title
        description = re.sub(r"\s+", " ", str(row.get("description") or "")).strip()
        if description.lower().startswith(base_title.lower()):
            description = description[len(base_title) :].lstrip(" :-—")
        intro = description or f"A short film titled {base_title}."
        prompt = (
            f"围绕“{base_title}”的主题和原作简介组织人物、场景与动作：{intro[:500]}"
            " 保持主体清晰、镜头连贯，并突出关键情节与环境氛围。"
        )
        filename = f"application ({local_id}).mp4"
        return {
            "title": title[:80],
            "intro": intro[:1200],
            "prompt": prompt[:1200],
            "tag_infos": [self._tag_for(str(row.get("category") or ""))],
            "creation_process_text": build_creation_process_text(
                title=title,
                description=intro,
                prompt=prompt,
                video_filename=filename,
                duration=round(duration),
            ),
        }

    @staticmethod
    def _files(local_id: int, root: Path) -> dict[str, Path]:
        return {
            "video": root / f"application ({local_id}).mp4",
            "json": root / f"application ({local_id}).mp4.json",
            "cover": root / f"application ({local_id}).octet-stream_thumb.jpg",
        }

    def _job_complete(self, job: dict[str, Any]) -> bool:
        segments = job.get("segments")
        return bool(
            job.get("status") == "completed"
            and isinstance(segments, list)
            and segments
            and all(
                all(path.exists() and path.stat().st_size > 0 for path in self._files(int(item["local_id"]), self.output).values())
                for item in segments
            )
        )

    def _find_duplicate_locked(self, digest: str, size: int) -> str:
        if digest in self.reserved_hashes:
            return f"pending movie {self.reserved_hashes[digest]}"
        for path in self.existing_sizes.get(size, []):
            candidate = self.known_hashes.get(path.name)
            if not candidate:
                candidate = sha256_file(path)
                self.known_hashes[path.name] = candidate
            if candidate == digest:
                return path.name
        return ""

    def _allocate_ids_locked(self, count: int) -> list[int]:
        values = list(range(self.next_id, self.next_id + count))
        self.next_id += count
        return values

    def process_one(self, row: dict[str, Any]) -> tuple[str, str]:
        movie_id = str(row["movie_id"])
        with self.lock:
            job = self.manifest["jobs"].setdefault(movie_id, {"movie_id": int(movie_id), "source": row})
            if self._job_complete(job):
                return movie_id, "already_completed"
            if job.get("status") == "duplicate":
                return movie_id, "already_duplicate"
            job["status"] = "processing"
            job["started_at"] = utc_now()
            job["error"] = ""
            self._save_manifest_locked()

        stage = self.staging / f"movie_{movie_id}"
        stage.mkdir(parents=True, exist_ok=True)
        source_video = stage / "source.mp4"
        published: list[Path] = []
        digest = ""
        try:
            detail_url = str(row.get("detail_url") or "")
            detail = self._request(detail_url, referer=f"{BASE_URL}/movies/list")
            video_url = parse_detail_video_url(detail.text, detail_url)
            if not video_url:
                raise RuntimeError("Detail page does not contain an MP4 source")
            self._download(video_url, source_video, referer=detail_url)
            actual_duration = self._probe(source_video)
            if actual_duration < 59.5:
                with self.lock:
                    job = self.manifest["jobs"][movie_id]
                    job["status"] = "filtered_actual_short"
                    job["actual_duration_seconds"] = round(actual_duration, 3)
                    self._save_manifest_locked()
                shutil.rmtree(stage, ignore_errors=True)
                return movie_id, "filtered_actual_short"

            digest = sha256_file(source_video)
            source_size = source_video.stat().st_size
            plan = segment_plan(actual_duration)
            with self.lock:
                duplicate = self._find_duplicate_locked(digest, source_size)
                if duplicate:
                    job = self.manifest["jobs"][movie_id]
                    job["status"] = "duplicate"
                    job["duplicate_of"] = duplicate
                    job["sha256"] = digest
                    job["actual_duration_seconds"] = round(actual_duration, 3)
                    self._save_manifest_locked()
                    shutil.rmtree(stage, ignore_errors=True)
                    return movie_id, "duplicate"
                self.reserved_hashes[digest] = movie_id
                reserved = job.get("reserved_ids")
                if not isinstance(reserved, list) or len(reserved) != len(plan):
                    reserved = self._allocate_ids_locked(len(plan))
                    job["reserved_ids"] = reserved
                local_ids = [int(value) for value in reserved]
                job["detail_url"] = detail_url
                job["video_url"] = video_url
                job["sha256"] = digest
                job["actual_duration_seconds"] = round(actual_duration, 3)
                self._save_manifest_locked()

            staged: list[dict[str, Any]] = []
            for index, ((start, requested_duration), local_id) in enumerate(zip(plan, local_ids), start=1):
                segment_dir = stage / f"segment_{index}"
                segment_dir.mkdir(parents=True, exist_ok=True)
                segment_video = source_video if len(plan) == 1 else segment_dir / "video.mp4"
                segment_cover = segment_dir / "cover.jpg"
                segment_json = segment_dir / "metadata.json"
                if len(plan) > 1:
                    self._extract_segment(source_video, segment_video, start, requested_duration)
                segment_duration = self._probe(segment_video)
                if segment_duration < 59.5 or segment_duration > 120.2:
                    raise RuntimeError(f"Invalid segment duration: {segment_duration:.3f}")
                remote_cover = str(row.get("cover_url") or "") if len(plan) == 1 else ""
                try:
                    cover_mode = self._make_cover(
                        segment_video,
                        segment_cover,
                        remote_url=remote_cover,
                        referer=detail_url,
                    )
                except Exception:
                    fallback = str(row.get("cover_fallback_url") or "") if len(plan) == 1 else ""
                    cover_mode = self._make_cover(
                        segment_video,
                        segment_cover,
                        remote_url=fallback,
                        referer=detail_url,
                    )
                metadata = self._metadata(
                    row,
                    local_id=local_id,
                    duration=segment_duration,
                    index=index,
                    count=len(plan),
                )
                segment_json.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                staged.append(
                    {
                        "index": index,
                        "local_id": local_id,
                        "start_seconds": round(start, 3),
                        "requested_duration_seconds": round(requested_duration, 3),
                        "actual_duration_seconds": round(segment_duration, 3),
                        "sha256": sha256_file(segment_video),
                        "cover_mode": cover_mode,
                        "stage": {"video": segment_video, "json": segment_json, "cover": segment_cover},
                    }
                )

            for item in staged:
                final = self._files(item["local_id"], self.output)
                for key in ("cover", "json", "video"):
                    final[key].unlink(missing_ok=True)
                    os.replace(item["stage"][key], final[key])
                    published.append(final[key])

            with self.lock:
                job = self.manifest["jobs"][movie_id]
                job["status"] = "completed"
                job["split_status"] = "completed" if len(staged) > 1 else "not_required"
                job["segments"] = [
                    {key: value for key, value in item.items() if key != "stage"}
                    | {
                        "files": {
                            "video": f"application ({item['local_id']}).mp4",
                            "json": f"application ({item['local_id']}).mp4.json",
                            "cover": f"application ({item['local_id']}).octet-stream_thumb.jpg",
                        }
                    }
                    for item in staged
                ]
                job["finished_at"] = utc_now()
                job.pop("reserved_ids", None)
                for item in staged:
                    path = self.output / f"application ({item['local_id']}).mp4"
                    self.existing_sizes.setdefault(path.stat().st_size, []).append(path)
                    self.known_hashes[path.name] = str(item["sha256"])
                self.reserved_hashes.pop(digest, None)
                self._save_manifest_locked()
            shutil.rmtree(stage, ignore_errors=True)
            return movie_id, "completed"
        except Exception as exc:
            for path in published:
                path.unlink(missing_ok=True)
            with self.lock:
                self.reserved_hashes.pop(digest, None)
                job = self.manifest["jobs"][movie_id]
                job["status"] = "failed"
                job["error"] = f"{type(exc).__name__}: {exc}"
                self._save_manifest_locked()
            return movie_id, "failed"

    def run(self) -> int:
        eligible = self.crawl()
        run_actions: dict[str, int] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = [executor.submit(self.process_one, row) for row in eligible]
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                movie_id, status = future.result()
                run_actions[status] = run_actions.get(status, 0) + 1
                print(
                    f"import {index}/{len(futures)} movie_id={movie_id} status={status} counts={run_actions}",
                    flush=True,
                )

        eligible_ids = {str(row["movie_id"]) for row in eligible}
        state_counts: dict[str, int] = {}
        output_materials = 0
        split_sources = 0
        with self.lock:
            for movie_id, job in self.manifest.get("jobs", {}).items():
                if movie_id not in eligible_ids:
                    continue
                state = str(job.get("status") or "unknown")
                state_counts[state] = state_counts.get(state, 0) + 1
                if state == "completed":
                    output_materials += len(job.get("segments") or [])
                    split_sources += int(job.get("split_status") == "completed")
            self.manifest["summary"] = {
                "eligible_sources": len(eligible),
                "completed_sources": state_counts.get("completed", 0),
                "duplicate_sources": state_counts.get("duplicate", 0),
                "failed_sources": state_counts.get("failed", 0),
                "filtered_actual_short": state_counts.get("filtered_actual_short", 0),
                "split_sources": split_sources,
                "output_materials": output_materials,
                "run_actions": run_actions,
                "finished_at": utc_now(),
            }
            self._save_manifest_locked()
        print(json.dumps(self.manifest["summary"], ensure_ascii=False), flush=True)
        return 1 if state_counts.get("failed") else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Import public AIFLIXHUB movies into the LingYaQQ material directory")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--first-page", type=int, default=1)
    parser.add_argument("--last-page", type=int, default=5)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--retries", type=int, default=4)
    args = parser.parse_args()
    return AiflixhubImporter(
        output=args.output,
        first_page=args.first_page,
        last_page=args.last_page,
        workers=args.workers,
        retries=args.retries,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
