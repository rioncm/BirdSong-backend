from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from typing import Dict, Iterable, List

from .service import NotificationService


class SummaryScheduler:
    def __init__(self, service: NotificationService, schedule: Dict[time, List]) -> None:
        self._service = service
        self._schedule = schedule
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if not self._schedule or self._task:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                now = datetime.utcnow()
                next_run = self._compute_next_run(now)
                if next_run is None:
                    await asyncio.sleep(3600)
                    continue
                wait_seconds = max((next_run - now).total_seconds(), 0.0)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
                    break
                except asyncio.TimeoutError:
                    pass
                due_channels = self._channels_due(datetime.utcnow())
                if due_channels:
                    self._service.flush_summaries(due_channels)
        except asyncio.CancelledError:
            raise

    def _compute_next_run(self, now: datetime) -> datetime | None:
        occurrences = [self._next_occurrence(now, schedule_time) for schedule_time in self._schedule]
        return min(occurrences) if occurrences else None

    @staticmethod
    def _next_occurrence(now: datetime, schedule_time: time) -> datetime:
        candidate = datetime.combine(now.date(), schedule_time)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _channels_due(self, now: datetime) -> List:
        due: List = []
        for schedule_time, channels in self._schedule.items():
            window_start = datetime.combine(now.date(), schedule_time)
            window_end = window_start + timedelta(minutes=1)
            if window_start <= now < window_end:
                due.extend(channels)
        return due
