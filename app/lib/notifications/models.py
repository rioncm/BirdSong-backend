from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from lib.alerts import AlertEvent


@dataclass(slots=True)
class SummaryRecord:
    species_id: Optional[str]
    scientific_name: Optional[str]
    common_name: Optional[str]
    confidence: Optional[float]
    detected_at: datetime
    recording_path: Optional[str]


@dataclass(slots=True)
class SummaryBucket:
    date: str
    records: List[SummaryRecord] = field(default_factory=list)


@dataclass(slots=True)
class NotificationEvent:
    event: AlertEvent
    rendered: Optional[str] = None
