from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.xianchou_media_common import (
    build_creation_process,
    build_intro,
    build_prompt,
    is_generic_intro,
    normalize_text,
)


DEFAULT_OUTPUT = Path(r"D:\tmp\tg-videos\video_files")
APPLICATION_JSON_RE = re.compile(r"^application \((\d+)\)\.mp4\.json$", re.IGNORECASE)


def _source_detail(payload: dict[str, Any]) -> str:
    title = normalize_text(payload.get("title"))
    intro = normalize_text(payload.get("intro"))
    prompt = normalize_text(payload.get("prompt"))
    if intro and intro != title and not is_generic_intro(intro):
        return intro
    for marker in (
        "保持主体清晰",
        "保持人物与场景",
        "镜头连贯",
    ):
        prompt = prompt.split(marker, 1)[0].rstrip("，。；; ")
    prompt = re.sub(r"^以[“\"]?.{0,80}?[”\"]?为主题[，,]?", "", prompt)
    prompt = re.sub(r"^重点呈现", "", prompt)
    return prompt or intro


def rewrite_metadata(output: Path, *, maximum_id: int | None, backup: Path | None) -> dict[str, int]:
    paths: list[tuple[int, Path]] = []
    for path in output.glob("application (*).mp4.json"):
        match = APPLICATION_JSON_RE.match(path.name)
        if not match:
            continue
        number = int(match.group(1))
        if maximum_id is None or number <= maximum_id:
            paths.append((number, path))
    paths.sort()

    if backup:
        backup.mkdir(parents=True, exist_ok=True)
        for _, path in paths:
            destination = backup / path.name
            if not destination.exists():
                shutil.copy2(path, destination)

    prompts: set[str] = set()
    processes: set[str] = set()
    rewritten = 0
    for number, path in paths:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        title = normalize_text(payload.get("title")) or f"影像作品 {number}"
        segment_match = re.search(r"（(\d+)/(\d+)）$", title)
        segment_index = int(segment_match.group(1)) if segment_match else 1
        segment_count = int(segment_match.group(2)) if segment_match else 1
        detail = _source_detail(payload)
        tags = [item.get("title") for item in payload.get("tag_infos") or [] if isinstance(item, dict)]
        intro = build_intro(title=title, description=detail, source_tags=tags, seed=f"history-{number}")
        for attempt in range(20):
            prompt = build_prompt(
                title=title,
                detail=intro,
                source_tags=tags,
                seed=f"rewrite-{number}-{attempt}",
                segment_index=segment_index,
                segment_count=segment_count,
            )
            if prompt not in prompts:
                break
        for attempt in range(20):
            process = build_creation_process(
                title=title,
                detail=intro,
                seed=f"rewrite-{number}-{attempt}",
                segment_index=segment_index,
                segment_count=segment_count,
            )
            if process not in processes:
                break
        prompts.add(prompt)
        processes.add(process)
        payload["title"] = title[:80]
        payload["intro"] = intro[:1200]
        payload["prompt"] = prompt[:1200]
        payload["creation_process_text"] = process[:1200]
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        rewritten += 1
    return {"rewritten": rewritten, "unique_prompts": len(prompts), "unique_processes": len(processes)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Diversify custom text in LingYaQQ video metadata")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--maximum-id", type=int)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    print(json.dumps(rewrite_metadata(args.output, maximum_id=args.maximum_id, backup=args.backup), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
