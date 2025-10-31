from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, ConfigDict


class DetectionSummary(BaseModel):
    total_detections: int = Field(..., ge=0)
    unique_species: int = Field(..., ge=0)
    first_detection: Optional[str] = None
    last_detection: Optional[str] = None
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1)


class SpeciesPreview(BaseModel):
    id: str
    common_name: Optional[str] = None
    scientific_name: Optional[str] = None
    genus: Optional[str] = None
    family: Optional[str] = None
    image_url: Optional[str] = None
    image_thumbnail_url: Optional[str] = None
    image_license: Optional[str] = None
    image_attribution: Optional[str] = None
    image_source_url: Optional[str] = None
    summary: Optional[str] = None
    info_url: Optional[str] = None


class RecordingPreview(BaseModel):
    wav_id: Optional[str] = None
    path: Optional[str] = None
    duration_seconds: Optional[float] = None
    url: Optional[str] = None


class DetectionItem(BaseModel):
    id: int
    recorded_at: Optional[str] = None
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    device_display_name: Optional[str] = None
    confidence: Optional[float] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    species: SpeciesPreview
    recording: RecordingPreview
    detection_count: Optional[int] = Field(
        default=None, ge=1, description="Aggregated count of detections represented by this item"
    )


class TimelineBucket(BaseModel):
    bucket_start: str
    bucket_end: str
    total_detections: int
    unique_species: int
    detections: List[DetectionItem]


class DetectionTimelineResponse(BaseModel):
    bucket_minutes: int
    has_more: bool
    next_cursor: Optional[str] = None
    previous_cursor: Optional[str] = None
    buckets: List[TimelineBucket]


class QuarterWindow(BaseModel):
    label: str
    start: str
    end: str


class QuarterPresetsResponse(BaseModel):
    date: str
    current_label: Optional[str] = None
    quarters: List[QuarterWindow]


class DetectionFeedResponse(BaseModel):
    date: Optional[str] = None
    summary: DetectionSummary
    detections: List[DetectionItem]


class CitationEntry(BaseModel):
    source_name: str
    data_type: str
    content: object
    last_updated: Optional[str] = None


class TaxonomyDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kingdom: Optional[str] = None
    phylum: Optional[str] = None
    class_: Optional[str] = Field(None, alias="class")
    order: Optional[str] = None
    family: Optional[str] = None
    genus: Optional[str] = None


class SpeciesImage(BaseModel):
    url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    license: Optional[str] = None
    attribution: Optional[str] = None
    source_url: Optional[str] = None


class SpeciesDetections(BaseModel):
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    total_count: int = 0


class SpeciesDetailResponse(BaseModel):
    id: str
    common_name: Optional[str] = None
    scientific_name: Optional[str] = None
    taxonomy: TaxonomyDetail
    summary: Optional[str] = None
    image: SpeciesImage
    detections: SpeciesDetections
    citations: List[CitationEntry]


class DayForecast(BaseModel):
    high: Optional[float] = None
    low: Optional[float] = None
    rain_probability: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Probability (0-1 scale)"
    )
    issued_at: Optional[str] = None
    source: Optional[str] = None


class DayActuals(BaseModel):
    high: Optional[float] = None
    low: Optional[float] = None
    rain_total: Optional[float] = None
    updated_at: Optional[str] = None
    source: Optional[str] = None


class DayOverviewResponse(BaseModel):
    date: str
    season: Optional[str] = None
    dawn: Optional[str] = None
    sunrise: Optional[str] = None
    solar_noon: Optional[str] = None
    sunset: Optional[str] = None
    dusk: Optional[str] = None
    forecast: DayForecast
    actual: DayActuals
