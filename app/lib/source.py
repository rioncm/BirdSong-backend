from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from pygbif import species as gbif_species

from lib.utils.retry import with_retry


__all__ = [
    "ThirdPartySourceError",
    "TaxonNotFoundError",
    "GbifTaxon",
    "GbifTaxaClient",
    "build_gbif_stub",
]


logger = logging.getLogger("birdsong.clients.gbif")


class ThirdPartySourceError(RuntimeError):
    """Raised when a third-party data provider request fails."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class TaxonNotFoundError(ThirdPartySourceError):
    """Raised when a taxon lookup returns no GBIF match."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


@dataclass(frozen=True)
class GbifTaxon:
    """
    Lightweight container for GBIF backbone taxonomy results.

    Only the most frequently used fields are normalized; the raw payload is
    retained for callers that need additional detail without another request.
    """

    usage_key: Optional[int]
    scientific_name: str
    canonical_name: Optional[str]
    rank: Optional[str]
    match_type: Optional[str]
    status: Optional[str]
    confidence: Optional[int]
    kingdom: Optional[str]
    phylum: Optional[str]
    taxon_class: Optional[str]
    order: Optional[str]
    family: Optional[str]
    genus: Optional[str]
    species: Optional[str]
    common_name: Optional[str]
    raw: Dict[str, Any]

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "GbifTaxon":
        """Convert the GBIF backbone payload into a normalized object."""
        usage_key = payload.get("usageKey")
        if usage_key is not None:
            try:
                usage_key = int(usage_key)
            except (TypeError, ValueError):
                usage_key = None

        confidence = payload.get("confidence")
        if confidence is not None:
            try:
                confidence = int(confidence)
            except (TypeError, ValueError):
                confidence = None

        common_name_candidates: Iterable[str] = (
            payload.get("vernacularName"),
            payload.get("vernacularNameEng"),
            payload.get("species"),
        )
        common_name = next(
            (value for value in common_name_candidates if isinstance(value, str)),
            None,
        )

        return cls(
            usage_key=usage_key,
            scientific_name=str(payload.get("scientificName") or ""),
            canonical_name=payload.get("canonicalName"),
            rank=payload.get("rank"),
            match_type=payload.get("matchType"),
            status=payload.get("status"),
            confidence=confidence,
            kingdom=payload.get("kingdom"),
            phylum=payload.get("phylum"),
            taxon_class=payload.get("class"),
            order=payload.get("order"),
            family=payload.get("family"),
            genus=payload.get("genus"),
            species=payload.get("species"),
            common_name=common_name,
            raw=dict(payload),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return the normalized fields as a plain dictionary."""
        return {
            "usage_key": self.usage_key,
            "scientific_name": self.scientific_name,
            "canonical_name": self.canonical_name,
            "rank": self.rank,
            "match_type": self.match_type,
            "status": self.status,
            "confidence": self.confidence,
            "kingdom": self.kingdom,
            "phylum": self.phylum,
            "class": self.taxon_class,
            "order": self.order,
            "family": self.family,
            "genus": self.genus,
            "species": self.species,
            "common_name": self.common_name,
        }


def _sorted_items(data: Dict[str, Any]) -> Tuple[Tuple[str, Any], ...]:
    """
    Produce a hashable, deterministic representation of a dict suitable for caching.
    """
    return tuple(sorted((key, data[key]) for key in data))


@lru_cache(maxsize=512)
def _cached_name_backbone(
    name: str,
    params_key: Tuple[Tuple[str, Any], ...],
) -> Dict[str, Any]:
    """Cached wrapper around pygbif's backbone name lookup."""
    params = dict(params_key)
    return gbif_species.name_backbone(name=name, **params)


def _default_gbif_fetch(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return _cached_name_backbone(name.strip(), _sorted_items(params))


def build_gbif_stub(
    responses: Dict[str, Dict[str, Any]],
    *,
    default: Optional[Dict[str, Any]] = None,
) -> Callable[[str, Dict[str, Any]], Dict[str, Any]]:
    """
    Produce a stub fetcher compatible with GbifTaxaClient for integration tests.

    Args:
        responses: mapping of lookup name (any casing) to the GBIF payload.
        default: optional payload returned when the name is missing.

    Returns:
        Callable that can be supplied to GbifTaxaClient(fetch_func=...).
    """
    normalized = {key.strip().lower(): dict(value) for key, value in responses.items()}
    default_payload = dict(default) if default is not None else {}

    def _fetch(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        # params retained in signature for parity with live fetcher; not used here.
        _ = params  # noqa: F841 - intentional no-op
        payload = normalized.get(name.strip().lower())
        if payload is None:
            return dict(default_payload)
        return dict(payload)

    return _fetch


class GbifTaxaClient:
    """
    Thin, cached wrapper around GBIF backbone lookups for taxonomy data.

    The client stores default parameters for all calls and exposes a lookup
    method that returns normalized data ready for downstream persistence.
    """

    def __init__(
        self,
        *,
        default_params: Optional[Dict[str, Any]] = None,
        raise_on_missing: bool = True,
        fetch_func: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
        attempts: int = 3,
        base_delay: float = 0.5,
    ) -> None:
        self._default_params = {
            key: value
            for key, value in (default_params or {}).items()
            if value is not None
        }
        self._raise_on_missing = raise_on_missing
        self._fetch_func = fetch_func or _default_gbif_fetch
        self._attempts = max(1, attempts)
        self._base_delay = base_delay

    def lookup(
        self,
        name: str,
        *,
        raise_on_missing: Optional[bool] = None,
        **overrides: Any,
    ) -> Optional[GbifTaxon]:
        """
        Look up a taxon by scientific or common name in GBIF.

        Parameters mirror pygbif.species.name_backbone; any overrides supplied
        per-call are merged on top of the client's defaults.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")

        merged_params = dict(self._default_params)
        merged_params.update({k: v for k, v in overrides.items() if v is not None})

        def _call() -> Dict[str, Any]:
            try:
                return self._fetch_func(name.strip(), merged_params)
            except ThirdPartySourceError:
                raise
            except Exception as exc:  # noqa: BLE001 - map to domain-specific error
                raise ThirdPartySourceError(
                    f"GBIF lookup failed for '{name}': {exc}",
                    retryable=True,
                ) from exc

        payload = with_retry(
            _call,
            attempts=self._attempts,
            base_delay=self._base_delay,
            logger=logger,
            description=f"GBIF lookup {name}",
            exceptions=(ThirdPartySourceError,),
        )

        logger.info(
            "GBIF lookup success",
            extra={
                "event": "gbif_request",
                "query": name,
            },
        )

        if not payload or payload.get("matchType") == "NONE":
            should_raise = (
                self._raise_on_missing if raise_on_missing is None else raise_on_missing
            )
            if should_raise:
                raise TaxonNotFoundError(f"No GBIF match found for '{name}'")
            return None

        return GbifTaxon.from_payload(payload)
