from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lib.clients import WikimediaClient, WikimediaClientError, WikimediaMedia, WikimediaSummary
from lib.clients.ebird import EbirdClient, EbirdClientError, EbirdSpeciesData
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
    ebird_data: Optional[EbirdSpeciesData]


class SpeciesEnricher:
    """
    Coordinates taxonomy + media enrichment and persists the results.
    """

    def __init__(
        self,
        *,
        gbif_client: Optional[GbifTaxaClient] = None,
        wikimedia_client: Optional[WikimediaClient] = None,
        ebird_client: Optional[EbirdClient] = None,
        images_dir: Optional[Path] = None,
    ) -> None:
        self._gbif_client = gbif_client or GbifTaxaClient()
        self._wikimedia_client = wikimedia_client or WikimediaClient()
        self._ebird_client = ebird_client
        self._images_dir = Path(images_dir) if images_dir else None
        if self._images_dir is not None:
            self._images_dir.mkdir(parents=True, exist_ok=True)
        self._species_cache: Dict[str, str] = {}
        self._http_client = httpx.Client(timeout=10.0)

    def close(self) -> None:
        try:
            self._wikimedia_client.close()
        except Exception:  # noqa: BLE001 - best effort cleanup
            logger.debug("Failed to close Wikimedia client", exc_info=True)
        if self._ebird_client is not None:
            try:
                self._ebird_client.close()
            except Exception:  # noqa: BLE001
                logger.debug("Failed to close eBird client", exc_info=True)
        try:
            self._http_client.close()
        except Exception:  # noqa: BLE001
            logger.debug("Failed to close enrichment HTTP client", exc_info=True)

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
                ebird_data=None,
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

                if self._species_requires_refresh(existing):
                    session.rollback()
                    taxon = self._lookup_taxon(scientific_name, common_name=common_name or existing.get("common_name"))
                    taxonomy_name = (
                        existing.get("sci_name")
                        or (taxon.scientific_name if taxon and taxon.scientific_name else normalized)
                    )
                    summary = self._lookup_summary(taxon, taxonomy_name, common_name or existing.get("common_name"))
                    media = self._lookup_media(taxon, taxonomy_name, common_name or existing.get("common_name"))
                    ebird_data = self._lookup_ebird(
                        taxonomy_name,
                        common_name=common_name or existing.get("common_name"),
                    )
                    cached_image_url = self._cache_media_image(
                        species_id,
                        media,
                        preferred_name=taxonomy_name,
                    )
                    if not cached_image_url:
                        cached_image_url = existing.get("image_url")

                    species_payload = _build_species_payload(
                        species_id=species_id,
                        scientific_name=taxonomy_name,
                        common_name=common_name or existing.get("common_name"),
                        taxon=taxon,
                        wikimedia_summary=summary,
                        wikimedia_media=media,
                        ebird_data=ebird_data,
                        cached_image_url=cached_image_url,
                    )

                    try:
                        crud.upsert_species(session, species_payload)
                        _record_citations(session, species_id, taxon, summary, media, ebird_data)
                        session.commit()
                    except SQLAlchemyError as exc:  # noqa: BLE001
                        session.rollback()
                        raise SpeciesEnrichmentError(
                            f"Database error during species metadata refresh: {exc}"
                        ) from exc

                    return SpeciesEnrichmentResult(
                        species_id=species_id,
                        created=False,
                        gbif_taxon=taxon,
                        wikimedia_summary=summary,
                        wikimedia_media=media,
                        ebird_data=ebird_data,
                    )

                return SpeciesEnrichmentResult(
                    species_id=species_id,
                    created=False,
                    gbif_taxon=None,
                    wikimedia_summary=None,
                    wikimedia_media=None,
                    ebird_data=None,
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
                    ebird_data=None,
                )

            summary = self._lookup_summary(taxon, taxonomy_name, common_name)
            media = self._lookup_media(taxon, taxonomy_name, common_name)
            ebird_data = self._lookup_ebird(
                taxonomy_name,
                common_name=common_name or (taxon.common_name if taxon else None),
            )
            cached_image_url = self._cache_media_image(
                species_id,
                media,
                preferred_name=taxonomy_name,
            )

            species_payload = _build_species_payload(
                species_id=species_id,
                scientific_name=taxonomy_name,
                common_name=common_name,
                taxon=taxon,
                wikimedia_summary=summary,
                wikimedia_media=media,
                ebird_data=ebird_data,
                cached_image_url=cached_image_url,
            )

            try:
                crud.upsert_species(session, species_payload)
                _record_citations(session, species_id, taxon, summary, media, ebird_data)
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
                ebird_data=ebird_data,
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

    def _lookup_ebird(
        self,
        scientific_name: str,
        *,
        common_name: Optional[str],
    ) -> Optional[EbirdSpeciesData]:
        if self._ebird_client is None:
            return None
        try:
            return self._ebird_client.lookup_species(
                scientific_name,
                common_name=common_name,
            )
        except (EbirdClientError, ValueError) as exc:
            logger.info("eBird lookup failed for '%s': %s", scientific_name, exc)
            return None

    def _cache_media_image(
        self,
        species_id: str,
        media: Optional[WikimediaMedia],
        *,
        preferred_name: Optional[str],
    ) -> Optional[str]:
        if self._images_dir is None or media is None:
            return media.image_url if media else None

        source_url = media.thumbnail_url or media.image_url
        if not source_url:
            return None

        try:
            parsed = httpx.URL(source_url)
        except ValueError:
            logger.info("Invalid media URL for species %s: %s", species_id, source_url)
            return None

        extension = Path(parsed.path).suffix or ".jpg"
        name_part = preferred_name or media.title or species_id
        safe_name = (
            name_part.strip().lower().replace(" ", "-").replace("/", "-").replace("'", "")
        )
        filename = f"{species_id}-{safe_name}{extension}"
        target_path = self._images_dir / filename

        if not target_path.exists():
            try:
                response = self._http_client.get(source_url)
                response.raise_for_status()
            except httpx.HTTPError as exc:  # noqa: BLE001
                logger.info("Failed to cache species image for %s: %s", species_id, exc)
                return None
            target_path.write_bytes(response.content)

        return f"/images/{filename}"

    @staticmethod
    def _species_requires_refresh(existing: Dict[str, object]) -> bool:
        for field in (
            "summary",
            "info_url",
            "image_url",
            "genus",
            "family",
            "species",
            "ebird_code",
        ):
            value = existing.get(field)
            if value in (None, ""):
                return True
        return False


