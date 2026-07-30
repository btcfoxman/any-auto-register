from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from scripts.xianchou_media_common import build_metadata, parse_har_projects, segment_plan


def test_parse_har_projects_handles_plain_and_base64_responses(tmp_path: Path) -> None:
    first = {"projects": [{"id": 1, "title": "first"}]}
    second = {"projects": [{"id": 2, "title": "second"}, {"id": 1, "title": "duplicate"}]}
    har = {
        "log": {
            "entries": [
                {"response": {"content": {"text": json.dumps(first)}}},
                {
                    "response": {
                        "content": {
                            "text": base64.b64encode(json.dumps(second).encode()).decode(),
                            "encoding": "base64",
                        }
                    }
                },
            ]
        }
    }
    path = tmp_path / "source.har"
    path.write_text(json.dumps(har), encoding="utf-8")

    assert [item["title"] for item in parse_har_projects(path)] == ["first", "second"]


@pytest.mark.parametrize("duration", [180.001, 183.0, 197.0, 218.0, 295.0, 741.0])
def test_segment_plan_splits_only_long_videos_without_short_parts(duration: float) -> None:
    plan = segment_plan(duration)
    assert len(plan) >= 2
    assert sum(part for _, part in plan) == pytest.approx(duration)
    assert all(part >= 60 for _, part in plan)


def test_segment_plan_keeps_three_minute_video_whole() -> None:
    assert segment_plan(180.0) == [(0.0, 180.0)]


def test_metadata_is_content_specific_and_seeded() -> None:
    catalog = {"视觉艺术": {"id": "fallback", "title": "视觉艺术", "alias": ""}}
    one = build_metadata(
        title="山海秘境",
        description="",
        source_tags=["AI 宣传片"],
        seed="one",
        tag_catalog=catalog,
    )
    two = build_metadata(
        title="山海秘境",
        description="",
        source_tags=["AI 宣传片"],
        seed="two",
        tag_catalog=catalog,
    )
    assert "山海秘境" not in one["prompt"]
    assert "山海秘境" not in one["creation_process_text"]
    assert "内容依据" not in one["creation_process_text"]
    assert "Seedance" not in one["creation_process_text"]
    assert one["prompt"] != two["prompt"]
    assert one["creation_process_text"] != two["creation_process_text"]
