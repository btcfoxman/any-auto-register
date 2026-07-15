from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from platforms.lingya_qq.publish import build_creation_process_text


DEFAULT_OUTPUT = Path(r"D:\tmp\tg-videos\video_files")

TAGS: dict[str, dict[str, str]] = {
    "古装": {"id": "tag_2QCVIf1DjT", "title": "古装", "alias": ""},
    "悬疑惊悚": {"id": "tag_2QCVIf1DjP", "title": "悬疑惊悚", "alias": ""},
    "奇幻": {"id": "tag_2QCVIf1DjN", "title": "奇幻", "alias": ""},
    "科幻": {"id": "tag_2QCVIf1DjJ", "title": "科幻", "alias": ""},
    "犯罪": {"id": "tag_5WGCJ7xQop", "title": "犯罪", "alias": ""},
    "历史人文": {"id": "tag_2QCVIf1DjV", "title": "历史人文", "alias": ""},
    "VLOG": {"id": "tag_5WGvjGlPk5", "title": "VLOG", "alias": ""},
}

# These entries had no usable description in the source API.  The text below is
# based on the five-frame contact sheets produced by import_huanzhou_videos.py.
VISUAL_ENRICHMENTS: dict[str, dict[str, str]] = {
    "3990": {
        "title": "幽冥双生",
        "intro": "冷色古风奇幻短片，神秘女子、烟雾化身与双生人物在幽暗空间交替出现，围绕执念与消散营造诡谲氛围。",
        "prompt": "塑造冷峻的古风女子与烟雾幻象，让双生人物、法器和破碎遗物在幽暗空间中依次显现，以低饱和光影和缓慢运镜强化神秘感。",
        "tag": "奇幻",
    },
    "2720": {
        "title": "郭靖VS欧阳锋·庭院激战",
        "intro": "水墨动漫风武侠对决，郭靖与欧阳锋在竹林庭院交锋，金色掌力与紫色毒功连续碰撞，招式凌厉、节奏紧凑。",
        "prompt": "呈现竹林庭院中的武侠高手对决，用漫画线条描绘腾挪、对掌与气劲爆发，突出金紫两色内力碰撞和碎石飞散的冲击力。",
        "tag": "古装",
    },
    "1521": {
        "title": "古镇悬案",
        "intro": "古装悬疑动画，从染血字条与异常手臂切入，人物循线进入阴暗牢房调查，在层层线索中逼近古镇案件真相。",
        "prompt": "以冷色古镇和幽暗牢房为主场景，通过染血字条、诡异手臂和调查者的谨慎行动推进案情，保持压迫感与线索递进。",
        "tag": "悬疑惊悚",
    },
    "822": {
        "title": "去年做的第一个AI短片，章鱼侠大乱斗",
        "intro": "科幻怪兽短片，从实验室里的章鱼样本延伸到海岸与城市废墟，巨型章鱼怪现身街区，形成实验失控后的灾难叙事。",
        "prompt": "从高科技实验室逐步转向海岸和破败街区，让章鱼生物由样本成长为巨型怪兽，以尺度变化和灾难场景强化科幻冲突。",
        "tag": "科幻",
    },
    "336": {
        "title": "《神仙微信群》01",
        "intro": "现代都市奇幻动画，青年通过手机聊天群卷入离奇事件，街巷冲突与神话人物交替出现，现实生活由此连接神仙世界。",
        "prompt": "以都市青年查看神秘手机群聊为开端，穿插街巷冲突和神话角色现身，让现代场景与东方奇幻元素自然衔接。",
        "tag": "奇幻",
    },
    "244": {
        "title": "千与千寻第一集",
        "intro": "动画剧情剪辑，串联红色发圈、夕阳原野、无脸男、千寻与夜色汤屋等画面，呈现少女进入奇异世界后的相遇与成长。",
        "prompt": "围绕少女在奇异世界中的旅程组织镜头，以发圈、无脸男和灯火通明的汤屋作为视觉线索，营造温暖又神秘的动画氛围。",
        "tag": "奇幻",
    },
    "241": {
        "title": "觉醒之域",
        "intro": "未来科技概念片，以无人机、火星新家园、智能交互桌和未来研究中心构建数字化基地，展现人类探索新空间的蓝图。",
        "prompt": "设计明亮的未来基地与蓝色全息界面，串联无人机运输、火星穹顶家园、智能工作台和研究中心，突出科技探索主题。",
        "tag": "科幻",
    },
    "239": {
        "title": "议案调查组",
        "intro": "都市犯罪悬疑短片，血迹现场、豪车、怀抱婴儿的女子与进入房间调查的侍者构成多重线索，逐步揭开异常案件。",
        "prompt": "以血迹斑驳的房间作为案件核心，交叉呈现豪车、神色紧张的人物和调查者进入现场的过程，用线索切换制造犯罪悬念。",
        "tag": "犯罪",
    },
    "215": {
        "title": "成语故事-愚公移山",
        "intro": "三维动画演绎愚公移山寓言，老人带领孩子丈量、搬运山石，在家人与乡邻支持下以长期坚持改变生活环境。",
        "prompt": "用温暖明亮的三维动画重现山村寓言，表现老人规划、众人搬石和家人相伴的过程，突出坚持不懈与代际传承。",
        "tag": "历史人文",
    },
    "214": {
        "title": "山海经",
        "intro": "东方玄幻短片，持书修行者游历云海群山，鲲、青龙等山海异兽相继现身，展现古籍被唤醒后的宏大神话世界。",
        "prompt": "在云海群山中安排白衣修行者翻阅古籍，以巨鲲、青龙和金色法光依次展开山海异象，营造恢宏的东方神话意境。",
        "tag": "奇幻",
    },
    "209": {
        "title": "音乐MV",
        "intro": "清新海岛旅行音乐影像，记录海边拍摄、复古巴士、情侣漫步、白色街区与椰青特写，呈现轻松明亮的度假氛围。",
        "prompt": "以阳光海滩和蓝绿色海水作为主色调，串联旅拍、复古巴士、情侣散步与海边饮品，让镜头保持轻快的音乐节奏。",
        "tag": "VLOG",
    },
    "208": {
        "title": "AI国庆短片",
        "intro": "国庆主题视觉短片，从锦绣中国祝福画面延伸到数字创作、城市立交与传统手作，展现现代科技和节日文化交融。",
        "prompt": "运用中国红、城市夜景和节庆灯笼组织国庆主题画面，穿插数字创作与传统手艺，呈现科技发展和家国祝福。",
        "tag": "历史人文",
    },
    "154": {
        "title": "古人蹦迪",
        "intro": "新中式水墨趣味动画，古代乐师、舞者与现代乐器同场演出，在园林山水间形成跨越时代的热闹歌舞场景。",
        "prompt": "以淡彩水墨园林为舞台，让古装乐师和舞者搭配键盘、架子鼓等现代乐器表演，用群舞构图制造轻松诙谐的反差。",
        "tag": "古装",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def metadata_for(
    enrichment: dict[str, str], *, title: str, local_id: int, duration: float
) -> dict[str, Any]:
    intro = enrichment["intro"]
    prompt = enrichment["prompt"]
    return {
        "title": title[:80],
        "intro": intro,
        "prompt": prompt,
        "tag_infos": [dict(TAGS[enrichment["tag"]])],
        "creation_process_text": build_creation_process_text(
            title=title,
            description=intro,
            prompt=prompt,
            video_filename=f"application ({local_id}).mp4",
            duration=round(duration),
        ),
    }


def enrich(output: Path) -> dict[str, int]:
    manifest_path = output / "huanzhou_import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    jobs = manifest.get("jobs") or {}
    counts = {"jobs": 0, "files": 0, "missing_jobs": 0, "missing_files": 0}

    for work_id, enrichment in VISUAL_ENRICHMENTS.items():
        job = jobs.get(work_id)
        if not isinstance(job, dict) or job.get("status") != "completed":
            counts["missing_jobs"] += 1
            continue
        segments = job.get("segments") if job.get("split_status") == "completed" else None
        if not isinstance(segments, list) or not segments:
            segments = [
                {
                    "local_id": job["local_id"],
                    "actual_duration_seconds": job.get("actual_duration_seconds", 0),
                }
            ]
        for index, segment in enumerate(segments, start=1):
            local_id = int(segment["local_id"])
            suffix = f"（{index}/{len(segments)}）" if len(segments) > 1 else ""
            title = f"{enrichment['title']}{suffix}"
            duration = float(segment.get("actual_duration_seconds") or job.get("actual_duration_seconds") or 0)
            payload = metadata_for(
                enrichment,
                title=title,
                local_id=local_id,
                duration=duration,
            )
            metadata_path = output / f"application ({local_id}).mp4.json"
            if not metadata_path.exists():
                counts["missing_files"] += 1
                continue
            write_json_atomic(metadata_path, payload)
            counts["files"] += 1

        base_id = int(job["local_id"])
        job["metadata"] = metadata_for(
            enrichment,
            title=enrichment["title"],
            local_id=base_id,
            duration=float(job.get("actual_duration_seconds") or job.get("source_actual_duration_seconds") or 0),
        )
        job["visual_enrichment"] = {
            "source": "five_frame_contact_sheet",
            "contact_sheet": job.get("contact_sheet", ""),
            "enriched_at": utc_now(),
        }
        counts["jobs"] += 1

    manifest["updated_at"] = utc_now()
    manifest["visual_enrichment_summary"] = {**counts, "processed_at": utc_now()}
    write_json_atomic(manifest_path, manifest)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich Huanzhou metadata from generated contact sheets")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    counts = enrich(args.output.resolve())
    print(json.dumps(counts, ensure_ascii=False))
    return 1 if counts["missing_jobs"] or counts["missing_files"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
