from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Iterable, Optional

from datetime import time
from typing import Iterable, Optional

from lib.alerts import AlertEvent

from ..models import SummaryBucket
from .base import NotificationChannel


class EmailChannel(NotificationChannel):
    name = "email"

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_address: str,
        to_addresses: Iterable[str],
        use_tls: bool = True,
        real_time: bool = False,
        summary_enabled: bool = True,
        summary_schedule: Optional[str] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_address = from_address
        self._to_addresses = list(to_addresses)
        self._use_tls = use_tls
        self.real_time_enabled = real_time
        self.summary_enabled = summary_enabled
        self.summary_schedule_time = self._parse_schedule(summary_schedule)

    def _send(self, subject: str, body: str) -> None:
        if not self._to_addresses:
            return

        message = EmailMessage()
        message["From"] = self._from_address
        message["To"] = ", ".join(self._to_addresses)
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(self._host, self._port, timeout=10) as server:
            if self._use_tls:
                server.starttls()
            if self._username:
                server.login(self._username, self._password)
            server.send_message(message)

    def send_alert(self, event: AlertEvent) -> None:
        if not self.real_time_enabled:
            return
        subject = f"BirdSong Alert: {event.species.get('common_name') or event.species.get('scientific_name')}"
        body = self._format_alert(event)
        self._send(subject, body)

    def send_summary(self, bucket: SummaryBucket) -> None:
        if not self.summary_enabled:
            return
        subject = f"BirdSong Daily Summary - {bucket.date}"
        lines = [f"Summary for {bucket.date}", ""]
        for record in bucket.records:
            if record.common_name or record.scientific_name:
                confidence = record.confidence
                confidence_text = f"{confidence:.2f}" if confidence is not None else "n/a"
                lines.append(
                    f"- {record.common_name or record.scientific_name} (confidence: {confidence_text})"
                )
        body = "\n".join(lines)
        self._send(subject, body)

    @staticmethod
    def _format_alert(event: AlertEvent) -> str:
        species = event.species
        detection = event.detection
        lines = [
            f"Alert: {event.name}",
            f"Species: {species.get('common_name') or species.get('scientific_name')}",
            f"Confidence: {detection.get('confidence')}",
            f"Recording: {detection.get('recording_path')}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _parse_schedule(value: Optional[str]) -> Optional[time]:
        if not value:
            return None
        try:
            hour, minute = map(int, value.split(":", 1))
            return time(hour=hour, minute=minute)
        except (ValueError, TypeError):
            return None
