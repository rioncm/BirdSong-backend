# Data Architecture Overview

## Core Entities
- **days**: Daily environmental context keyed by `date`. Holds forecast (anticipated) and actual weather metrics, sunrise/dusk, and season tag.
- **species**: Canonical bird taxonomy records. Populated on-demand via GBIF + Wikimedia. Includes IDs, common/scientific names, classification hierarchy, image URL, attribution, and summary text.
- **recordings**: Stored audio files that produced detections. Links detections back to the originating WAV.
- **idents**: Per-detection records derived from BirdNET analysis after genus deduplication. References `species`, `recordings`, and `days`.
- **data_sources**: Third-party providers (GBIF, Wikimedia, NOAA, etc.) configured in `config.yaml`.
- **data_citations**: Junction table mapping `species` to `data_sources` with provider-specific payloads (image metadata, summaries).

## Relationships
- `idents` → `species` (many-to-one) to reuse taxonomy + enrichment assets.
- `idents` → `days` (many-to-one) to associate detections with the environmental context when they occurred.
- `idents` → `recordings` (optional) for traceable audio playback.
- `data_citations` → `species` and → `data_sources` (many-to-many) capturing provenance.

## Data Flow Summary
1. **Ingest path**: BirdNET analysis writes/updates `recordings`, `species`, `data_citations`, and `idents` as outlined in `docs/analyze_flow.md`.
2. **Environmental path**: Scheduled NOAA jobs populate `days`. Detections link to the relevant `days` row based on local date.
3. **Configuration sync**: `initialize_environment` seeds `data_sources` from `config.yaml`, ensuring citations reference valid rows.

## Background Jobs
- **Forecast updater** (daily, early morning): Inserts/updates `days` forecast fields and dawn/dusk times.
- **Observation backfill** (daily, post-midnight): Writes actual weather values into existing `days` rows.
- **Taxonomy/media refresher** (optional weekly): Revalidates `species` enrichment (e.g., if Wikimedia content changes or new media should be preferred).

## Operational Notes
- Use migrations to add new columns rather than altering tables in-place.
- Consider read replicas or caching layers if front-end queries on `idents` become heavy.
- For analytics, materialized views or summary tables (e.g., detections per week/species) may be introduced later—capture requirements before adding them.
