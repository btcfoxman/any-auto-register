from __future__ import annotations

import pytest

from scripts.import_huanzhou_videos import parse_duration
from scripts.split_huanzhou_long_videos import segment_plan


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("01:00", 60),
        ("01:31", 91),
        ("1:02:03", 3723),
        ("120", 120),
        ("", 0),
        ("invalid", 0),
    ],
)
def test_parse_duration(value: str, expected: int) -> None:
    assert parse_duration(value) == expected


@pytest.mark.parametrize("duration", [120.001, 121.0, 159.0, 200.0, 265.056, 633.0])
def test_segment_plan_balances_long_videos_without_short_tail(duration: float) -> None:
    plan = segment_plan(duration)

    assert len(plan) >= 2
    assert plan[0][0] == 0
    assert sum(segment_duration for _, segment_duration in plan) == pytest.approx(duration)
    assert all(segment_duration >= 60 for _, segment_duration in plan)
    assert all(segment_duration <= 120 for _, segment_duration in plan)
    for previous, current in zip(plan, plan[1:]):
        assert previous[0] + previous[1] == pytest.approx(current[0])


def test_segment_plan_keeps_video_at_two_minute_threshold_whole() -> None:
    assert segment_plan(120.0) == [(0.0, 120.0)]
