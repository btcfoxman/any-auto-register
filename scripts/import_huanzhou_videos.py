from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from platforms.lingya_qq.publish import build_creation_process_text


API_URL = "https://www.huanzhou.art/api/base/works/index"
DEFAULT_OUTPUT = Path(r"D:\tmp\tg-videos\video_files")
INSECURE_TLS_HOSTS = {"works.huanzhou.art", "poster.huanzhou.art"}
APPLICATION_RE = re.compile(r"^application \((\d+)\)\.mp4$", re.IGNORECASE)
REQUEST_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "referer": "https://www.huanzhou.art/ai/square",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
    "xx-device-type": "web",
}
TAG_KEYWORDS = (
    ("美食", ("美食", "烹饪", "厨房", "食物", "餐厅", "料理", "咖啡", "蛋糕", "火锅")),
    ("萌宠", ("萌宠", "猫", "狗", "小动物", "宠物", "熊猫", "兔子", "狐狸")),
    ("战争", ("战争", "战场", "士兵", "军队", "枪战", "坦克", "空袭")),
    ("末日", ("末日", "废土", "灾难", "丧尸", "毁灭", "废墟")),
    ("犯罪", ("犯罪", "警察", "案件", "追捕", "黑帮", "凶手", "绑架")),
    ("悬疑惊悚", ("悬疑", "惊悚", "恐怖", "诡异", "鬼", "怪谈", "密室")),
    ("科幻", ("科幻", "未来", "太空", "宇宙", "机器人", "机甲", "赛博", "飞船")),
    ("玄幻", ("玄幻", "修仙", "仙侠", "神魔", "渡劫", "法术")),
    ("古装", ("古装", "武侠", "江湖", "宫廷", "皇帝", "侠客", "大唐", "宋朝")),
    ("奇幻", ("奇幻", "魔法", "精灵", "巨龙", "童话", "宫崎骏", "超现实")),
    ("历史人文", ("历史", "古代", "文物", "人文", "传统", "故宫", "博物馆")),
    ("游戏", ("游戏", "电竞", "玩家", "像素", "关卡")),
    ("科技", ("科技", "芯片", "编程", "人工智能", "数码", "无人机")),
    ("知识科普", ("科普", "知识", "教学", "教程", "实验", "原理")),
    ("创意广告", ("广告", "品牌", "产品展示", "宣传片", "商业")),
    ("时尚", ("时尚", "服装", "穿搭", "模特", "秀场", "妆容")),
    ("复古", ("复古", "怀旧", "老电影", "年代感", "胶片")),
    ("风景", ("风景", "山川", "森林", "海边", "草原", "日落", "旅行", "自然")),
    ("VLOG", ("vlog", "日常", "记录", "探店", "旅行日志")),
    ("情感", ("爱情", "亲情", "友情", "家庭", "感动", "温情", "孩子", "母亲", "父亲")),
    ("都市现代", ("都市", "城市", "职场", "现代", "街头", "生活")),
    ("娱乐", ("娱乐", "搞笑", "舞蹈", "音乐", "明星", "综艺", "演出")),
    ("数字人", ("数字人", "虚拟人", "虚拟主播", "口播")),
    ("意识流", ("意识流", "梦境", "抽象叙事", "实验影像")),
)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_duration(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        if ":" not in text:
            return max(int(float(text)), 0)
        parts = [int(float(part)) for part in text.split(":")]
    except (TypeError, ValueError):
        return 0
    if len(parts) == 2:
        return max(parts[0] * 60 + parts[1], 0)
    if len(parts) == 3:
        return max(parts[0] * 3600 + parts[1] * 60 + parts[2], 0)
    return 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_binary(name: str, fallback: Path) -> str:
    found = shutil.which(name)
    if found:
        return found
    if fallback.exists():
        return str(fallback)
    raise RuntimeError(f"Required binary not found: {name}")


def run_checked(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


class HuanzhouImporter:
    def __init__(self, *, output: Path, page_size: int, workers: int, retries: int) -> None:
        self.output = output.resolve()
        self.page_size = max(page_size, 1)
        self.workers = max(workers, 1)
        self.retries = max(retries, 1)
        self.staging = self.output / ".huanzhou_import"
        self.contact_dir = self.output / "analysis_frames" / "huanzhou"
        self.manifest_path = self.output / "huanzhou_import_manifest.json"
        self.lock = threading.RLock()
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
        self.contact_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = self._load_manifest()
        self.tag_catalog = self._load_tag_catalog()
        self.existing_sizes: dict[int, list[Path]] = {}
        self.existing_hashes = self._load_existing_hashes()
        self.next_id = self._next_application_id()

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
            "source": API_URL,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "jobs": {},
        }

    def _save_manifest_locked(self) -> None:
        self.manifest["updated_at"] = utc_now()
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.manifest_path)

    def save_manifest(self) -> None:
        with self.lock:
            self._save_manifest_locked()

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
            raise RuntimeError("Existing material tag catalog is missing the required 视觉艺术 fallback tag")
        return catalog

    def _load_existing_hashes(self) -> dict[str, str]:
        cached = self.manifest.setdefault("existing_hashes", {})
        hashes: dict[str, str] = {}
        for path in sorted(self.output.glob("application (*).mp4")):
            stat = path.stat()
            self.existing_sizes.setdefault(stat.st_size, []).append(path)
            cache_key = path.name
            cached_item = cached.get(cache_key) if isinstance(cached.get(cache_key), dict) else {}
            if (
                cached_item.get("size") == stat.st_size
                and cached_item.get("mtime_ns") == stat.st_mtime_ns
                and cached_item.get("sha256")
            ):
                digest = str(cached_item["sha256"])
                hashes[digest] = path.name
        self.save_manifest()
        return hashes

    def _find_existing_duplicate(self, digest: str, size: int) -> str:
        with self.lock:
            known = self.existing_hashes.get(digest)
            if known:
                return known
            cache = self.manifest.setdefault("existing_hashes", {})
            for path in self.existing_sizes.get(size, []):
                stat = path.stat()
                cached_item = cache.get(path.name) if isinstance(cache.get(path.name), dict) else {}
                if (
                    cached_item.get("size") == stat.st_size
                    and cached_item.get("mtime_ns") == stat.st_mtime_ns
                    and cached_item.get("sha256")
                ):
                    candidate_digest = str(cached_item["sha256"])
                else:
                    candidate_digest = sha256_file(path)
                    cache[path.name] = {
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "sha256": candidate_digest,
                    }
                self.existing_hashes[candidate_digest] = path.name
                if candidate_digest == digest:
                    return path.name
            return ""

    def _next_application_id(self) -> int:
        ids = []
        for path in self.output.glob("application (*).mp4"):
            match = APPLICATION_RE.match(path.name)
            if match:
                ids.append(int(match.group(1)))
        return max(ids, default=0) + 1

    def _request(self, url: str, *, params: dict[str, Any] | None = None, stream: bool = False) -> requests.Response:
        hostname = (urlparse(url).hostname or "").lower()
        verify = hostname not in INSECURE_TLS_HOSTS
        if not verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=REQUEST_HEADERS,
                    timeout=(30, 300),
                    stream=stream,
                    verify=verify,
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
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        total = 0
        page = 1
        while True:
            response = self._request(
                API_URL,
                params={"page": page, "page_size": self.page_size, "works_type": 1},
            )
            payload = response.json()
            if int(payload.get("code") or 0) != 200:
                raise RuntimeError(f"Huanzhou API returned an error on page {page}: {payload}")
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            page_rows = data.get("list") if isinstance(data.get("list"), list) else []
            total = max(total, int(data.get("total") or 0))
            if not page_rows:
                break
            for row in page_rows:
                if not isinstance(row, dict):
                    continue
                work_id = str(row.get("work_id") or "").strip()
                if work_id and work_id not in seen:
                    seen.add(work_id)
                    rows.append(dict(row))
            print(f"crawl page={page} rows={len(page_rows)} unique={len(rows)} total={total}", flush=True)
            if total and len(rows) >= total:
                break
            page += 1

        eligible = [row for row in rows if parse_duration(row.get("duration")) >= 60]
        with self.lock:
            self.manifest["crawl"] = {
                "total": total,
                "unique_rows": len(rows),
                "eligible_rows": len(eligible),
                "page_size": self.page_size,
                "last_page": page,
                "crawled_at": utc_now(),
            }
            jobs = self.manifest.setdefault("jobs", {})
            for row in rows:
                work_id = str(row.get("work_id") or "")
                if not work_id:
                    continue
                job = jobs.setdefault(work_id, {})
                job["work_id"] = int(row.get("work_id") or 0)
                job["source"] = row
                job["declared_duration_seconds"] = parse_duration(row.get("duration"))
                if job["declared_duration_seconds"] < 60 and not job.get("status"):
                    job["status"] = "filtered_short"
            self._save_manifest_locked()
        return eligible

    def _download_file(self, url: str, destination: Path) -> None:
        if destination.exists() and destination.stat().st_size > 0:
            return
        temporary = destination.with_name(
            f"{destination.name}.part.{os.getpid()}.{threading.get_ident()}"
        )
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            response: requests.Response | None = None
            try:
                temporary.unlink(missing_ok=True)
                response = self._request(url, stream=True)
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
                if temporary.stat().st_size <= 0:
                    raise RuntimeError(f"Downloaded an empty file: {url}")
                os.replace(temporary, destination)
                return
            except (OSError, requests.RequestException, RuntimeError) as exc:
                last_error = exc
                temporary.unlink(missing_ok=True)
                if attempt >= self.retries:
                    raise
                time.sleep(min(2 ** (attempt - 1), 8))
            finally:
                if response is not None:
                    response.close()
        raise RuntimeError(f"Download failed: {url}: {last_error}")

    def _probe_duration(self, video: Path) -> float:
        result = run_checked(
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video),
            ]
        )
        return float(result.stdout.strip())

    def _make_cover(self, poster_url: str, video: Path, destination: Path, raw_cover: Path) -> str:
        mode = "remote"
        if poster_url:
            try:
                self._download_file(poster_url, raw_cover)
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
                    "1",
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
            raise RuntimeError("Cover conversion did not produce a JPEG file")
        return mode

    def _make_contact_sheet(self, work_id: str, video: Path, duration: float) -> Path:
        destination = self.contact_dir / f"huanzhou_{work_id}.jpg"
        if destination.exists() and destination.stat().st_size > 0:
            return destination
        temporary = destination.with_suffix(".tmp.jpg")
        frames = [destination.with_name(f"{destination.stem}.frame_{frame_index}.jpg") for frame_index in range(5)]
        try:
            for frame, fraction in zip(frames, (0.05, 0.25, 0.5, 0.75, 0.95)):
                run_checked(
                    [
                        self.ffmpeg,
                        "-y",
                        "-loglevel",
                        "error",
                        "-ss",
                        f"{max(duration * fraction, 0.1):.6f}",
                        "-i",
                        str(video),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=320:-2",
                        "-q:v",
                        "3",
                        str(frame),
                    ]
                )
            command = [self.ffmpeg, "-y", "-loglevel", "error"]
            for frame in frames:
                command.extend(["-i", str(frame)])
            command.extend(
                [
                    "-filter_complex",
                    "[0:v][1:v][2:v][3:v][4:v]hstack=inputs=5[v]",
                    "-map",
                    "[v]",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    str(temporary),
                ]
            )
            run_checked(command)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
            for frame in frames:
                frame.unlink(missing_ok=True)
        return destination

    def _tag_for(self, title: str, description: str) -> dict[str, str]:
        haystack = f"{title} {description}".lower()
        for tag_title, keywords in TAG_KEYWORDS:
            if tag_title not in self.tag_catalog:
                continue
            if any(keyword.lower() in haystack for keyword in keywords):
                return dict(self.tag_catalog[tag_title])
        return dict(self.tag_catalog["视觉艺术"])

    def _metadata_for(self, row: dict[str, Any], *, local_id: int, duration: int) -> dict[str, Any]:
        work_id = str(row.get("work_id") or local_id)
        source_title = re.sub(r"\s+", " ", str(row.get("name") or "")).strip()
        source_description = re.sub(r"\s+", " ", str(row.get("desc") or "")).strip()
        title = (source_title or f"幻舟影像作品 {work_id}")[:80]
        intro = source_description or f"围绕“{title}”展开的影像内容，呈现主要人物、动作与场景变化。"
        prompt_detail = source_description or "画面中的主要人物、环境、动作和光影变化"
        prompt = f"以“{title}”为主题，重点呈现{prompt_detail}，保持主体清晰、场景连贯并突出关键情节。"
        filename = f"application ({local_id}).mp4"
        return {
            "title": title,
            "intro": intro,
            "prompt": prompt,
            "tag_infos": [self._tag_for(title, intro)],
            "creation_process_text": build_creation_process_text(
                title=title,
                description=intro,
                prompt=prompt,
                video_filename=filename,
                duration=duration,
            ),
        }

    def _complete_files_exist(self, local_id: Any) -> bool:
        try:
            number = int(local_id)
        except (TypeError, ValueError):
            return False
        return all(
            path.exists() and path.stat().st_size > 0
            for path in (
                self.output / f"application ({number}).mp4",
                self.output / f"application ({number}).mp4.json",
                self.output / f"application ({number}).octet-stream_thumb.jpg",
            )
        )

    def _allocate_id_locked(self) -> int:
        while any(
            (self.output / name).exists()
            for name in (
                f"application ({self.next_id}).mp4",
                f"application ({self.next_id}).mp4.json",
                f"application ({self.next_id}).octet-stream_thumb.jpg",
            )
        ):
            self.next_id += 1
        value = self.next_id
        self.next_id += 1
        return value

    def process_one(self, row: dict[str, Any]) -> tuple[str, str]:
        work_id = str(row.get("work_id") or "").strip()
        if not work_id:
            return "unknown", "failed"
        with self.lock:
            job = self.manifest["jobs"].setdefault(work_id, {"source": row})
            if job.get("status") == "completed" and self._complete_files_exist(job.get("local_id")):
                return work_id, "completed"
            if job.get("status") == "duplicate" and job.get("duplicate_of"):
                return work_id, "duplicate"
            job["status"] = "processing"
            job["started_at"] = utc_now()
            job["error"] = ""
            self._save_manifest_locked()

        stage = self.staging / f"work_{work_id}"
        stage.mkdir(parents=True, exist_ok=True)
        video = stage / "video.mp4"
        cover = stage / "cover.jpg"
        raw_cover = stage / "cover.source"
        digest = ""
        published_moves: list[tuple[Path, Path]] = []
        try:
            source_url = str(row.get("source_url") or "").strip()
            if not source_url:
                raise RuntimeError("source_url is empty")
            self._download_file(source_url, video)
            actual_duration = self._probe_duration(video)
            if actual_duration < 59.5:
                raise RuntimeError(f"Downloaded video is shorter than 60 seconds: {actual_duration:.3f}")
            digest = sha256_file(video)

            duplicate_of = self._find_existing_duplicate(digest, video.stat().st_size)
            with self.lock:
                duplicate_of = duplicate_of or self.reserved_hashes.get(digest)
                if duplicate_of:
                    job = self.manifest["jobs"][work_id]
                    job.update(
                        {
                            "status": "duplicate",
                            "sha256": digest,
                            "duplicate_of": duplicate_of,
                            "finished_at": utc_now(),
                        }
                    )
                    self._save_manifest_locked()
                    return work_id, "duplicate"
                self.reserved_hashes[digest] = f"work_id:{work_id}"

            cover_mode = self._make_cover(str(row.get("poster_url") or "").strip(), video, cover, raw_cover)
            contact = self._make_contact_sheet(work_id, video, actual_duration)

            with self.lock:
                local_id = self._allocate_id_locked()
                metadata = self._metadata_for(row, local_id=local_id, duration=int(round(actual_duration)))
                json_stage = stage / "metadata.json"
                json_stage.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                final_video = self.output / f"application ({local_id}).mp4"
                final_json = self.output / f"application ({local_id}).mp4.json"
                final_cover = self.output / f"application ({local_id}).octet-stream_thumb.jpg"
                # The showcase discovers assets from MP4 files, so publish the MP4
                # last. It can never observe a video before its JSON and cover exist.
                os.replace(cover, final_cover)
                published_moves.append((final_cover, cover))
                os.replace(json_stage, final_json)
                published_moves.append((final_json, json_stage))
                os.replace(video, final_video)
                published_moves.append((final_video, video))

                self.existing_hashes[digest] = final_video.name
                self.reserved_hashes.pop(digest, None)
                stat = final_video.stat()
                self.existing_sizes.setdefault(stat.st_size, []).append(final_video)
                self.manifest.setdefault("existing_hashes", {})[final_video.name] = {
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "sha256": digest,
                }
                job = self.manifest["jobs"][work_id]
                job.update(
                    {
                        "status": "completed",
                        "local_id": local_id,
                        "sha256": digest,
                        "actual_duration_seconds": round(actual_duration, 3),
                        "cover_mode": cover_mode,
                        "contact_sheet": str(contact),
                        "metadata": metadata,
                        "files": {
                            "video": final_video.name,
                            "json": final_json.name,
                            "cover": final_cover.name,
                        },
                        "finished_at": utc_now(),
                    }
                )
                self._save_manifest_locked()
            shutil.rmtree(stage, ignore_errors=True)
            return work_id, "completed"
        except Exception as exc:
            with self.lock:
                for published, staged in reversed(published_moves):
                    if published.exists() and not staged.exists():
                        os.replace(published, staged)
                if digest:
                    self.reserved_hashes.pop(digest, None)
                job = self.manifest["jobs"][work_id]
                job["status"] = "failed"
                job["error"] = f"{type(exc).__name__}: {exc}"
                job["finished_at"] = utc_now()
                self._save_manifest_locked()
            return work_id, "failed"

    def run(self) -> int:
        eligible = self.crawl()
        run_actions: dict[str, int] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = [executor.submit(self.process_one, row) for row in eligible]
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                work_id, status = future.result()
                run_actions[status] = run_actions.get(status, 0) + 1
                print(
                    f"import {index}/{len(futures)} work_id={work_id} "
                    f"status={status} counts={run_actions}",
                    flush=True,
                )

        with self.lock:
            eligible_ids = {str(row.get("work_id")) for row in eligible}
            state_counts: dict[str, int] = {}
            for work_id, job in self.manifest.get("jobs", {}).items():
                if work_id not in eligible_ids:
                    continue
                state = str(job.get("status") or "unknown")
                state_counts[state] = state_counts.get(state, 0) + 1
            self.manifest["summary"] = {
                "eligible": len(eligible),
                "completed": state_counts.get("completed", 0),
                "duplicates": state_counts.get("duplicate", 0),
                "failed": state_counts.get("failed", 0),
                "run_actions": run_actions,
                "finished_at": utc_now(),
            }
            self._save_manifest_locked()
        print(json.dumps(self.manifest["summary"], ensure_ascii=False), flush=True)
        return 1 if state_counts.get("failed") or run_actions.get("failed") else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Import long Huanzhou videos into the LingYaQQ showcase directory")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--retries", type=int, default=4)
    args = parser.parse_args()
    importer = HuanzhouImporter(
        output=args.output,
        page_size=args.page_size,
        workers=args.workers,
        retries=args.retries,
    )
    return importer.run()


if __name__ == "__main__":
    raise SystemExit(main())
