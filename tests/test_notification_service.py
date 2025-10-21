from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.lib.alerts import AlertEvent
from app.lib.notifications.service import NotificationService


def _sample_event() -> AlertEvent:
    now = datetime.utcnow()
    return AlertEvent(
        name="rare_species",
        severity="info",
        detected_at=now,
        species={"scientific_name": "Corvus corax", "common_name": "Common Raven", "id": "corvus-corax"},
        detection={"confidence": 0.92, "recording_path": "/tmp/raven.wav"},
    )


def test_handle_alert_persists_summary(tmp_path):
    storage = tmp_path / "summaries.json"
    service = NotificationService({}, storage)

    service.handle_alert(_sample_event())

    assert storage.exists()
    data = storage.read_text(encoding="utf-8")
    assert "corvus-corax" in data


def test_flush_summaries_sends_to_channels(tmp_path):
    storage = tmp_path / "summaries.json"
    service = NotificationService({}, storage)

    event = _sample_event()
    service.handle_alert(event)

    stub = StubChannel()
    service._channels = [stub]
    service._summary_channels = [stub]

    service.flush_summaries([stub])

    assert stub.received_summaries == 1


class StubChannel:
    name = "stub"
    summary_enabled = True
    real_time_enabled = False
    summary_schedule_time = None

    def __init__(self) -> None:
        self.received_summaries = 0

    def send_alert(self, event: AlertEvent) -> None:
        return

    def send_summary(self, bucket) -> None:
        self.received_summaries += 1
