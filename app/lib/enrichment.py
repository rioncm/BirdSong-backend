from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Dict, Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lib.clients import WikimediaClient, WikimediaClientError, WikimediaMedia, WikimediaSummary
from lib.data import crud
from lib.data.db import get_session
from lib.source import GbifTaxaClient, GbifTaxon, ThirdPartySourceError


logger = logging.getLogger("birdsong.enrichment")


__all__ = [
    "SpeciesEnrichmentError",
    "SpeciesEnrichmentResult",
    "SpeciesEnricher",
]


class SpeciesEnrichmentError(RuntimeError):
    """Raised when species enrichment fails."""


@dataclass(frozen=True)
class SpeciesEnrichmentResult:
    species_id: str
    created: bool
    gbif_taxon: Optional[GbifTaxon]
    wikimedia_summary: Optional[WikimediaSummary]
    wikimedia_media: Optional[WikimediaMedia]


class SpeciesEnricher:
    """
    Coordinates taxonomy + media enrichment and persists the results.
    """

    def __init__(
        self,
        *,
        gbif_client: Optional[GbifTaxaClient] = None,
        wikimedia_client: Optional[WikimediaClient] = None,
    ) -> None:
        self._gbif_client = gbif_client or GbifTaxaClient()
        self._wikimedia_client = wikimedia_client or WikimediaClient()
        self._species_cache: Dict[str, str] = {}

    def close(self) -> None:
        try:
            self._wikimedia_client.close()
        except Exception:  # noqa: BLE001 - best effort cleanup
            logger.debug("Failed to close Wikimedia client", exc_info=True)

    def ensure_species(
        self,
        scientific_name: str,
        *,
        common_name: Optional[str] = None,
    ) -> SpeciesEnrichmentResult:
        """
        Guarantee that a species record exists for the given detection.

        If the species already exists, no external calls are made.
        Otherwise this method fetches taxonomy data from GBIF and media/summary
        from Wikimedia, inserts the species row, and records citations.
        """
        normalized = scientific_name.strip()
        if not normalized:
            raise SpeciesEnrichmentError("scientific_name must be a non-empty string")

        cache_hit = self._species_cache.get(normalized.lower())
        if cache_hit:
            return SpeciesEnrichmentResult(
                species_id=cache_hit,
                created=False,
                gbif_taxon=None,
                wikimedia_summary=None,
                wikimedia_media=None,
            )

        candidate_species_id = crud.generate_species_id(normalized)

        with _managed_session() as session:
            existing = crud.get_species_by_id(session, candidate_species_id)
            if existing is None:
                try:
                    existing = crud.get_species_by_scientific_name(session, normalized)
                except ValueError:
                    existing = None
            if existing is not None:
                species_id = existing["id"]
                self._remember_species(species_id, normalized, existing.get("sci_name"))
                return SpeciesEnrichmentResult(
                    species_id=species_id,
                    created=False,
                    gbif_taxon=None,
                    wikimedia_summary=None,
                    wikimedia_media=None,
                )

            taxon = self._lookup_taxon(scientific_name, common_name=common_name)
            taxonomy_name = (
                taxon.scientific_name
                if taxon and taxon.scientific_name
                else normalized
            )
            species_id = crud.generate_species_id(taxonomy_name)

            existing = crud.get_species_by_id(session, species_id)
            if existing is not None:
                self._remember_species(
                    species_id, normalized, taxonomy_name, existing.get("sci_name")
                )
                return SpeciesEnrichmentResult(
                    species_id=species_id,
                    created=False,
                    gbif_taxon=taxon,
                    wikimedia_summary=None,
                    wikimedia_media=None,
                )

            summary = self._lookup_summary(taxon, scientific_name, common_name)
            media = self._lookup_media(taxon, scientific_name, common_name)

            species_payload = _build_species_payload(
                species_id=species_id,
                scientific_name=taxonomy_name,
                common_name=common_name,
                taxon=taxon,
                wikimedia_summary=summary,
                wikimedia_media=media,
            )

            try:
                crud.upsert_species(session, species_payload)
                _record_citations(session, species_id, taxon, summary, media)
                session.commit()
            except SQLAlchemyError as exc:  # noqa: BLE001
                session.rollback()
                raise SpeciesEnrichmentError(f"Database error during species upsert: {exc}") from exc

            self._remember_species(
                species_id,
                normalized,
                taxonomy_name,
                taxon.scientific_name if taxon else None,
                summary.title if summary else None,
            )

            return SpeciesEnrichmentResult(
                species_id=species_id,
                created=True,
                gbif_taxon=taxon,
                wikimedia_summary=summary,
                wikimedia_media=media,
            )

    def _lookup_taxon(
        self,
        scientific_name: str,
        *,
        common_name: Optional[str],
    ) -> Optional[GbifTaxon]:
        try:
            taxon = self._gbif_client.lookup(scientific_name, raise_on_missing=False)
        except ThirdPartySourceError as exc:
            logger.warning("GBIF lookup failed for '%s': %s", scientific_name, exc)
            taxon = None

        if taxon is None and common_name:
            try:
                taxon = self._gbif_client.lookup(common_name, raise_on_missing=False)
            except ThirdPartySourceError as exc:
                logger.warning("GBIF lookup failed for common name '%s': %s", common_name, exc)
                taxon = None

        return taxon

    def _lookup_summary(
        self,
        taxon: Optional[GbifTaxon],
        scientific_name: str,
        common_name: Optional[str],
    ) -> Optional[WikimediaSummary]:
        lookup_titles = []
        if taxon and taxon.canonical_name:
            lookup_titles.append(taxon.canonical_name)
        lookup_titles.append(scientific_name)
        if common_name:
            lookup_titles.append(common_name)

        seen = set()
        for title in lookup_titles:
            normalized = title.strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            try:
                summary = self._wikimedia_client.summary(normalized)
            except WikimediaClientError as exc:
                logger.info("Wikimedia summary lookup failed for '%s': %s", normalized, exc)
                continue
            if summary:
                return summary
        return None

    def _remember_species(self, species_id: str, *names: Optional[str]) -> None:
        for name in names:
            if not name:
                continue
            normalized = name.strip().lower()
            if normalized:
                self._species_cache[normalized] = species_id

    def _lookup_media(
        self,
        taxon: Optional[GbifTaxon],
        scientific_name: str,
        common_name: Optional[str],
    ) -> Optional[WikimediaMedia]:
        lookup_titles = []
        if taxon and taxon.canonical_name:
            lookup_titles.append(taxon.canonical_name)
        lookup_titles.append(scientific_name)
        if common_name:
            lookup_titles.append(common_name)

        seen = set()
        for title in lookup_titles:
            normalized = title.strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            try:
                media = self._wikimedia_client.media(normalized)
            except WikimediaClientError as exc:
                logger.info("Wikimedia media lookup failed for '%s': %s", normalized, exc)
                continue
            if media:
                return media
        return None


