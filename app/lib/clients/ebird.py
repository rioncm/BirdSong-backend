from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup  # type: ignore[import]

from lib.utils.retry import with_retry


__all__ = [
    "EbirdClient",
    "EbirdClientError",
    "EbirdSpeciesData",
]


logger = logging.getLogger("birdsong.clients.ebird")


class EbirdClientError(RuntimeError):
    """Raised when eBird requests fail."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class EbirdSpeciesData:
    species_code: str
    info_url: str
    summary: Optional[str]
    raw_taxonomy: Dict[str, Any]


def _normalize_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = value.strip()
    return normalized.lower() if normalized else None


class EbirdClient:
    """
    Lightweight eBird client that retrieves taxonomy metadata (species codes)
    and scrapes the public species page for the identification summary.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.ebird.org/v2",
        website_url: str = "https://ebird.org",
        timeout: float = 10.0,
        user_agent: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if not api_key:
            raise ValueError("eBird API key must be provided")

        headers = {
            "X-eBirdApiToken": api_key,
            "Accept": "application/json",
        }
        if user_agent:
            headers["User-Agent"] = user_agent

        self._api_client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers=headers,
            transport=transport,
        )
        self._html_client = httpx.Client(
            base_url=website_url,
            timeout=timeout,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": user_agent or "BirdSong/0.1 (+https://github.com/rion/BirdSong)",
            },
            transport=transport,
        )

    def close(self) -> None:
        self._api_client.close()
        self._html_client.close()

    def lookup_species(
        self,
        scientific_name: str,
        *,
        common_name: Optional[str] = None,
    ) -> Optional[EbirdSpeciesData]:
        normalized_scientific = _normalize_name(scientific_name)
        normalized_common = _normalize_name(common_name)
        if not normalized_scientific and not normalized_common:
            raise ValueError("At least one of scientific_name or common_name must be provided")

        taxonomy_entry = self._find_taxonomy_entry(
            normalized_scientific,
            normalized_common,
        )
        if taxonomy_entry is None:
            return None

        species_code = (
            taxonomy_entry.get("speciesCode")
            or taxonomy_entry.get("SPECIES_CODE")
            or taxonomy_entry.get("species_code")
        )
        if not isinstance(species_code, str) or not species_code:
            logger.debug("eBird taxonomy entry missing species code: %s", taxonomy_entry)
            return None

        species_code = species_code.lower()
        info_url = urljoin(self._html_client.base_url, f"/species/{species_code}")
        summary = self._fetch_identification_summary(species_code)

        return EbirdSpeciesData(
            species_code=species_code,
            info_url=info_url,
            summary=summary,
            raw_taxonomy=taxonomy_entry,
        )

    def _find_taxonomy_entry(
        self,
        scientific_name: Optional[str],
        common_name: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        dataset = self._load_taxonomy_dataset()
        if not dataset:
            return None

        def _matches(entry: Dict[str, Any], candidates: Iterable[Optional[str]]) -> bool:
            for candidate in candidates:
                if not candidate:
                    continue
                for key in ("sciName", "SCI_NAME", "scientificName", "SCIENTIFIC_NAME"):
                    value = entry.get(key)
                    if isinstance(value, str) and _normalize_name(value) == candidate:
                        return True
                for key in ("comName", "COM_NAME"):
                    value = entry.get(key)
                    if isinstance(value, str) and _normalize_name(value) == candidate:
                        return True
            return False

        for entry in dataset:
            if not isinstance(entry, dict):
                continue
            if _matches(entry, (scientific_name, common_name)):
                return dict(entry)

        return None

    @lru_cache(maxsize=1)
    def _load_taxonomy_dataset(self) -> Optional[Iterable[Dict[str, Any]]]:
        def _call() -> Iterable[Dict[str, Any]]:
            response = self._api_client.get(
                "/ref/taxonomy/ebird",
                params={"fmt": "json"},
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:  # noqa: BLE001
                raise EbirdClientError(
                    f"eBird taxonomy request failed: {exc.response.status_code}",
                    retryable=500 <= exc.response.status_code < 600,
                ) from exc

            try:
                payload = response.json()
            except json.JSONDecodeError as exc:  # noqa: BLE001
                raise EbirdClientError(
                    f"Failed to decode eBird taxonomy response: {exc}",
                    retryable=True,
                ) from exc

            if not isinstance(payload, list):
                raise EbirdClientError(
                    "Unexpected taxonomy payload: expected list",
                    retryable=False,
                )
            logger.info(
                "Loaded eBird taxonomy dataset",
                extra={"entries": len(payload)},
            )
            return payload

        try:
            return with_retry(
                _call,
                attempts=2,
                base_delay=0.5,
                logger=logger,
                description="eBird taxonomy dataset",
                exceptions=(EbirdClientError,),
            )
        except EbirdClientError as exc:
            if exc.retryable:
                logger.warning("eBird taxonomy request failed: %s", exc)
            else:
                logger.error("eBird taxonomy request failed: %s", exc)
            return None

    @lru_cache(maxsize=512)
    def _fetch_identification_summary(self, species_code: str) -> Optional[str]:
        if not species_code:
            return None

        def _call() -> Optional[str]:
            response = self._html_client.get(f"/species/{species_code.lower()}")
            if response.status_code == 404:
                logger.info("eBird species page not found for code '%s'", species_code)
                return None
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:  # noqa: BLE001
                raise EbirdClientError(
                    f"eBird species page request failed: {exc.response.status_code}",
                    retryable=500 <= exc.response.status_code < 600,
                ) from exc

            soup = BeautifulSoup(response.text, "html.parser")
            identify_section = (
                soup.find(id="identify")
                or soup.find("section", {"data-tab-panel": "identify"})
                or soup.find("section", {"data-component": "SpeciesProfileIdentification"})
            )
            if identify_section is None:
                heading = soup.find(lambda node: node.name in ("h2", "h3") and node.get_text(strip=True).lower() == "identification")
                identify_section = heading.find_parent("section") if heading else None
            if identify_section is None:
                logger.info("Identification section not found for species '%s'", species_code)
                return None

            paragraphs = identify_section.find_all(["p", "li"])
            if not paragraphs:
                return None

            summary_parts = []
            for node in paragraphs:
                text = node.get_text(" ", strip=True)
                if text:
                    summary_parts.append(text)
                if len(summary_parts) >= 4:
                    break
            return " ".join(summary_parts) if summary_parts else None

        try:
            return with_retry(
                _call,
                attempts=2,
                base_delay=0.5,
                logger=logger,
                description=f"eBird species summary {species_code}",
                exceptions=(EbirdClientError,),
            )
        except EbirdClientError as exc:
            if exc.retryable:
                logger.warning("eBird summary fetch failed for '%s': %s", species_code, exc)
            else:
                logger.error("eBird summary fetch failed for '%s': %s", species_code, exc)
            return None
