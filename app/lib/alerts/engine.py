from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable, List

from .models import AlertContext, AlertEvent
from .registry import build_rules


class AlertEngine:
    def __init__(
        self,
        config: dict,
        publisher: Callable[[AlertEvent], None],
    ) -> None:
        self._publisher = publisher
        self._rules = build_rules(config)
        self._recent_detections: dict[str, datetime] = {}

    def process_detection(self, detection: dict) -> None:
        if not self._rules:
            return

        now = datetime.utcnow()
        context = AlertContext(now=now, recent_detections=self._recent_detections)
        species_id = detection.get("species_id") or detection.get("scientific_name")
        species_id = str(species_id) if species_id else None

        for rule in self._rules:
            events: Iterable[AlertEvent] = rule.evaluate(detection, context)
            for event in events:
                self._publisher(event)

        if species_id:
            self._recent_detections[species_id] = now

    def flush_all(self) -> None:
        # Placeholder for future buffering logic.
        return
