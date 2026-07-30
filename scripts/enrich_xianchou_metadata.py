from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.xianchou_media_common import build_metadata


DEFAULT_OUTPUT = Path(r"D:\tmp\tg-videos\video_files")

# These source records had empty descriptions and titles too vague to describe
# their videos. Each override was written after reviewing the five-frame contact
# sheet produced during import.
FRAME_REVIEW_OVERRIDES: dict[str, dict[str, Any]] = {
    "57633": {
        "title": "庭院尽头的战场记忆",
        "description": "一位老人从庭院轮椅上的平静生活被带入硝烟弥漫的战场记忆；士兵在废墟中奔跑，现实与战争回忆彼此交错。",
        "tags": ["战争", "情感"],
    },
    "54351": {
        "title": "霓虹与晶体之间",
        "description": "雨夜霓虹街道、流动光影与透明晶体材质交替出现，镜头在城市倒影和抽象微观景观之间快速穿梭。",
        "tags": ["视觉艺术", "意识流"],
    },
    "56694": {
        "title": "一字请战",
        "description": "水墨书写的“我”字引出古代战场故事，年轻士兵请战、骑兵冲锋与宫殿中的权力抉择交错展开。",
        "tags": ["古装", "战争"],
    },
    "54582": {
        "title": "光落于居所",
        "description": "镜头游走于宽敞明亮的现代住宅，女性在书房与客厅活动并轻触大理石台面，突出空间设计、材质和安静的居住氛围。",
        "tags": ["都市现代", "视觉艺术"],
    },
    "52636": {
        "title": "荒漠追踪",
        "description": "荒漠中一只土拨鼠穿行逃窜，持枪男子伏地追踪并瞄准，人与野生动物在辽阔旷野形成紧张追逐。",
        "tags": ["动物", "冒险"],
    },
    "57143": {
        "title": "橘猫的九点办公室",
        "description": "一只拟人化橘猫被带进办公室，对着显示鱼类的电脑开始工作，在困惑与惊讶的表情中展开荒诞职场情节。",
        "tags": ["萌宠", "娱乐"],
    },
    "54975": {
        "title": "仙门碑前的新局",
        "description": "仙门广场上的金色石碑发光，白发古装人物与众人对峙，随后黑衣角色卷入激烈冲突，一场宗门危机由此展开。",
        "tags": ["玄幻", "古装"],
    },
    "55205": {
        "title": "金箍山河英雄卷",
        "description": "作品从金箍棒与水墨人物意象出发，穿过长城和历史人物画面，最终进入神话军阵，串联中国文化与英雄叙事。",
        "tags": ["历史人文", "奇幻"],
    },
}


def enrich(output: Path) -> dict[str, Any]:
    manifest_path = output / "xianchou_import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tag_catalog: dict[str, dict[str, str]] = {}
    for path in output.glob("application (*).mp4.json"):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        for item in payload.get("tag_infos") or []:
            if isinstance(item, dict) and item.get("title") and item.get("id"):
                tag_catalog[str(item["title"])] = dict(item)
    if "视觉艺术" not in tag_catalog:
        raise RuntimeError("Missing 视觉艺术 fallback tag")

    changed: list[int] = []
    for project_id, override in FRAME_REVIEW_OVERRIDES.items():
        job = manifest.get("jobs", {}).get(project_id) or {}
        segments = job.get("segments") or []
        for index, segment in enumerate(segments, start=1):
            local_id = int(segment["local_id"])
            metadata = build_metadata(
                title=str(override["title"]),
                description=str(override["description"]),
                source_tags=list(override["tags"]),
                seed=f"xianchou-{project_id}-{local_id}",
                tag_catalog=tag_catalog,
                segment_index=index,
                segment_count=len(segments),
            )
            path = output / f"application ({local_id}).mp4.json"
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)
            segment["frame_review_metadata"] = metadata
            changed.append(local_id)
        job["frame_review_override"] = override

    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_manifest, manifest_path)
    return {"reviewed_projects": len(FRAME_REVIEW_OVERRIDES), "changed_ids": changed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply frame-reviewed descriptions to ambiguous Xianchou records")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(enrich(args.output), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
