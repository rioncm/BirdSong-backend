from __future__ import annotations

from typing import Iterable, Protocol

from ..models import AlertContext, AlertEvent


class AlertRule(Protocol):
    name: str

    def evaluate(self, detection: dict, context: AlertContext) -> Iterable[AlertEvent]:
        ...