def _record_citations(
    session: Session,
    species_id: str,
    taxon: Optional[GbifTaxon],
    summary: Optional[WikimediaSummary],
    media: Optional[WikimediaMedia],
) -> None:
    gbif_source_id = crud.get_data_source_id(session, "Global Biodiversity Information Facility")
    if gbif_source_id and taxon:
        crud.upsert_data_citation(
            session,
            source_id=gbif_source_id,
            species_id=species_id,
            data_type="taxa",
            content=json.dumps(taxon.raw, ensure_ascii=False),
        )

    wikimedia_source_id = crud.get_data_source_id(session, "Wikimedia Commons")
    if wikimedia_source_id:
        if summary:
            summary_payload = {
                "title": summary.title,
                "extract": summary.extract,
                "page_url": summary.page_url,
                "thumbnail_url": summary.thumbnail_url,
            }
            crud.upsert_data_citation(
                session,
                source_id=wikimedia_source_id,
                species_id=species_id,
                data_type="copy",
                content=json.dumps(summary_payload, ensure_ascii=False),
            )
        if media:
            media_payload = {
                "title": media.title,
                "image_url": media.image_url,
                "thumbnail_url": media.thumbnail_url,
                "license": media.license_code,
                "attribution": media.attribution,
                "page_url": media.page_url,
            }
            crud.upsert_data_citation(
                session,
                source_id=wikimedia_source_id,
                species_id=species_id,
                data_type="image",
                content=json.dumps(media_payload, ensure_ascii=False),
            )


def _build_species_payload(
    *,
    species_id: str,
    scientific_name: str,
    common_name: Optional[str],
    taxon: Optional[GbifTaxon],
    wikimedia_summary: Optional[WikimediaSummary],
    wikimedia_media: Optional[WikimediaMedia],
) -> dict:
    genus = None
    family = None
    species_epithet = None
    if taxon:
        genus = taxon.genus or genus
        family = taxon.family or family
        species_epithet = taxon.species or species_epithet

    if not species_epithet and scientific_name:
        parts = scientific_name.split()
        if len(parts) > 1:
            genus = genus or parts[0]
            species_epithet = parts[-1]

    image_url = wikimedia_media.image_url if wikimedia_media else None
    info_url = wikimedia_summary.page_url if wikimedia_summary else None
    summary_text = wikimedia_summary.extract if wikimedia_summary else None

    fallback_common = (
        common_name
        or (taxon.common_name if taxon and taxon.common_name else None)
    )

    return {
        "id": species_id,
        "sci_name": scientific_name,
        "species": species_epithet,
        "genus": genus,
        "family": family,
        "common_name": fallback_common or scientific_name,
        "image_url": image_url,
        "info_url": info_url,
        "ai_summary": summary_text,
    }


class _managed_session:
    """
    Context manager wrapper so we can use SQLAlchemy sessions cleanly
    without depending on SQLAlchemy 2.0 style APIs.
    """

    def __enter__(self) -> Session:
        self._session = get_session()
        return self._session

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None:
            self._session.rollback()
        self._session.close()
