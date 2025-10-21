from __future__ import annotations

from datetime import time
from typing import Any, Iterable, Optional

import httpx

from lib.alerts import AlertEvent

from ..models import SummaryBucket
from .base import NotificationChannel


class TelegramChannel(NotificationChannel):
    name = "telegram"

    def __init__(
        self,
        bot_token: str,
        chat_ids: Iterable[Any],
        real_time: bool = True,
        summary_enabled: bool = True,
        summary_schedule: Optional[str] = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_ids = self._normalize_chat_ids(chat_ids)
        self.real_time_enabled = real_time
        self.summary_enabled = summary_enabled
        self._client = httpx.Client(base_url=f"https://api.telegram.org/bot{bot_token}")
        self.summary_schedule_time = self._parse_schedule(summary_schedule)

    def close(self) -> None:
        self._client.close()

    def _send(self, text: str) -> None:
        if not self._chat_ids:
            return
        for chat_id in self._chat_ids:
            response = self._client.post("/sendMessage", data={"chat_id": chat_id, "text": text})
            response.raise_for_status()

    def send_alert(self, event: AlertEvent) -> None:
        if not self.real_time_enabled:
            return
        text = self._format_alert(event)
        self._send(text)

    def send_summary(self, bucket: SummaryBucket) -> None:
        if not self.summary_enabled:
            return
        lines = [f"Summary for {bucket.date}"]
        for record in bucket.records:
            lines.append(f"• {record.common_name or record.scientific_name}")
        self._send("\n".join(lines))

    @staticmethod
    def _format_alert(event: AlertEvent) -> str:
        species = event.species
        detection = event.detection
        return (
            f"Alert: {event.name}\n"
            f"Species: {species.get('common_name') or species.get('scientific_name')}\n"
            f"Confidence: {detection.get('confidence')}"
        )

    @staticmethod
    def _parse_schedule(value: Optional[str]) -> Optional[time]:
        if not value:
            return None
        try:
            hour, minute = map(int, value.split(":", 1))
            return time(hour=hour, minute=minute)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _normalize_chat_ids(chat_ids: Iterable[Any]) -> list[str]:
        normalized: list[str] = []
        for entry in chat_ids:
            if isinstance(entry, dict):
                chat_id = entry.get("id") or entry.get("chat_id")
                if chat_id is None:
                    continue
                normalized.append(str(chat_id))
            else:
                normalized.append(str(entry))
        return normalized
