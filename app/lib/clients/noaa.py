from __future__ import annotations

import os
import logging
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from lib.utils.retry import with_retry

__all__ = [
    "NoaaClient",
    "NoaaClientError",
    "build_noaa_client",
]


DEFAULT_NOAA_BASE_URL = "https://api.weather.gov"


class NoaaClientError(RuntimeError):
    """Raised when a NOAA request fails."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


logger = logging.getLogger("birdsong.clients.noaa")


def _default_headers(user_agent: Optional[str], token: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/ld+json",
        "User-Agent": user_agent
        or os.getenv("NOAA_USER_AGENT")
        or "BirdSong/0.1 (+https://github.com/rion/BirdSong)",
    }
    effective_token = token or os.getenv("NOAA_API_TOKEN")
    if effective_token:
        headers["token"] = effective_token
    return headers


class NoaaClient:
    """
    Thin wrapper over the NOAA/NWS API with simple caching for point metadata.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_NOAA_BASE_URL,
        timeout: float = 10.0,
        user_agent: Optional[str] = None,
        token: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
        attempts: int = 3,
        base_delay: float = 0.5,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers=_default_headers(user_agent, token),
            transport=transport,
        )
        self._point_cache: Dict[Tuple[float, float], Dict[str, Any]] = {}
        self._station_cache: Dict[str, Dict[str, Any]] = {}
        self._attempts = max(1, attempts)
        self._base_delay = base_delay

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "NoaaClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def get_point(self, latitude: float, longitude: float) -> Dict[str, Any]:
        key = (round(latitude, 4), round(longitude, 4))
        cached = self._point_cache.get(key)
        if cached is not None:
            return cached

        path = f"/points/{latitude},{longitude}"

        def _call() -> Dict[str, Any]:
            start = time.perf_counter()
            response = self._client.get(path)
            duration = time.perf_counter() - start
            status = response.status_code
            if status == 404:
                raise NoaaClientError(
                    f"NOAA point lookup failed for lat={latitude}, lon={longitude}",
                    retryable=False,
                )
            if status >= 500:
                raise NoaaClientError(
                    f"NOAA point lookup received {status} for lat={latitude}, lon={longitude}",
                    retryable=True,
                )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:  # noqa: BLE001
                raise NoaaClientError(
                    f"NOAA point lookup error {exc.response.status_code} for lat={latitude}, lon={longitude}",
                    retryable=500 <= exc.response.status_code < 600,
                ) from exc

            payload = response.json()
            logger.info(
                "NOAA request success",
                extra={
                    "event": "noaa_request",
                    "operation": "points",
                    "status": status,
                    "duration": duration,
                },
            )
            self._point_cache[key] = payload
            return payload

        return with_retry(
            _call,
            attempts=self._attempts,
            base_delay=self._base_delay,
            logger=logger,
            description=f"NOAA points {latitude},{longitude}",
            exceptions=(NoaaClientError,),
        )

    def get_forecast(self, grid_id: str, grid_x: int, grid_y: int) -> Dict[str, Any]:
        path = f"/gridpoints/{grid_id}/{grid_x},{grid_y}/forecast"

        def _call() -> Dict[str, Any]:
            start = time.perf_counter()
            response = self._client.get(path)
            duration = time.perf_counter() - start
            status = response.status_code
            if status == 404:
                raise NoaaClientError(
                    f"NOAA forecast not found for grid {grid_id} {grid_x},{grid_y}",
                    retryable=False,
                )
            if status >= 500:
                raise NoaaClientError(
                    f"NOAA forecast error {status} for grid {grid_id} {grid_x},{grid_y}",
                    retryable=True,
                )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:  # noqa: BLE001
                raise NoaaClientError(
                    f"NOAA forecast error {exc.response.status_code} for grid {grid_id} {grid_x},{grid_y}",
                    retryable=500 <= exc.response.status_code < 600,
                ) from exc
            logger.info(
                "NOAA request success",
                extra={
                    "event": "noaa_request",
                    "operation": "forecast",
                    "status": status,
                    "duration": duration,
                },
            )
            return response.json()

        return with_retry(
            _call,
            attempts=self._attempts,
            base_delay=self._base_delay,
            logger=logger,
            description=f"NOAA forecast {grid_id} {grid_x},{grid_y}",
            exceptions=(NoaaClientError,),
        )

    def get_observation_stations(self, stations_url: str) -> Dict[str, Any]:
        if not stations_url:
            raise NoaaClientError("Observation stations URL missing from point metadata")
        cached = self._station_cache.get(stations_url)
        if cached is not None:
            return cached

        def _call() -> Dict[str, Any]:
            start = time.perf_counter()
            response = self._client.get(stations_url)
            duration = time.perf_counter() - start
            status = response.status_code
            if status >= 500:
                raise NoaaClientError(
                    f"NOAA observation stations error {status}",
                    retryable=True,
                )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:  # noqa: BLE001
                raise NoaaClientError(
                    f"NOAA observation stations request failed ({exc.response.status_code})",
                    retryable=500 <= exc.response.status_code < 600,
                ) from exc
            payload = response.json()
            logger.info(
                "NOAA request success",
                extra={
                    "event": "noaa_request",
                    "operation": "stations",
                    "status": status,
                    "duration": duration,
                },
            )
            self._station_cache[stations_url] = payload
            return payload

        return with_retry(
            _call,
            attempts=self._attempts,
            base_delay=self._base_delay,
            logger=logger,
            description="NOAA observation stations",
            exceptions=(NoaaClientError,),
        )

    def get_observations(
        self,
        station_id: str,
        *,
        start: str,
        end: str,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        if not station_id:
            raise NoaaClientError("station_id is required for observations lookup")
        path = f"/stations/{station_id}/observations"
        params = {"start": start, "end": end, "limit": str(limit)}

        def _call() -> Dict[str, Any]:
            start_ts = time.perf_counter()
            response = self._client.get(path, params=params)
            duration = time.perf_counter() - start_ts
            status = response.status_code
            if status >= 500:
                raise NoaaClientError(
                    f"NOAA observations error {status} for station {station_id}",
                    retryable=True,
                )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:  # noqa: BLE001
                raise NoaaClientError(
                    f"NOAA observations error {exc.response.status_code} for station {station_id}",
                    retryable=500 <= exc.response.status_code < 600,
                ) from exc
            logger.info(
                "NOAA request success",
                extra={
                    "event": "noaa_request",
                    "operation": "observations",
                    "status": status,
                    "duration": duration,
                },
            )
            return response.json()

        return with_retry(
            _call,
            attempts=self._attempts,
            base_delay=self._base_delay,
            logger=logger,
            description=f"NOAA observations {station_id}",
            exceptions=(NoaaClientError,),
        )


def build_noaa_client(**kwargs: Any) -> NoaaClient:
    """
    Convenience factory so callers can defer import paths.
    """

    return NoaaClient(**kwargs)
