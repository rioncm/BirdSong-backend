from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass(slots=True)
class AlertContext:
    """Contextual data available to rules when evaluating detections."""

    now: datetime
    recent_detections: Dict[str, datetime] = field(default_factory=dict)


@dataclass(slots=True)
class AlertEvent:
    """Structured alert payload broadcast to notification subscribers."""

    name: str
    severity: str
    detected_at: datetime
    species: Dict[str, Any]
    detection: Dict[str, Any]
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "severity": self.severity,
            "detected_at": self.detected_at.isoformat(),
            "species": self.species,
            "detection": self.detection,
            "context": self.context,
        }