def _record_citations(
    session: Session,
    species_id: str,
    taxon: Optional[GbifTaxon],
    summary: Optional[WikimediaSummary],
    media: Optional[WikimediaMedia],
    ebird_data: Optional[EbirdSpeciesData],
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

    ebird_source_id = crud.get_data_source_id(session, "eBird")
    if ebird_source_id and ebird_data:
        ebird_payload = {
            "species_code": ebird_data.species_code,
            "info_url": ebird_data.info_url,
            "summary": ebird_data.summary,
            "raw": ebird_data.raw_taxonomy,
        }
        crud.upsert_data_citation(
            session,
            source_id=ebird_source_id,
            species_id=species_id,
            data_type="copy",
            content=json.dumps(ebird_payload, ensure_ascii=False),
        )


def _build_species_payload(
    *,
    species_id: str,
    scientific_name: str,
    common_name: Optional[str],
    taxon: Optional[GbifTaxon],
    wikimedia_summary: Optional[WikimediaSummary],
    wikimedia_media: Optional[WikimediaMedia],
    ebird_data: Optional[EbirdSpeciesData],
    cached_image_url: Optional[str],
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

    image_url = cached_image_url or (wikimedia_media.image_url if wikimedia_media else None)
    info_url = (
        ebird_data.info_url
        if ebird_data and ebird_data.info_url
        else wikimedia_summary.page_url if wikimedia_summary else None
    )
    summary_text = (
        ebird_data.summary
        if ebird_data and ebird_data.summary
        else wikimedia_summary.extract if wikimedia_summary else None
    )

    fallback_common = (
        common_name
        or (taxon.common_name if taxon and taxon.common_name else None)
    )
    ebird_code = (
        ebird_data.species_code
        if ebird_data and ebird_data.species_code
        else None
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
        "summary": summary_text,
        "ebird_code": ebird_code,
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
