from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest
from sqlalchemy import insert, select

from lib.clients import WikimediaClient
from lib.config import DatabaseConfig
from lib.data import crud
from lib.data import db as db_module
from lib.data.db import get_session, initialize_database
from lib.data.tables import data_citations, data_sources
from lib.enrichment import SpeciesEnricher
from lib.source import GbifTaxaClient


GBIF_PAYLOAD: Dict[str, object] = {
    "usageKey": 2492484,
    "scientificName": "Aphelocoma californica (Vigors, 1839)",
    "canonicalName": "Aphelocoma californica",
    "rank": "SPECIES",
    "matchType": "EXACT",
    "status": "ACCEPTED",
    "confidence": 98,
    "kingdom": "Animalia",
    "phylum": "Chordata",
    "class": "Aves",
    "order": "Passeriformes",
    "family": "Corvidae",
    "genus": "Aphelocoma",
    "species": "Aphelocoma californica",
    "vernacularName": "California Scrub-Jay",
}

SUMMARY_PAYLOAD: Dict[str, object] = {
    "title": "California scrub jay",
    "extract": "The California Scrub-Jay is a bright blue bird native to the western United States.",
    "content_urls": {
        "desktop": {"page": "https://en.wikipedia.org/wiki/California_scrub_jay"}
    },
    "thumbnail": {"source": "https://example.org/summary-thumb.jpg"},
}

MEDIA_PAYLOAD: Dict[str, object] = {
    "items": [
        {
            "type": "image",
            "title": "File:California_Scrub_Jay.jpg",
            "original": {"source": "https://example.org/media.jpg"},
            "thumbnail": {"source": "https://example.org/media-thumb.jpg"},
            "license": {"code": "CC BY-SA 4.0"},
            "artist": {"name": "Jane Doe"},
            "file_page": "https://commons.wikimedia.org/wiki/File:California_Scrub_Jay.jpg",
        }
    ]
}


@pytest.fixture(scope="module")
def temp_database(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("wikimedia-db")
    db_path = Path(tmp_dir) / "test.db"
    config = DatabaseConfig(engine="sqlite", name=db_path.name, path=db_path.parent)

    # reset global state for test database
    db_module._ENGINE = None
    db_module._SESSION_FACTORY = None

    engine = initialize_database(config)

    with engine.begin() as connection:
        connection.execute(
            insert(data_sources).values(
                name="Global Biodiversity Information Facility",
                source_type="taxa",
                cite=True,
            )
        )
        connection.execute(
            insert(data_sources).values(
                name="Wikimedia Commons",
                source_type="image",
                cite=True,
            )
        )

    try:
        yield config
    finally:
        if db_module._ENGINE is not None:
            db_module._ENGINE.dispose()
        db_module._ENGINE = None
        db_module._SESSION_FACTORY = None


def test_species_enricher_creates_species_and_reuses_cache(temp_database):
    counters = {"gbif": 0, "summary": 0, "media": 0}

    def gbif_fetch(name: str, params: Dict[str, object]) -> Dict[str, object]:
        counters["gbif"] += 1
        return dict(GBIF_PAYLOAD)

    def summary_fetch(title: str) -> Dict[str, object]:
        counters["summary"] += 1
        return dict(SUMMARY_PAYLOAD)

    def media_fetch(title: str) -> Dict[str, object]:
        counters["media"] += 1
        return dict(MEDIA_PAYLOAD)

    gbif_client = GbifTaxaClient(fetch_func=gbif_fetch)
    wikimedia_client = WikimediaClient(
        summary_fetcher=summary_fetch,
        media_fetcher=media_fetch,
    )

    enricher = SpeciesEnricher(
        gbif_client=gbif_client,
        wikimedia_client=wikimedia_client,
    )

    result = enricher.ensure_species(
        "Aphelocoma californica",
        common_name="California Scrub-Jay",
    )

    assert result.created is True
    assert counters == {"gbif": 1, "summary": 1, "media": 1}

    session = get_session()
    try:
        species_row = crud.get_species_by_id(session, result.species_id)
        assert species_row is not None
        assert species_row["sci_name"] == "Aphelocoma californica (Vigors, 1839)"
        assert species_row["common_name"] == "California Scrub-Jay"
        assert species_row["image_url"] == "https://example.org/media.jpg"
        assert species_row["summary"].startswith("The California Scrub-Jay")

        citation_rows = session.execute(
            select(data_citations.c.citation_id).where(
                data_citations.c.species_id == result.species_id
            )
        ).all()
        assert len(citation_rows) == 3
    finally:
        session.close()

    repeat = enricher.ensure_species(
        "Aphelocoma californica",
        common_name="California Scrub-Jay",
    )

    assert repeat.created is False
    assert repeat.species_id == result.species_id
    assert counters == {"gbif": 1, "summary": 1, "media": 1}

    session = get_session()
    try:
        citation_rows = session.execute(
            select(data_citations.c.citation_id).where(
                data_citations.c.species_id == result.species_id
            )
        ).all()
        assert len(citation_rows) == 3
    finally:
        session.close()

    enricher.close()


def test_species_enricher_uses_existing_records_without_re_enrichment(temp_database):
    counters = {"gbif": 0, "summary": 0, "media": 0}

    def gbif_fetch(name: str, params: Dict[str, object]) -> Dict[str, object]:
        counters["gbif"] += 1
        return dict(GBIF_PAYLOAD)

    def summary_fetch(title: str) -> Dict[str, object]:
        counters["summary"] += 1
        return dict(SUMMARY_PAYLOAD)

    def media_fetch(title: str) -> Dict[str, object]:
        counters["media"] += 1
        return dict(MEDIA_PAYLOAD)

    gbif_client = GbifTaxaClient(fetch_func=gbif_fetch)
    wikimedia_client = WikimediaClient(
        summary_fetcher=summary_fetch,
        media_fetcher=media_fetch,
    )

    enricher = SpeciesEnricher(
        gbif_client=gbif_client,
        wikimedia_client=wikimedia_client,
    )

    result = enricher.ensure_species(
        "Aphelocoma californica",
        common_name="California Scrub-Jay",
    )

    assert result.created is False
    assert counters["gbif"] == 1  # taxonomy fetched once to confirm existing record
    assert counters["summary"] == 0  # media endpoints skipped because species already exists
    assert counters["media"] == 0

    enricher.close()
