from __future__ import annotations

import threading
import time
import logging
from typing import Optional

from .service import SettingsService


logger = logging.getLogger("birdsong.settings.refresher")


class SettingsCacheRefresher:
    def __init__(self, service: SettingsService, interval_seconds: float = 60.0) -> None:
        self._service = service
        self._interval = max(5.0, interval_seconds)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="settings-cache-refresher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._service.clear_cache()
            except Exception:  # noqa: BLE001
                logger.debug("Settings cache refresher failed", exc_info=True)
            self._stop_event.wait(self._interval)
