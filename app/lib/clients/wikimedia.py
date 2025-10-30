from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote

import httpx

from lib.utils.retry import with_retry


__all__ = [
    "WikimediaClient",
    "WikimediaClientError",
    "WikimediaSummary",
    "WikimediaMedia",
    "build_wikimedia_stub",
]


class WikimediaClientError(RuntimeError):
    """Raised when Wikimedia requests fail."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class WikimediaSummary:
    title: str
    extract: str
    page_url: Optional[str]
    thumbnail_url: Optional[str]
    raw: Dict[str, Any]


@dataclass(frozen=True)
class WikimediaMedia:
    title: str
    image_url: str
    thumbnail_url: Optional[str]
    license_code: Optional[str]
    attribution: Optional[str]
    page_url: Optional[str]
    raw: Dict[str, Any]


def _default_headers(user_agent: str) -> Dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "application/json",
    }


def _normalize_title(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("title must be a non-empty string")
    return normalized


logger = logging.getLogger("birdsong.clients.wikimedia")


class WikimediaClient:
    """
    Minimal REST client for Wikimedia summary and media endpoints.
    """

    def __init__(
        self,
        *,
        summary_base_url: str = "https://en.wikipedia.org/api/rest_v1",
        media_base_url: str = "https://commons.wikimedia.org/api/rest_v1",
        timeout: float = 5.0,
        user_agent: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
        summary_fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
        media_fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
        attempts: int = 3,
        base_delay: float = 0.5,
    ) -> None:
        resolved_user_agent = (
            user_agent
            or os.getenv("WIKIMEDIA_USER_AGENT")
            or "BirdSong/0.1 (+https://github.com/rion/BirdSong)"
        )
        self._summary_client = httpx.Client(
            base_url=summary_base_url,
            timeout=timeout,
            headers=_default_headers(resolved_user_agent),
            transport=transport,
        )
        self._media_client = httpx.Client(
            base_url=media_base_url,
            timeout=timeout,
            headers=_default_headers(resolved_user_agent),
            transport=transport,
        )
        self._summary_fetcher = summary_fetcher or self._fetch_summary
        self._media_fetcher = media_fetcher or self._fetch_media
        self._attempts = max(1, attempts)
        self._base_delay = base_delay

    def close(self) -> None:
        self._summary_client.close()
        self._media_client.close()

    def summary(self, title: str) -> Optional[WikimediaSummary]:
        normalized = _normalize_title(title)
        def _call() -> Optional[WikimediaSummary]:
            try:
                payload = self._summary_fetcher(normalized)
            except WikimediaClientError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise WikimediaClientError(
                    f"Summary lookup failed for '{normalized}': {exc}",
                    retryable=True,
                ) from exc

            if not payload:
                return None
            return _parse_summary(payload)

        return with_retry(
            _call,
            attempts=self._attempts,
            base_delay=self._base_delay,
            logger=logger,
            description=f"Wikimedia summary {normalized}",
            exceptions=(WikimediaClientError,),
        )

    def media(self, title: str) -> Optional[WikimediaMedia]:
        normalized = _normalize_title(title)
        def _call() -> Optional[WikimediaMedia]:
            try:
                payload = self._media_fetcher(normalized)
            except WikimediaClientError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise WikimediaClientError(
                    f"Media lookup failed for '{normalized}': {exc}",
                    retryable=True,
                ) from exc

            if not payload:
                return None
            return _parse_media(payload)

        return with_retry(
            _call,
            attempts=self._attempts,
            base_delay=self._base_delay,
            logger=logger,
            description=f"Wikimedia media {normalized}",
            exceptions=(WikimediaClientError,),
        )

    def _fetch_summary(self, title: str) -> Dict[str, Any]:
        start = time.perf_counter()
        response = self._summary_client.get(f"/page/summary/{quote(title)}")
        duration = time.perf_counter() - start
        if response.status_code == 404:
            return {}
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WikimediaClientError(
                f"Wikimedia summary request failed for '{title}': {exc.response.status_code}",
                retryable=500 <= exc.response.status_code < 600 or exc.response.status_code == 429,
            ) from exc
        logger.info(
            "Wikimedia request success",
            extra={
                "event": "wikimedia_request",
                "operation": "summary",
                "status": response.status_code,
                "duration": duration,
            },
        )
        return response.json()

    def _fetch_media(self, title: str) -> Dict[str, Any]:
        start = time.perf_counter()
        response = self._media_client.get(f"/page/media/{quote(title)}")
        duration = time.perf_counter() - start
        if response.status_code == 404:
            return {}
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WikimediaClientError(
                f"Wikimedia media request failed for '{title}': {exc.response.status_code}",
                retryable=500 <= exc.response.status_code < 600 or exc.response.status_code == 429,
            ) from exc
        logger.info(
            "Wikimedia request success",
            extra={
                "event": "wikimedia_request",
                "operation": "media",
                "status": response.status_code,
                "duration": duration,
            },
        )
        return response.json()


def _parse_summary(payload: Dict[str, Any]) -> Optional[WikimediaSummary]:
    title = payload.get("title")
    extract = payload.get("extract")
    if not isinstance(title, str) or not isinstance(extract, str):
        return None

    content_urls = payload.get("content_urls") or {}
    desktop = content_urls.get("desktop") if isinstance(content_urls, dict) else {}
    page_url = desktop.get("page") if isinstance(desktop, dict) else None

    thumbnail = payload.get("thumbnail")
    thumbnail_url = thumbnail.get("source") if isinstance(thumbnail, dict) else None

    return WikimediaSummary(
        title=title,
        extract=extract,
        page_url=page_url,
        thumbnail_url=thumbnail_url,
        raw=dict(payload),
    )


def _parse_media(payload: Dict[str, Any]) -> Optional[WikimediaMedia]:
    items = payload.get("items")
    if not isinstance(items, list):
        return None

    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "image":
            continue

        original = item.get("original") or {}
        image_url = original.get("source")
        if not isinstance(image_url, str) or not image_url:
            continue

        thumbnail = item.get("thumbnail") or {}
        thumbnail_url = thumbnail.get("source") if isinstance(thumbnail, dict) else None

        license_info = item.get("license") or {}
        license_code = None
        if isinstance(license_info, dict):
            license_code = (
                license_info.get("code")
                or license_info.get("name")
                or license_info.get("title")
            )

        attribution = None
        artist = item.get("artist")
        if isinstance(artist, dict):
            attribution = artist.get("name") or artist.get("string")
        if not attribution:
            attribution = item.get("credit")

        page_url = item.get("file_page") or item.get("title")

        return WikimediaMedia(
            title=str(item.get("title", "")),
            image_url=image_url,
            thumbnail_url=thumbnail_url,
            license_code=license_code,
            attribution=attribution,
            page_url=page_url if isinstance(page_url, str) else None,
            raw=dict(payload),
        )
    return None


def build_wikimedia_stub(
    *,
    summaries: Optional[Dict[str, Dict[str, Any]]] = None,
    media: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Callable[[str], Dict[str, Any]]]:
    """
    Build stub fetchers for WikimediaClient.

    Returns a dict with 'summary' and 'media' callables compatible with the
    summary_fetcher/media_fetcher constructor arguments.
    """

    summary_map = {key.lower(): dict(value) for key, value in (summaries or {}).items()}
    media_map = {key.lower(): dict(value) for key, value in (media or {}).items()}

    def summary_fetcher(title: str) -> Dict[str, Any]:
        return summary_map.get(title.strip().lower(), {})

    def media_fetcher(title: str) -> Dict[str, Any]:
        return media_map.get(title.strip().lower(), {})

    return {"summary": summary_fetcher, "media": media_fetcher}
