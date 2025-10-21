from __future__ import annotations

from typing import Iterable

from ..models import AlertContext, AlertEvent
from .base import AlertRule


class FirstDetectionRule(AlertRule):
    name = "first_detection"

    def evaluate(self, detection: dict, context: AlertContext) -> Iterable[AlertEvent]:
        species_id = detection.get("species_id") or detection.get("scientific_name")
        if not species_id:
            return []
        species_id = str(species_id)

        if species_id in context.recent_detections:
            return []

        event = AlertEvent(
            name=self.name,
            severity="info",
            detected_at=context.now,
            species={
                "scientific_name": detection.get("scientific_name"),
                "common_name": detection.get("common_name"),
                "id": species_id,
            },
            detection={
                "confidence": detection.get("confidence"),
                "recording_path": detection.get("recording_path"),
                "start_time": detection.get("start_time"),
                "end_time": detection.get("end_time"),
            },
            context={"reason": "first_detection_overall"},
        )
        return [event]
