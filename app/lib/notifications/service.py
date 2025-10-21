from __future__ import annotations

import json
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from lib.alerts import AlertEvent

from .channels.base import NotificationChannel
from .channels.email import EmailChannel
from .channels.telegram import TelegramChannel
from .models import SummaryBucket, SummaryRecord


class NotificationService:
    def __init__(
        self,
        config: Dict[str, any],
        storage_path: Path,
    ) -> None:
        self._config = dict(config)
        self._storage_path = storage_path
        self._channels: List[NotificationChannel] = self._build_channels(config)
        self._summary_channels: List[NotificationChannel] = [
            channel for channel in self._channels if getattr(channel, "summary_enabled", False)
        ]
        self._flush_summaries = bool(config.get("flush_summaries", True))
        self._retain_period = self._parse_period(config.get("retain_period", "7 days"))
        self._summary_schedule_map: Dict[time, List[NotificationChannel]] = self._build_schedule_map()
        self._last_summary_sent: Dict[str, str] = {}

    def close(self) -> None:
        for channel in self._channels:
            close_fn = getattr(channel, "close", None)
            if callable(close_fn):
                close_fn()

    def _build_channels(self, config: Dict[str, any]) -> List[NotificationChannel]:
        channels: List[NotificationChannel] = []
        email_conf = config.get("email")
        if isinstance(email_conf, dict) and email_conf.get("enabled"):
            channels.append(
                EmailChannel(
                    host=email_conf["smtp_host"],
                    port=int(email_conf.get("smtp_port", 587)),
                    username=email_conf.get("username", ""),
                    password=email_conf.get("password", ""),
                    from_address=email_conf["from_address"],
                    to_addresses=email_conf.get("to_addresses", []),
                    use_tls=bool(email_conf.get("use_tls", True)),
                    real_time=bool(email_conf.get("real_time", False)),
                    summary_enabled=bool(email_conf.get("summary", True)),
                    summary_schedule=email_conf.get("summary_schedule"),
                )
            )
        telegram_conf = config.get("telegram")
        if isinstance(telegram_conf, dict) and telegram_conf.get("enabled"):
            channels.append(
                TelegramChannel(
                    bot_token=telegram_conf["bot_token"],
                    chat_ids=telegram_conf.get("chats", []),
                    real_time=bool(telegram_conf.get("real_time", True)),
                    summary_enabled=bool(telegram_conf.get("summary", True)),
                    summary_schedule=telegram_conf.get("summary_schedule"),
                )
            )
        return channels

    def handle_alert(self, event: AlertEvent) -> None:
        for channel in self._channels:
            channel.send_alert(event)
        self._store_summary_record(event)

    def flush_summaries(self, channels: Optional[List[NotificationChannel]] = None) -> None:
        if not self._flush_summaries:
            return
        buckets = self._load_buckets()
        if not buckets:
            return
        targets = channels or self._summary_channels
        if not targets:
            return
        for channel in targets:
            if not getattr(channel, "summary_enabled", False):
                continue
            channel_key = self._channel_key(channel)
            last_sent = self._last_summary_sent.get(channel_key)
            for date_key in sorted(buckets.keys()):
                if last_sent is not None and date_key <= last_sent:
                    continue
                channel.send_summary(buckets[date_key])
                self._last_summary_sent[channel_key] = date_key
        self._purge_old_buckets(buckets)
        self._cleanup_sent_buckets(buckets)
        self._persist_buckets(buckets)

    def _store_summary_record(self, event: AlertEvent) -> None:
        buckets = self._load_buckets()
        detected_at = self._ensure_utc(event.detected_at)
        date_key = detected_at.date().isoformat()
        bucket = buckets.setdefault(date_key, SummaryBucket(date=date_key))
        detection = event.detection
        bucket.records.append(
            SummaryRecord(
                species_id=event.species.get("id"),
                scientific_name=event.species.get("scientific_name"),
                common_name=event.species.get("common_name"),
                confidence=detection.get("confidence"),
                detected_at=detected_at,
                recording_path=detection.get("recording_path"),
            )
        )
        self._purge_old_buckets(buckets)
        self._persist_buckets(buckets)

    def _load_buckets(self) -> Dict[str, SummaryBucket]:
        if not self._storage_path.exists():
            return {}
        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        buckets: Dict[str, SummaryBucket] = {}
        for key, payload in data.items():
            records = [
                SummaryRecord(
                    species_id=record.get("species_id"),
                    scientific_name=record.get("scientific_name"),
                    common_name=record.get("common_name"),
                    confidence=record.get("confidence"),
                    detected_at=self._ensure_utc(datetime.fromisoformat(record["detected_at"])),
                    recording_path=record.get("recording_path"),
                )
                for record in payload.get("records", [])
            ]
            buckets[key] = SummaryBucket(date=payload.get("date", key), records=records)
        return buckets

    def _persist_buckets(self, buckets: Dict[str, SummaryBucket]) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {
            key: {
                "date": bucket.date,
                "records": [
                    {
                        "species_id": record.species_id,
                        "scientific_name": record.scientific_name,
                        "common_name": record.common_name,
                        "confidence": record.confidence,
                        "detected_at": record.detected_at.isoformat(),
                        "recording_path": record.recording_path,
                    }
                    for record in bucket.records
                ],
            }
            for key, bucket in buckets.items()
        }
        self._storage_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")

    def _purge_old_buckets(self, buckets: Dict[str, SummaryBucket]) -> None:
        cutoff = datetime.now(timezone.utc) - self._retain_period
        to_remove = []
        for key, bucket in buckets.items():
            latest = max((self._ensure_utc(record.detected_at) for record in bucket.records), default=None)
            if latest and latest < cutoff:
                to_remove.append(key)
        for key in to_remove:
            buckets.pop(key, None)

    def _cleanup_sent_buckets(self, buckets: Dict[str, SummaryBucket]) -> None:
        if not self._summary_channels:
            buckets.clear()
            return
        completed_dates = None
        for channel in self._summary_channels:
            key = self._channel_key(channel)
            last_sent = self._last_summary_sent.get(key)
            if last_sent is None:
                completed_dates = None
                break
            if completed_dates is None or last_sent < completed_dates:
                completed_dates = last_sent
        if completed_dates is None:
            return
        for key in list(buckets.keys()):
            if key <= completed_dates:
                buckets.pop(key, None)

    @staticmethod
    def _parse_period(raw: str) -> timedelta:
        value, unit = str(raw).strip().split()
        amount = int(value)
        unit = unit.lower()
        if unit.startswith("day"):
            return timedelta(days=amount)
        if unit.startswith("week"):
            return timedelta(weeks=amount)
        if unit.startswith("month"):
            return timedelta(days=30 * amount)
        if unit.startswith("year"):
            return timedelta(days=365 * amount)
        raise ValueError(f"Unsupported retention period unit: {unit}")

    def _build_schedule_map(self) -> Dict[time, List[NotificationChannel]]:
        schedule: Dict[time, List[NotificationChannel]] = {}
        for channel in self._channels:
            schedule_time = getattr(channel, "summary_schedule_time", None)
            if schedule_time:
                schedule.setdefault(schedule_time, []).append(channel)
        return schedule

    def get_summary_schedule(self) -> Dict[time, List[NotificationChannel]]:
        return self._summary_schedule_map

    def _channel_key(self, channel: NotificationChannel) -> str:
        return getattr(channel, "name", channel.__class__.__name__)

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
