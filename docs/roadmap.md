# BirdSong Roadmap

This document outlines the major feature milestones for the project. Version numbers capture meaningful backend capability changes; minor bugfixes are tracked in PR history.

## v1.0 (In Progress)
- Audio ingest API (`/ears`) with BirdNET analysis, species enrichment, and persisted detections.
- External data integrations:
  - GBIF taxonomy enrichment via `GbifTaxaClient`.
  - Wikimedia summaries/media via `SpeciesEnricher`.
  - NOAA daily forecast & observation normalization (`lib/noaa.py`).
- Data persistence (SQLite) with species, days, recordings, and idents tables.
- FastAPI read endpoints matching the documented contract:
  - `GET /detections`
  - `GET /species/{id}`
  - `GET /days/{date}`
- Config-driven third-party setup (includes per-provider headers/user-agent strings).
- Ops tooling: `python -m app.jobs.noaa_update` for on-demand forecast/backfill refreshes.
- Broadcast Notifications: Email + Telegram channels configurable via `config.yaml` (see `notifications_plan.md`).
- Tests covering enrichment, NOAA normalization, and schema responses.

## v1.1 (Observability & Scheduling)
- Scheduled NOAA forecast/backfill job integrated with the deployment’s task runner (cron/Celery/etc.).
- Metrics/exporter wiring (e.g., Prometheus counters derived from `*_request` logs) for GBIF/Wikimedia/NOAA success & latency tracking.
- Optional alerting rules for external service degradation.


## Future Considerations
- Front-end UI endpoints for additional analytics (weekly trends, species rarity scoring).
- Real-time notification stack (Telegram/SMS) based on detection thresholds.
- Circuit breaker support for repeated third-party failures with automatic re-enable.
- Users database implementation
    - primarily for user configured alerts
- Enhance Alerts module for by user customization
