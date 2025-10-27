from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from starlette.routing import NoMatchFound

from app.api import (
    _build_quarter_windows,
    _floor_to_bucket,
    _resolve_device_metadata,
    _group_detections_into_buckets,
)
from app.lib.schemas import DetectionItem


class _DummyRequest:
    def url_for(self, _name: str, **_kwargs):  # noqa: D401 - simple stub
        raise NoMatchFound()


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
            "display_name": "Backyard Microphone",
            "location": "Backyard",
            "path": str(device_dir),
        }
    ]

    metadata = _resolve_device_metadata(str(recording_path), device_index)
    assert metadata is not None
    assert metadata["id"] == "backyard-mic"
    assert metadata["display_name"] == "Backyard Microphone"
    assert metadata["location"] == "Backyard"


def test_grouping_collapses_species_entries(tmp_path: Path):
    base_dir = tmp_path / "streams" / "whobox"
    base_dir.mkdir(parents=True)
    sample_path = base_dir / "sample.wav"
    sample_path.touch()

    rows = [
        {
            "id": 1,
            "date": datetime(2024, 10, 21, tzinfo=timezone.utc).date(),
            "time": datetime(2024, 10, 21, 12, 0, tzinfo=timezone.utc).time(),
            "ident_common_name": "California Scrub-Jay",
            "ident_scientific_name": "Aphelocoma californica",
            "confidence": 0.82,
            "start_time": 0.5,
            "end_time": 3.5,
            "wav_id": "wav-1",
            "species_id": "apca",
            "species_common_name": "California Scrub-Jay",
            "species_scientific_name": "Aphelocoma californica",
            "genus": "Aphelocoma",
            "family": "Corvidae",
            "image_url": None,
            "info_url": None,
            "summary": None,
            "recording_path": str(sample_path),
            "recording_source_id": "whobox",
            "recording_source_name": "whobox",
            "recording_source_display_name": "Whobox Camera",
            "recording_source_location": "Backyard",
        },
        {
            "id": 2,
            "date": datetime(2024, 10, 21, tzinfo=timezone.utc).date(),
            "time": datetime(2024, 10, 21, 12, 2, tzinfo=timezone.utc).time(),
            "ident_common_name": "California Scrub-Jay",
            "ident_scientific_name": "Aphelocoma californica",
            "confidence": 0.67,
            "start_time": 5.0,
            "end_time": 7.0,
            "wav_id": "wav-2",
            "species_id": "apca",
            "species_common_name": "California Scrub-Jay",
            "species_scientific_name": "Aphelocoma californica",
            "genus": "Aphelocoma",
            "family": "Corvidae",
            "image_url": None,
            "info_url": None,
            "summary": None,
            "recording_path": str(sample_path),
            "recording_source_id": "whobox",
            "recording_source_name": "whobox",
            "recording_source_display_name": "Whobox Camera",
            "recording_source_location": "Backyard",
        },
    ]

    device_index = [
        {
            "type": "stream",
            "id": "whobox",
            "name": "whobox",
            "display_name": "Whobox Camera",
            "location": "Backyard",
            "path": str(base_dir),
        }
    ]

    buckets, _ = _group_detections_into_buckets(
        rows,
        attribution_map={},
        device_index=device_index,
        request=_DummyRequest(),
        bucket_minutes=5,
    )

    assert len(buckets) == 1
    bucket = buckets[0]
    assert bucket["total_detections"] == 2
    assert bucket["unique_species"] == 1
    assert len(bucket["detections"]) == 1
    aggregated: DetectionItem = bucket["detections"][0]
    assert aggregated.detection_count == 2
    assert aggregated.device_display_name == "Whobox Camera"


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
