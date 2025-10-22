from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.api import (
    _build_quarter_windows,
    _floor_to_bucket,
    _resolve_device_metadata,
)


def test_floor_to_bucket_rounds_down_to_interval():
    moment = datetime(2024, 10, 21, 10, 7, tzinfo=timezone.utc)
    result = _floor_to_bucket(moment, bucket_minutes=5)
    assert result.hour == 10
    assert result.minute == 5
    assert result.second == 0


def test_resolve_device_metadata_matches_prefix(tmp_path: Path):
    device_dir = tmp_path / "microphones" / "backyard-mic"
    device_dir.mkdir(parents=True)
    recording_path = device_dir / "sample.wav"
    recording_path.touch()

    device_index = [
        {
            "type": "microphone",
            "id": "backyard-mic",
            "name": "backyard-mic",
            "location": "Backyard",
            "path": str(device_dir),
        }
    ]

    device_name, location = _resolve_device_metadata(str(recording_path), device_index)
    assert device_name == "backyard-mic"
    assert location == "Backyard"


@pytest.mark.parametrize(
    "hour,expected_label",
    [
        (1, "Q1"),
        (7, "Q2"),
        (13, "Q3"),
        (19, "Q4"),
    ],
)
def test_quarter_windows_cover_expected_hours(hour, expected_label):
    target_date = datetime(2024, 10, 21, tzinfo=timezone.utc).date()
    quarters = _build_quarter_windows(target_date)
    for quarter in quarters:
        if quarter.label == expected_label:
            start = datetime.fromisoformat(quarter.start)
            end = datetime.fromisoformat(quarter.end)
            probe = datetime(2024, 10, 21, hour, 30, tzinfo=timezone.utc)
            assert start <= probe < end
            break
    else:
        pytest.fail(f"Quarter {expected_label} not found")
