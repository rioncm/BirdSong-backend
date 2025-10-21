from __future__ import annotations

from typing import Protocol

from lib.alerts import AlertEvent
from ..models import SummaryBucket


class NotificationChannel(Protocol):
    name: str

    def send_alert(self, event: AlertEvent) -> None:
        ...

    def send_summary(self, bucket: SummaryBucket) -> None:
        ...
