from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session

from app.lib.data.tables import days, idents, metadata, recordings, species
from app.lib.stats import (
    TimeWindow,
    derive_comparison_window,
    fetch_data_comparison,
    fetch_overview_stats,
    parse_window_shorthand,
    resolve_time_window,
)


def test_parse_window_shorthand_supports_common_units():
    assert parse_window_shorthand("30m") == timedelta(minutes=30)
    assert parse_window_shorthand("24h") == timedelta(hours=24)
    assert parse_window_shorthand("7d") == timedelta(days=7)


def test_resolve_time_window_defaults_to_last_24_hours():
    now = datetime(2024, 4, 8, 12, 0, tzinfo=timezone.utc)
    window = resolve_time_window(start=None, end=None, window=None, now=now)
    assert window.end == now
    assert window.start == now - timedelta(hours=24)


def test_resolve_time_window_with_explicit_bounds():
    start = "2024-04-01T00:00:00Z"
    end = "2024-04-02T00:00:00Z"
    window = resolve_time_window(start=start, end=end, window=None)
    assert window.start == datetime(2024, 4, 1, tzinfo=timezone.utc)
    assert window.end == datetime(2024, 4, 2, tzinfo=timezone.utc)


def test_derive_comparison_window_prior_range():
    primary = TimeWindow(
        start=datetime(2024, 4, 7, 10, 0, tzinfo=timezone.utc),
        end=datetime(2024, 4, 7, 12, 0, tzinfo=timezone.utc),
    )
    comparison = derive_comparison_window(primary, "prior_range")
    assert comparison.end == primary.start
    assert comparison.start == primary.start - primary.duration


def _prepare_in_memory_db():
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)

    sample_date = date(2024, 4, 7)
    with engine.begin() as conn:
        conn.execute(insert(species), [
            {"id": "sp-1", "sci_name": "Corvus brachyrhynchos", "common_name": "American Crow"},
            {"id": "sp-2", "sci_name": "Setophaga coronata", "common_name": "Yellow-rumped Warbler"},
        ])
        result = conn.execute(
            insert(days)
            .values(date=sample_date)
        )
        day_id = result.inserted_primary_key[0]
        conn.execute(insert(recordings), [
            {
                "wav_id": "wav-1",
                "path": "/audio/wav-1.wav",
                "source_id": "dev-1",
                "source_name": "Device One",
                "source_display_name": "Device One",
            }
        ])
        conn.execute(insert(idents), [
            {
                "date_id": day_id,
                "species_id": "sp-1",
                "date": sample_date,
                "time": time(10, 15),
                "common_name": "American Crow",
                "sci_name": "Corvus brachyrhynchos",
                "confidence": 0.8,
                "wav_id": "wav-1",
            },
            {
                "date_id": day_id,
                "species_id": "sp-1",
                "date": sample_date,
                "time": time(10, 45),
                "common_name": "American Crow",
                "sci_name": "Corvus brachyrhynchos",
                "confidence": 0.6,
                "wav_id": "wav-1",
            },
            {
                "date_id": day_id,
                "species_id": "sp-2",
                "date": sample_date,
                "time": time(11, 5),
                "common_name": "Yellow-rumped Warbler",
                "sci_name": "Setophaga coronata",
                "confidence": 0.9,
                "wav_id": "wav-1",
            },
        ])

    return engine


def test_fetch_overview_stats_basic_counts():
    engine = _prepare_in_memory_db()
    session = Session(engine)
    try:
        window = TimeWindow(
            start=datetime(2024, 4, 7, tzinfo=timezone.utc),
            end=datetime(2024, 4, 8, tzinfo=timezone.utc),
        )
        result = fetch_overview_stats(
            session,
            window,
            device_index=[{"id": "dev-1", "display_name": "Device One"}],
            top_species_limit=5,
            top_hours_limit=5,
            top_streams_limit=5,
        )
    finally:
        session.close()

    assert result["detections_total"] == 3
    assert result["unique_species"] == 2
    assert result["active_devices"] == 1
    assert result["top_species"][0]["species_id"] == "sp-1"
    assert result["top_species"][0]["detections"] == 2
    assert pytest.approx(result["avg_confidence"], rel=1e-3) == (0.8 + 0.6 + 0.9) / 3

    top_hours = result["top_hours"]
    assert top_hours[0]["bucket_start"] == "2024-04-07T10:00:00Z"
    assert top_hours[0]["detections"] == 2

    top_streams = result["top_streams"]
    assert top_streams[0]["device_id"] == "dev-1"
    assert top_streams[0]["display_name"] == "Device One"
    assert top_streams[0]["detections"] == 3


def test_fetch_data_comparison_prior_range_handles_zero_baseline():
    engine = _prepare_in_memory_db()
    session = Session(engine)
    primary_window = TimeWindow(
        start=datetime(2024, 4, 7, 10, 0, tzinfo=timezone.utc),
        end=datetime(2024, 4, 7, 12, 0, tzinfo=timezone.utc),
    )
    try:
        comparison = fetch_data_comparison(
            session,
            metric="detections_total",
            primary_window=primary_window,
            selector="prior_range",
        )
    finally:
        session.close()

    assert comparison["primary_value"] == 3.0
    assert comparison["comparison_value"] == 0.0
    assert comparison["percent_change"] is None
    comparison_window: TimeWindow = comparison["comparison_window"]
    assert comparison_window.end == primary_window.start
    assert comparison_window.start == primary_window.start - primary_window.duration
