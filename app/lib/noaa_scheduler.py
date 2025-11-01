from __future__ import annotations

import asyncio
import logging
from typing import Optional

from lib.clients.noaa import NoaaClient
from lib.config import AppConfig

from .noaa import resolve_noaa_user_agent, update_daily_weather_from_config


logger = logging.getLogger("birdsong.scheduler.noaa")


class NoaaUpdateScheduler:
    def __init__(
        self,
        app_config: AppConfig,
        resources: dict,
        *,
        interval_hours: int = 6,
        include_actuals: bool = True,
    ) -> None:
        self._app_config = app_config
        self._resources = resources
        self._interval_hours = max(1, interval_hours)
        self._include_actuals = include_actuals
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "NOAA scheduler started (interval=%sh, include_actuals=%s)",
            self._interval_hours,
            self._include_actuals,
        )

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
        logger.info("NOAA scheduler stopped")

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._execute_once()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._interval_hours * 3600,
                    )
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise

    async def _execute_once(self) -> None:
        try:
            await asyncio.to_thread(self._run_sync_update)
        except Exception:  # noqa: BLE001
            logger.exception("NOAA scheduled update failed")

    def _run_sync_update(self) -> None:
        user_agent = resolve_noaa_user_agent(self._resources)
        logger.info("Running scheduled NOAA update")
        with NoaaClient(user_agent=user_agent) as client:
            update_daily_weather_from_config(
                self._app_config,
                client=client,
                include_actuals=self._include_actuals,
                user_agent=user_agent,
            )
        logger.info("NOAA update complete")
