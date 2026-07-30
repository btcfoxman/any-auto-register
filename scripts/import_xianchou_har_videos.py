from __future__ import annotations

import argparse
import concurrent.futures
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

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.xianchou_media_common import build_metadata, normalize_text, parse_har_projects, segment_plan


DEFAULT_HAR = Path(__file__).resolve().parents[1] / "tmp" / "xianchou.com.har"
DEFAULT_OUTPUT = Path(r"D:\tmp\tg-videos\video_files")
APPLICATION_RE = re.compile(r"^application \((\d+)\)\.mp4$", re.IGNORECASE)
REQUEST_HEADERS = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "referer": "https://xianchou.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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


class XianchouHarImporter:
    def __init__(
        self,
        *,
        har: Path,
        output: Path,
        workers: int,
        retries: int,
        start_id: int,
    ) -> None:
        self.har = har.resolve()
        self.output = output.resolve()
        self.workers = max(1, workers)
        self.retries = max(1, retries)
        self.start_id = max(1, start_id)
        self.staging = self.output / ".xianchou_import"
        self.contact_dir = self.output / "analysis_frames" / "xianchou"
        self.manifest_path = self.output / "xianchou_import_manifest.json"
        self.lock = threading.RLock()
        self.local = threading.local()
        self.ffmpeg = locate_binary("ffmpeg", Path(r"D:\workspace_github\ffmpeg-win64-v4.2.1\bin\ffmpeg.exe"))
        self.ffprobe = locate_binary("ffprobe", Path(r"D:\workspace_github\ffmpeg-win64-v4.2.1\bin\ffprobe.exe"))
        self.output.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        self.contact_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = self._load_manifest()
        self.tag_catalog = self._load_tag_catalog()
        self.next_id = self._next_application_id()

    def _session(self) -> requests.Session:
        session = getattr(self.local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            session.headers.update(REQUEST_HEADERS)
            self.local.session = session
        return session

    def _load_manifest(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            try:
                payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    payload.setdefault("jobs", {})
                    return payload
            except Exception:
                pass
        return {
            "schema_version": 1,
            "source_har": str(self.har),
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
                tag_id = normalize_text(item.get("id"))
                title = normalize_text(item.get("title"))
                if tag_id and title:
                    catalog[title] = {"id": tag_id, "title": title, "alias": normalize_text(item.get("alias"))}
        if "视觉艺术" not in catalog:
            raise RuntimeError("Existing material tag catalog does not contain the 视觉艺术 fallback tag")
        return catalog

    def _next_application_id(self) -> int:
        ids = [self.start_id - 1]
        for path in self.output.glob("application (*).mp4"):
            match = APPLICATION_RE.match(path.name)
            if match:
                ids.append(int(match.group(1)))
        for job in self.manifest.get("jobs", {}).values():
            ids.extend(int(value) for value in job.get("reserved_ids") or [])
        return max(ids) + 1

    def _allocate_ids_locked(self, count: int) -> list[int]:
        values: list[int] = []
        while len(values) < count:
            current = self.next_id
            self.next_id += 1
            names = (
                f"application ({current}).mp4",
                f"application ({current}).mp4.json",
                f"application ({current}).octet-stream_thumb.jpg",
            )
            if not any((self.output / name).exists() for name in names):
                values.append(current)
        return values

    def _request(self, url: str, *, stream: bool = False, headers: dict[str, str] | None = None) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self._session().get(url, stream=stream, headers=headers or {}, timeout=(30, 300))
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise
                time.sleep(min(2 ** (attempt - 1), 8))
        raise RuntimeError(f"Request failed: {url}: {last_error}")

    def _download(self, url: str, destination: Path) -> None:
        if destination.exists() and destination.stat().st_size > 0:
            return
        partial = destination.with_suffix(destination.suffix + ".part")
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            response: requests.Response | None = None
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                response = self._request(url, stream=True, headers={"range": f"bytes={offset}-"} if offset else {})
                append = offset > 0 and response.status_code == 206
                with partial.open("ab" if append else "wb") as handle:
                    for chunk in response.iter_content(chunk_size=2 * 1024 * 1024):
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

    def _probe_duration(self, path: Path) -> float:
        result = run_checked([
            self.ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ])
        return float(result.stdout.strip())

    def _has_audio(self, path: Path) -> bool:
        result = run_checked([
            self.ffprobe, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
        ])
        return bool(result.stdout.strip())

    def _audio_input_options(self, path: Path) -> list[str]:
        result = run_checked([
            self.ffprobe, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,codec_tag_string", "-of", "json", str(path),
        ])
        streams = json.loads(result.stdout or "{}").get("streams") or []
        if not streams:
            return []
        stream = streams[0]
        if not normalize_text(stream.get("codec_name")) and normalize_text(stream.get("codec_tag_string")).lower() == "ipcm":
            # ffmpeg 4.2 predates automatic decoding for ISO-BMFF ipcm tracks,
            # but the captured sources use signed 16-bit little-endian PCM.
            return ["-c:a", "pcm_s16le"]
        return []

    def _extend_to_sixty(self, source: Path, destination: Path, duration: float) -> None:
        padding = max(60.1 - duration, 0.1)
        command = [
            self.ffmpeg, "-y", "-loglevel", "error", *self._audio_input_options(source), "-i", str(source),
            "-map", "0:v:0", "-vf",
            f"scale=trunc(iw/2)*2:trunc(ih/2)*2,tpad=stop_mode=clone:stop_duration={padding:.6f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        ]
        if self._has_audio(source):
            command.extend(["-map", "0:a:0", "-af", f"apad=pad_dur={padding:.6f}", "-c:a", "aac", "-b:a", "192k"])
        else:
            command.append("-an")
        command.extend(["-t", "60.05", "-movflags", "+faststart", str(destination)])
        run_checked(command)
        actual = self._probe_duration(destination)
        if actual < 59.95:
            raise RuntimeError(f"Extended video is still shorter than 60 seconds: {actual:.3f}")

    def _extract_segment(self, source: Path, destination: Path, start: float, duration: float) -> None:
        run_checked([
            self.ffmpeg, "-y", "-loglevel", "error", "-ss", f"{start:.6f}", "-i", str(source),
            "-t", f"{duration:.6f}", "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
            "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(destination),
        ])
        actual = self._probe_duration(destination)
        if actual >= 59.5 and abs(actual - duration) <= 3.0:
            return
        destination.unlink(missing_ok=True)
        run_checked([
            self.ffmpeg, "-y", "-loglevel", "error", "-ss", f"{start:.6f}", "-i", str(source),
            "-t", f"{duration:.6f}", "-map", "0:v:0", "-map", "0:a?",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(destination),
        ])
        actual = self._probe_duration(destination)
        if actual < 59.5:
            raise RuntimeError(f"Segment is shorter than 60 seconds: {actual:.3f}")

    def _make_cover(self, video: Path, destination: Path, *, remote_url: str = "", raw_cover: Path | None = None) -> str:
        mode = "video_frame"
        if remote_url and raw_cover is not None:
            try:
                self._download(remote_url, raw_cover)
                run_checked([
                    self.ffmpeg, "-y", "-loglevel", "error", "-i", str(raw_cover),
                    "-frames:v", "1", "-q:v", "2", str(destination),
                ])
                mode = "remote"
            except Exception:
                destination.unlink(missing_ok=True)
        if not destination.exists() or destination.stat().st_size <= 2 or destination.read_bytes()[:2] != b"\xff\xd8":
            run_checked([
                self.ffmpeg, "-y", "-loglevel", "error", "-ss", "1", "-i", str(video),
                "-frames:v", "1", "-q:v", "2", str(destination),
            ])
            mode = "video_frame"
        if destination.read_bytes()[:2] != b"\xff\xd8":
            raise RuntimeError("Cover conversion did not produce JPEG")
        return mode

    def _make_contact_sheet(self, project_id: str, video: Path, duration: float) -> Path:
        destination = self.contact_dir / f"xianchou_{project_id}.jpg"
        if destination.exists() and destination.stat().st_size > 0:
            return destination
        frames = [destination.with_name(f"{destination.stem}.frame_{index}.jpg") for index in range(5)]
        temporary = destination.with_suffix(".tmp.jpg")
        try:
            for frame, fraction in zip(frames, (0.08, 0.27, 0.5, 0.73, 0.92)):
                run_checked([
                    self.ffmpeg, "-y", "-loglevel", "error", "-ss", f"{max(duration * fraction, 0.5):.6f}",
                    "-i", str(video), "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "3", str(frame),
                ])
            command = [self.ffmpeg, "-y", "-loglevel", "error"]
            for frame in frames:
                command.extend(["-i", str(frame)])
            command.extend([
                "-filter_complex", "[0:v][1:v][2:v][3:v][4:v]hstack=inputs=5[v]",
                "-map", "[v]", "-frames:v", "1", "-q:v", "3", str(temporary),
            ])
            run_checked(command)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
            for frame in frames:
                frame.unlink(missing_ok=True)
        return destination

    def _files_complete(self, local_id: int) -> bool:
        return all((self.output / name).exists() and (self.output / name).stat().st_size > 0 for name in (
            f"application ({local_id}).mp4",
            f"application ({local_id}).mp4.json",
            f"application ({local_id}).octet-stream_thumb.jpg",
        ))

    def _job_complete(self, job: dict[str, Any]) -> bool:
        segments = job.get("segments")
        return bool(job.get("status") == "completed" and isinstance(segments, list) and segments and all(self._files_complete(int(item["local_id"])) for item in segments))

    def process_one(self, row: dict[str, Any]) -> tuple[str, str]:
        project_id = normalize_text(row.get("id"))
        with self.lock:
            job = self.manifest["jobs"].setdefault(project_id, {"project_id": row.get("id"), "source": row})
            if self._job_complete(job):
                return project_id, "already_completed"
            if job.get("status") == "filtered_actual_short":
                return project_id, "already_filtered"
            job.update({"source": row, "status": "processing", "started_at": utc_now(), "error": ""})
            self._save_manifest_locked()

        stage = self.staging / f"project_{project_id}"
        stage.mkdir(parents=True, exist_ok=True)
        source = stage / "source.mp4"
        published: list[tuple[Path, Path]] = []
        try:
            video_url = normalize_text(row.get("videoUrl"))
            if not video_url:
                raise RuntimeError("videoUrl is empty")
            self._download(video_url, source)
            actual_duration = self._probe_duration(source)
            if actual_duration < 44.95:
                with self.lock:
                    job = self.manifest["jobs"][project_id]
                    job.update({"status": "filtered_actual_short", "actual_duration_seconds": round(actual_duration, 3), "finished_at": utc_now()})
                    self._save_manifest_locked()
                shutil.rmtree(stage, ignore_errors=True)
                return project_id, "filtered_actual_short"

            if not normalize_text(row.get("description")):
                contact = self._make_contact_sheet(project_id, source, actual_duration)
            else:
                contact = None

            requested_plan = segment_plan(actual_duration)
            videos: list[Path] = []
            if actual_duration < 59.95:
                segment_dir = stage / "segment_1"
                segment_dir.mkdir(exist_ok=True)
                extended = segment_dir / "video.mp4"
                self._extend_to_sixty(source, extended, actual_duration)
                videos = [extended]
                requested_plan = [(0.0, 60.0)]
                processing_mode = "extended_to_60"
            elif len(requested_plan) == 1:
                videos = [source]
                processing_mode = "original"
            else:
                processing_mode = "split"
                for index, (start, duration) in enumerate(requested_plan, start=1):
                    segment_dir = stage / f"segment_{index}"
                    segment_dir.mkdir(exist_ok=True)
                    segment_video = segment_dir / "video.mp4"
                    self._extract_segment(source, segment_video, start, duration)
                    videos.append(segment_video)

            durations = [self._probe_duration(video) for video in videos]
            if any(duration < 59.5 for duration in durations):
                raise RuntimeError(f"One or more output segments are shorter than 60 seconds: {durations}")

            with self.lock:
                job = self.manifest["jobs"][project_id]
                reserved = job.get("reserved_ids")
                if not isinstance(reserved, list) or len(reserved) != len(videos):
                    reserved = self._allocate_ids_locked(len(videos))
                    job["reserved_ids"] = reserved
                local_ids = [int(value) for value in reserved]
                self._save_manifest_locked()

            staged_items: list[dict[str, Any]] = []
            raw_cover = stage / "remote_cover.source"
            for index, (video, duration, local_id) in enumerate(zip(videos, durations, local_ids), start=1):
                segment_dir = video.parent
                cover = segment_dir / "cover.jpg"
                metadata_file = segment_dir / "metadata.json"
                cover_mode = self._make_cover(
                    video,
                    cover,
                    remote_url=normalize_text(row.get("coverUrl")) if len(videos) == 1 else "",
                    raw_cover=raw_cover,
                )
                metadata = build_metadata(
                    title=normalize_text(row.get("title")),
                    description=normalize_text(row.get("description")),
                    source_tags=row.get("tags") if isinstance(row.get("tags"), list) else [],
                    seed=f"xianchou-{project_id}-{local_id}",
                    tag_catalog=self.tag_catalog,
                    segment_index=index,
                    segment_count=len(videos),
                )
                metadata_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                staged_items.append({
                    "local_id": local_id,
                    "video": video,
                    "cover": cover,
                    "metadata_file": metadata_file,
                    "metadata": metadata,
                    "duration": duration,
                    "cover_mode": cover_mode,
                })

            segment_records: list[dict[str, Any]] = []
            with self.lock:
                for item in staged_items:
                    local_id = item["local_id"]
                    final_video = self.output / f"application ({local_id}).mp4"
                    final_json = self.output / f"application ({local_id}).mp4.json"
                    final_cover = self.output / f"application ({local_id}).octet-stream_thumb.jpg"
                    os.replace(item["cover"], final_cover)
                    published.append((final_cover, item["cover"]))
                    os.replace(item["metadata_file"], final_json)
                    published.append((final_json, item["metadata_file"]))
                    os.replace(item["video"], final_video)
                    published.append((final_video, item["video"]))
                    segment_records.append({
                        "local_id": local_id,
                        "index": len(segment_records) + 1,
                        "duration_seconds": round(item["duration"], 3),
                        "cover_mode": item["cover_mode"],
                        "files": {"video": final_video.name, "json": final_json.name, "cover": final_cover.name},
                    })
                job = self.manifest["jobs"][project_id]
                job.update({
                    "status": "completed",
                    "actual_duration_seconds": round(actual_duration, 3),
                    "processing_mode": processing_mode,
                    "contact_sheet": str(contact) if contact else "",
                    "segments": segment_records,
                    "finished_at": utc_now(),
                })
                self._save_manifest_locked()
            shutil.rmtree(stage, ignore_errors=True)
            return project_id, "completed"
        except Exception as exc:
            with self.lock:
                for final, staged in reversed(published):
                    if final.exists() and not staged.exists():
                        staged.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(final, staged)
                job = self.manifest["jobs"][project_id]
                job.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}", "finished_at": utc_now()})
                self._save_manifest_locked()
            return project_id, "failed"

    def run(self) -> int:
        projects = parse_har_projects(self.har)
        eligible = [row for row in projects if float(row.get("duration") or 0) >= 45]
        with self.lock:
            jobs = self.manifest.setdefault("jobs", {})
            for row in projects:
                project_id = normalize_text(row.get("id"))
                job = jobs.setdefault(project_id, {"project_id": row.get("id")})
                job["source"] = row
                if float(row.get("duration") or 0) < 45 and not job.get("status"):
                    job["status"] = "filtered_declared_short"
            self.manifest["har_summary"] = {
                "projects": len(projects),
                "eligible_declared": len(eligible),
                "filtered_declared_short": len(projects) - len(eligible),
                "loaded_at": utc_now(),
            }
            self._save_manifest_locked()

        counts: dict[str, int] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = [executor.submit(self.process_one, row) for row in eligible]
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                project_id, status = future.result()
                counts[status] = counts.get(status, 0) + 1
                print(f"xianchou {index}/{len(futures)} project={project_id} status={status} counts={counts}", flush=True)

        with self.lock:
            eligible_ids = {normalize_text(row.get("id")) for row in eligible}
            states: dict[str, int] = {}
            output_sets = 0
            for project_id, job in self.manifest["jobs"].items():
                if project_id not in eligible_ids:
                    continue
                state = normalize_text(job.get("status")) or "unknown"
                states[state] = states.get(state, 0) + 1
                if state == "completed":
                    output_sets += len(job.get("segments") or [])
            self.manifest["summary"] = {
                "eligible_projects": len(eligible),
                "states": states,
                "output_sets": output_sets,
                "run_actions": counts,
                "finished_at": utc_now(),
            }
            self._save_manifest_locked()
        print(json.dumps(self.manifest["summary"], ensure_ascii=False), flush=True)
        return 1 if states.get("failed") else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Xianchou videos captured in a HAR file")
    parser.add_argument("--har", type=Path, default=DEFAULT_HAR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--start-id", type=int, default=920)
    args = parser.parse_args()
    return XianchouHarImporter(
        har=args.har,
        output=args.output,
        workers=args.workers,
        retries=args.retries,
        start_id=args.start_id,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
