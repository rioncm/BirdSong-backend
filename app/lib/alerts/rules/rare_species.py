from __future__ import annotations

from typing import Iterable, Sequence

from ..models import AlertContext, AlertEvent
from .base import AlertRule


class RareSpeciesRule(AlertRule):
    name = "rare_species"

    def __init__(self, scientific_names: Sequence[str]) -> None:
        self._scientific_names = {name.strip().lower() for name in scientific_names}

    def evaluate(self, detection: dict, context: AlertContext) -> Iterable[AlertEvent]:
        sci_name = str(detection.get("scientific_name") or "").strip().lower()
        if not sci_name or sci_name not in self._scientific_names:
            return []

        event = AlertEvent(
            name=self.name,
            severity="info",
            detected_at=context.now,
            species={
                "scientific_name": detection.get("scientific_name"),
                "common_name": detection.get("common_name"),
                "id": detection.get("species_id"),
            },
            detection={
                "confidence": detection.get("confidence"),
                "recording_path": detection.get("recording_path"),
                "start_time": detection.get("start_time"),
                "end_time": detection.get("end_time"),
            },
            context={"reason": "listed_rare_species"},
        )
        return [event]
