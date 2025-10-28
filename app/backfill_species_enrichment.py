from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Dict, Iterable, Tuple

import yaml
from sqlalchemy import select

from lib.data.db import get_session
from lib.data.tables import species
from lib.setup import initialize_environment
from main import _build_species_enricher


logger = logging.getLogger("birdsong.backfill_species")


PROJECT_ROOT = Path(__file__).resolve().parent


NEEDS_REFRESH_FIELDS = (
    "species",
    "genus",
    "family",
    "ebird_code",
    "summary",
    "info_url",
    "image_url",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill species enrichment metadata for existing rows."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config.yaml",
        help="Path to the birdsong configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List species that would be refreshed without making any changes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optionally limit the number of species processed.",
    )
    return parser.parse_args()


def _load_environment(config_path: Path) -> Tuple[Dict, Dict]:
    with config_path.open("r", encoding="utf-8") as config_file:
        config_data = yaml.safe_load(config_file)
    return initialize_environment(config_data, base_dir=PROJECT_ROOT)


def _rows_needing_refresh() -> Iterable[Dict[str, object]]:
    session = get_session()
    try:
        result = session.execute(select(species))
        for row in result.mappings():
            row_dict = dict(row)
            if any(not row_dict.get(field) for field in NEEDS_REFRESH_FIELDS):
                yield row_dict
    finally:
        session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args()
    _, resources = _load_environment(args.config)
    species_enricher = _build_species_enricher(resources)

    refreshed = 0
    skipped = 0
    failed = 0

    try:
        for index, row in enumerate(_rows_needing_refresh(), start=1):
            if args.limit is not None and refreshed >= args.limit:
                skipped += 1
                continue

            sci_name = row.get("sci_name") or ""
            common_name = row.get("common_name")
            species_id = row.get("id")

            if not sci_name:
                logger.warning(
                    "Skipping species row without scientific name (id=%s)", species_id
                )
                skipped += 1
                continue

            if args.dry_run:
                logger.info("Would refresh %s (%s)", sci_name, species_id)
                refreshed += 1
                continue

            logger.info("Refreshing %s (%s)...", sci_name, species_id)
            try:
                species_enricher.ensure_species(sci_name, common_name=common_name)
                refreshed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.error(
                    "Failed to refresh %s (%s): %s", sci_name, species_id, exc
                )

    finally:
        species_enricher.close()

    logger.info(
        "Backfill complete. refreshed=%d skipped=%d failed=%d",
        refreshed,
        skipped,
        failed,
    )


if __name__ == "__main__":
    main()
