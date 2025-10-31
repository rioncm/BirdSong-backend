from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from html import unescape
from typing import Any, Callable, Dict, Optional, Sequence
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
    attribution_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


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
        commons_base_url: str = "https://commons.wikimedia.org/w/rest.php/v1",
        timeout: float = 5.0,
        user_agent: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
        summary_fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
        search_fetcher: Optional[Callable[[str, int], Sequence[Dict[str, Any]]]] = None,
        file_fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
        attempts: int = 3,
        base_delay: float = 0.5,
        search_limit: int = 5,
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
        self._commons_client = httpx.Client(
            base_url=commons_base_url,
            timeout=timeout,
            headers=_default_headers(resolved_user_agent),
            transport=transport,
        )
        self._summary_fetcher = summary_fetcher or self._fetch_summary
        self._search_fetcher = search_fetcher or self._search_commons
        self._file_fetcher = file_fetcher or self._fetch_file
        self._attempts = max(1, attempts)
        self._base_delay = base_delay
        self._search_limit = max(1, search_limit)

    def close(self) -> None:
        self._summary_client.close()
        self._commons_client.close()

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
                search_results = self._search_fetcher(normalized, self._search_limit)
            except WikimediaClientError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise WikimediaClientError(
                    f"Media search failed for '{normalized}': {exc}",
                    retryable=True,
                ) from exc

            if not search_results:
                return None

            for entry in search_results:
                if not isinstance(entry, dict):
                    continue
                file_key = entry.get("key")
                if not isinstance(file_key, str) or not file_key:
                    continue
                normalized_key = file_key.replace(" ", "_")
                try:
                    file_payload = self._file_fetcher(normalized_key)
                except WikimediaClientError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    raise WikimediaClientError(
                        f"Media lookup failed for '{normalized_key}': {exc}",
                        retryable=True,
                    ) from exc
                if not file_payload:
                    continue
                media = _parse_commons_media(file_payload, entry)
                if media:
                    return media
            return None

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

    def _search_commons(self, query: str, limit: int) -> Sequence[Dict[str, Any]]:
        start = time.perf_counter()
        response = self._commons_client.get(
            "/search/page",
            params={"q": query, "limit": limit},
        )
        duration = time.perf_counter() - start
        if response.status_code == 404:
            return []
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WikimediaClientError(
                f"Wikimedia Commons search failed for '{query}': {exc.response.status_code}",
                retryable=500 <= exc.response.status_code < 600 or exc.response.status_code == 429,
            ) from exc
        payload = response.json()
        logger.info(
            "Wikimedia request success",
            extra={
                "event": "wikimedia_request",
                "operation": "commons_search",
                "status": response.status_code,
                "duration": duration,
            },
        )
        pages = payload.get("pages")
        if not isinstance(pages, list):
            return []
        return [dict(page) for page in pages if isinstance(page, dict)]

    def _fetch_file(self, file_key: str) -> Dict[str, Any]:
        normalized_key = file_key if file_key.startswith("File:") else f"File:{file_key}"
        normalized_key = normalized_key.replace(" ", "_")
        encoded_key = quote(normalized_key, safe="/:()'_")
        start = time.perf_counter()
        response = self._commons_client.get(f"/file/{encoded_key}")
        duration = time.perf_counter() - start
        if response.status_code == 404:
            return {}
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WikimediaClientError(
                f"Wikimedia Commons file lookup failed for '{normalized_key}': {exc.response.status_code}",
                retryable=500 <= exc.response.status_code < 600 or exc.response.status_code == 429,
            ) from exc
        logger.info(
            "Wikimedia request success",
            extra={
                "event": "wikimedia_request",
                "operation": "commons_file",
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


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", "", text)
    return unescape(cleaned).strip()


def _extract_license(search_entry: Dict[str, Any], file_payload: Dict[str, Any]) -> Optional[str]:
    license_info = file_payload.get("license")
    if isinstance(license_info, dict):
        for key in ("spdx", "short_name", "code", "name"):
            value = license_info.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    excerpt = search_entry.get("excerpt")
    if isinstance(excerpt, str) and excerpt:
        text = _strip_html(excerpt)
        markers = [
            "Creative Commons",
            "Public domain",
            "CC ",
            "GNU",
        ]
        for marker in markers:
            idx = text.find(marker)
            if idx != -1:
                segment = text[idx:]
                # stop at double 'true' or language marker if present
                segment = segment.split("truetrue", 1)[0]
                segment = segment.split("true", 1)[0]
                return segment.strip()
    return None


def _parse_commons_media(
    file_payload: Dict[str, Any],
    search_entry: Optional[Dict[str, Any]] = None,
) -> Optional[WikimediaMedia]:
    preferred = file_payload.get("preferred") or {}
    image_url = preferred.get("url")
    if not isinstance(image_url, str) or not image_url:
        original = file_payload.get("original") or {}
        image_url = original.get("url")
    if not isinstance(image_url, str) or not image_url:
        return None

    thumbnail = file_payload.get("thumbnail") or {}
    thumbnail_url = thumbnail.get("url") if isinstance(thumbnail, dict) else None

    title = file_payload.get("title")
    if not isinstance(title, str):
        if isinstance(search_entry, dict):
            title = search_entry.get("title")
            if not isinstance(title, str):
                title = ""
        else:
            title = ""

    page_url = file_payload.get("file_description_url")
    if isinstance(page_url, str) and page_url.startswith("//"):
        page_url = f"https:{page_url}"
    elif not isinstance(page_url, str):
        page_url = None

    attribution = None
    attribution_url = None
    latest = file_payload.get("latest")
    if isinstance(latest, dict):
        user = latest.get("user")
        if isinstance(user, dict):
            user_name = user.get("name")
            if isinstance(user_name, str) and user_name.strip():
                attribution = user_name.strip()
                attribution_url = f"https://commons.wikimedia.org/wiki/User:{attribution.replace(' ', '_')}"
            user_id = user.get("id")
            if attribution_url is None and isinstance(user_id, int):
                attribution_url = f"https://commons.wikimedia.org/wiki/User:{user_id}"

    license_code = _extract_license(search_entry or {}, file_payload)

    raw_payload = {
        "search": dict(search_entry) if isinstance(search_entry, dict) else None,
        "file": dict(file_payload),
    }

    return WikimediaMedia(
        title=title,
        image_url=image_url,
        thumbnail_url=thumbnail_url,
        license_code=license_code,
        attribution=attribution,
        page_url=page_url,
        attribution_url=attribution_url,
        raw=raw_payload,
    )


def build_wikimedia_stub(
    *,
    summaries: Optional[Dict[str, Dict[str, Any]]] = None,
    searches: Optional[Dict[str, Sequence[Dict[str, Any]]]] = None,
    files: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Callable]:
    """
    Build stub fetchers for WikimediaClient.

    Returns a dict with 'summary', 'search', and 'file' callables compatible with the
    summary_fetcher/search_fetcher/file_fetcher constructor arguments.
    """

    summary_map = {key.lower(): dict(value) for key, value in (summaries or {}).items()}
    search_map = {
        key.lower(): [dict(item) for item in value]
        for key, value in (searches or {}).items()
        if isinstance(value, Sequence)
    }
    file_map = {key.replace(" ", "_"): dict(value) for key, value in (files or {}).items()}

    def summary_fetcher(title: str) -> Dict[str, Any]:
        return summary_map.get(title.strip().lower(), {})

    def search_fetcher(query: str, limit: int) -> Sequence[Dict[str, Any]]:
        entries = search_map.get(query.strip().lower(), [])
        return entries[:limit]

    def file_fetcher(key: str) -> Dict[str, Any]:
        normalized = key.replace(" ", "_")
        return dict(file_map.get(normalized) or file_map.get(key) or {})

    return {"summary": summary_fetcher, "search": search_fetcher, "file": file_fetcher}
