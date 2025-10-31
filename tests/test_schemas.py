from __future__ import annotations

from app.schemas import (
    CitationEntry,
    DayActuals,
    DayForecast,
    DayOverviewResponse,
    DetectionFeedResponse,
    DetectionItem,
    DetectionSummary,
    RecordingPreview,
    SpeciesDetections,
    SpeciesDetailResponse,
    SpeciesImage,
    SpeciesPreview,
    TaxonomyDetail,
)


def test_detection_feed_schema_roundtrip() -> None:
    response = DetectionFeedResponse(
        date="2025-10-20",
        summary=DetectionSummary(
            total_detections=3,
            unique_species=2,
            first_detection="05:00:00",
            last_detection="18:30:00",
            page=1,
            page_size=25,
        ),
        detections=[
            DetectionItem(
                id=1,
                recorded_at="2025-10-20T05:00:00Z",
                confidence=0.9,
                start_time=0.5,
                end_time=2.5,
                species=SpeciesPreview(
                    id="apca",
                    common_name="California Scrub-Jay",
                    scientific_name="Aphelocoma californica",
                    image_thumbnail_url="https://example.org/thumb.jpg",
                    image_license="CC BY-SA 4.0",
                    image_attribution="© Example",
                ),
                recording=RecordingPreview(
                    wav_id="123",
                    path="/tmp/audio.wav",
                    duration_seconds=32.5,
                ),
            )
        ],
    )

    payload = response.model_dump()
    assert payload["summary"]["total_detections"] == 3
    assert payload["detections"][0]["species"]["id"] == "apca"
    assert payload["detections"][0]["recording"]["duration_seconds"] == 32.5
    assert payload["detections"][0]["species"]["image_thumbnail_url"] == "https://example.org/thumb.jpg"


def test_species_detail_schema_supports_alias() -> None:
    response = SpeciesDetailResponse(
        id="apca",
        common_name="California Scrub-Jay",
        scientific_name="Aphelocoma californica",
        taxonomy=TaxonomyDetail(
            kingdom="Animalia",
            phylum="Chordata",
            class_="Aves",
            order="Passeriformes",
            family="Corvidae",
            genus="Aphelocoma",
        ),
        summary="A bright blue bird native to the western US.",
        image=SpeciesImage(
            url="https://example.org/bird.jpg",
            license="CC BY-SA 4.0",
        ),
        detections=SpeciesDetections(
            first_seen="2025-01-01",
            last_seen="2025-10-20",
            total_count=5,
        ),
        citations=[
            CitationEntry(
                source_name="Wikimedia Commons",
                data_type="image",
                content={"title": "Bird"},
                last_updated="2025-10-20T10:00:00Z",
            )
        ],
    )

    dumped = response.model_dump(by_alias=True)
    assert dumped["taxonomy"]["class"] == "Aves"
    assert dumped["image"]["license"] == "CC BY-SA 4.0"


def test_day_overview_schema() -> None:
    response = DayOverviewResponse(
        date="2025-10-20",
        season="autumn",
        dawn="06:10:00",
        sunrise="06:38:00",
        solar_noon="12:52:00",
        sunset="19:05:00",
        dusk="19:32:00",
        forecast=DayForecast(
            high=78.0,
            low=55.0,
            rain_probability=0.1,
            issued_at="2025-10-20T06:00:00Z",
            source="NOAA NWS",
        ),
        actual=DayActuals(
            high=76.4,
            low=54.9,
            rain_total=0.0,
            updated_at="2025-10-21T02:00:00Z",
            source="NOAA NWS",
        ),
    )

    payload = response.model_dump()
    assert payload["forecast"]["rain_probability"] == 0.1
    assert payload["actual"]["high"] == 76.4
