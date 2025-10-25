# Change Log

## 2025-10-20
- Implemented the taxonomy & media enrichment workstream:
  - Added a Wikimedia REST client with stub support and wired it into a new `SpeciesEnricher`.
  - Persist GBIF/Wikimedia enrichment results into `species` and `data_citations`, including caching-friendly helpers in `lib/data/crud.py`.
  - Trigger enrichment during audio ingest so newly detected species are immediately hydrated with taxonomy, summaries, and CC-licensed imagery.
- Seeded configuration and setup plumbing for new data sources, including Wikimedia Commons.
- Added runtime caching for species enrichment plus stub-backed tests to guard GBIF/Wikimedia integrations.
- Documented the `WIKIMEDIA_USER_AGENT` override for deployment-specific contact info.
- Built a NOAA REST client, normalization helpers, and update functions (`lib/noaa.py`) to populate the `days` table with forecast and observation data; added schema migration support for the new metadata.
- Delivered read-side CRUD helpers and FastAPI endpoints (`/detections`, `/species/{id}`, `/days/{date}`) that satisfy the front-end contract.
- Introduced unit tests for the NOAA workflow (`backend/tests/test_noaa.py`) alongside broader backend fixtures.
- Added reusable retry/backoff utilities and instrumented GBIF/Wikimedia/NOAA clients with structured success/failure logging for monitoring.
- Parsed per-provider headers from `config.yaml` so clients (Wikimedia, NOAA) now emit the configured `User-Agent` values without requiring environment overrides.
- Added `python -m app.jobs.noaa_update` CLI to run forecast/observation refreshes using config-driven credentials/user agents.
- Implemented alert evaluation rules (rare species, first detection/return) and wired FastAPI ingest to emit alert events.
- Added notification service scaffolding with email/Telegram channels, per-channel scheduling, and summary storage.
