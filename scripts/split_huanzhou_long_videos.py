from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from platforms.lingya_qq.publish import build_creation_process_text


DEFAULT_OUTPUT = Path(r"D:\tmp\tg-videos\video_files")
APPLICATION_RE = re.compile(r"^application \((\d+)\)\.mp4$", re.IGNORECASE)


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def segment_plan(duration: float, target: float = 80.0, minimum: float = 60.0) -> list[tuple[float, float]]:
    if duration <= 120.0:
        return [(0.0, duration)]
    count = max(2, int(math.floor(duration / target + 0.5)))
    while count > 2 and duration / count < minimum:
        count -= 1
    width = duration / count
    if width < minimum:
        raise RuntimeError(f"Cannot split {duration:.3f}s into segments of at least {minimum:.3f}s")
    return [
        (index * width, duration - index * width if index == count - 1 else width)
        for index in range(count)
    ]


class LongVideoSplitter:
    def __init__(self, output: Path) -> None:
        self.output = output.resolve()
        self.manifest_path = self.output / "huanzhou_import_manifest.json"
        self.staging = self.output / ".huanzhou_split"
        self.contact_dir = self.output / "analysis_frames" / "huanzhou"
        self.ffmpeg = locate_binary(
            "ffmpeg",
            Path(r"D:\workspace_github\ffmpeg-win64-v4.2.1\bin\ffmpeg.exe"),
        )
        self.ffprobe = locate_binary(
            "ffprobe",
            Path(r"D:\workspace_github\ffmpeg-win64-v4.2.1\bin\ffprobe.exe"),
        )
        self.staging.mkdir(parents=True, exist_ok=True)
        self.contact_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.next_id = self._next_id()

    def _next_id(self) -> int:
        values = []
        for path in self.output.glob("application (*).mp4"):
            match = APPLICATION_RE.match(path.name)
            if match:
                values.append(int(match.group(1)))
        return max(values, default=0) + 1

    def _allocate_id(self) -> int:
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

    def _save_manifest(self) -> None:
        self.manifest["updated_at"] = utc_now()
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.manifest_path)

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
        copy_command = [
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
        run_checked(copy_command)
        actual = self._probe(destination)
        if actual >= 59.5:
            return
        destination.unlink(missing_ok=True)
        # Rare sources with sparse keyframes can create a short stream-copy tail.
        # Re-encode only that segment to guarantee an independently playable clip.
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
        if actual < 59.5:
            raise RuntimeError(f"Segment remained shorter than 60 seconds after fallback: {actual:.3f}")

    def _make_cover(self, video: Path, destination: Path) -> None:
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
            raise RuntimeError("Segment cover is not JPEG")

    def _make_contact(self, work_id: str, index: int, video: Path, duration: float, destination: Path) -> None:
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

    @staticmethod
    def _files(local_id: int, root: Path) -> dict[str, Path]:
        return {
            "video": root / f"application ({local_id}).mp4",
            "json": root / f"application ({local_id}).mp4.json",
            "cover": root / f"application ({local_id}).octet-stream_thumb.jpg",
        }

    def split_job(self, work_id: str, job: dict[str, Any]) -> str:
        if job.get("split_status") == "completed" and job.get("segments"):
            return "already_split"
        local_id = int(job.get("local_id") or 0)
        if local_id <= 0:
            return "skipped"
        original = self._files(local_id, self.output)
        if not all(path.exists() for path in original.values()):
            raise RuntimeError(f"Incomplete original triple for work_id={work_id} local_id={local_id}")
        actual_duration = self._probe(original["video"])
        plan = segment_plan(actual_duration)
        if len(plan) == 1:
            job["split_status"] = "not_required"
            return "not_required"

        stage = self.staging / f"work_{work_id}"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True, exist_ok=True)
        base_metadata = json.loads(original["json"].read_text(encoding="utf-8-sig"))
        ids = [local_id] + [self._allocate_id() for _ in range(len(plan) - 1)]
        staged: list[dict[str, Any]] = []
        published_extras: list[Path] = []
        backup = stage / "original"
        backup.mkdir(parents=True, exist_ok=True)
        original_moved = False
        try:
            for index, ((start, requested_duration), segment_id) in enumerate(zip(plan, ids), start=1):
                segment_dir = stage / f"segment_{index}"
                segment_dir.mkdir(parents=True, exist_ok=True)
                segment_video = segment_dir / "video.mp4"
                segment_cover = segment_dir / "cover.jpg"
                segment_json = segment_dir / "metadata.json"
                self._extract_segment(original["video"], segment_video, start, requested_duration)
                segment_duration = self._probe(segment_video)
                if segment_duration < 59.5:
                    raise RuntimeError(f"Segment {index} is too short: {segment_duration:.3f}")
                self._make_cover(segment_video, segment_cover)
                contact = self.contact_dir / f"huanzhou_{work_id}_segment_{index}.jpg"
                self._make_contact(work_id, index, segment_video, segment_duration, contact)

                metadata = dict(base_metadata)
                base_title = str(base_metadata.get("title") or f"幻舟作品 {work_id}").strip()
                metadata["title"] = f"{base_title}（{index}/{len(plan)}）"[:80]
                metadata["creation_process_text"] = build_creation_process_text(
                    title=metadata["title"],
                    description=metadata.get("intro"),
                    prompt=metadata.get("prompt"),
                    video_filename=f"application ({segment_id}).mp4",
                    duration=round(segment_duration),
                )
                segment_json.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                staged.append(
                    {
                        "index": index,
                        "local_id": segment_id,
                        "start_seconds": round(start, 3),
                        "requested_duration_seconds": round(requested_duration, 3),
                        "actual_duration_seconds": round(segment_duration, 3),
                        "sha256": sha256_file(segment_video),
                        "contact_sheet": str(contact),
                        "stage": {"video": segment_video, "json": segment_json, "cover": segment_cover},
                    }
                )

            # Publish additional IDs first, always with MP4 last so the showcase
            # cannot discover an incomplete material triple.
            for item in staged[1:]:
                final = self._files(item["local_id"], self.output)
                for key in ("cover", "json", "video"):
                    os.replace(item["stage"][key], final[key])
                    published_extras.append(final[key])

            backup_files = self._files(local_id, backup)
            for key, source in original.items():
                os.replace(source, backup_files[key])
            original_moved = True
            first = staged[0]
            for key in ("cover", "json", "video"):
                os.replace(first["stage"][key], original[key])

            job["source_actual_duration_seconds"] = round(actual_duration, 3)
            job["split_status"] = "completed"
            job["split_finished_at"] = utc_now()
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
            self._save_manifest()
            shutil.rmtree(stage, ignore_errors=True)
            return "split"
        except Exception:
            if original_moved:
                backup_files = self._files(local_id, backup)
                for path in original.values():
                    path.unlink(missing_ok=True)
                for key, saved in backup_files.items():
                    if saved.exists():
                        os.replace(saved, original[key])
            for path in published_extras:
                path.unlink(missing_ok=True)
            job["split_status"] = "failed"
            job["split_error"] = "split transaction rolled back"
            self._save_manifest()
            raise

    def run(self) -> int:
        run_actions: dict[str, int] = {}
        jobs = self.manifest.get("jobs") if isinstance(self.manifest.get("jobs"), dict) else {}
        completed = [(work_id, job) for work_id, job in jobs.items() if job.get("status") == "completed"]
        for index, (work_id, job) in enumerate(completed, start=1):
            try:
                status = self.split_job(work_id, job)
            except Exception as exc:
                status = "failed"
                job["split_error"] = f"{type(exc).__name__}: {exc}"
                self._save_manifest()
            run_actions[status] = run_actions.get(status, 0) + 1
            print(
                f"split {index}/{len(completed)} work_id={work_id} "
                f"status={status} counts={run_actions}",
                flush=True,
            )

        state_counts: dict[str, int] = {}
        segment_count = 0
        for _, job in completed:
            state = str(job.get("split_status") or "unknown")
            state_counts[state] = state_counts.get(state, 0) + 1
            if state == "completed" and isinstance(job.get("segments"), list):
                segment_count += len(job["segments"])
        self.manifest["split_summary"] = {
            "eligible_sources": len(completed),
            "split_sources": state_counts.get("completed", 0),
            "not_required_sources": state_counts.get("not_required", 0),
            "failed_sources": state_counts.get("failed", 0),
            "output_segments": segment_count,
            "run_actions": run_actions,
            "processed_at": utc_now(),
        }
        self._save_manifest()
        print(json.dumps(self.manifest["split_summary"], ensure_ascii=False), flush=True)
        return 1 if run_actions.get("failed") or state_counts.get("failed") else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Split imported Huanzhou videos longer than two minutes")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    return LongVideoSplitter(args.output).run()


if __name__ == "__main__":
    raise SystemExit(main())
