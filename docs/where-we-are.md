# Where We Are

## Ingest & Analysis
- RTSP stream capture runs through `AudioCapture`, recording 30s clips to `streams/<stream_id>/` before passing them to BirdNET.
- BirdNET operates with the bundled v2.4 TFLite model (`models/BirdNET_v2.4_tflite`) at 48 kHz, 3 s windows, 0.5 overlap, and a 0.30 confidence floor; only the top five predictions are kept for downstream filtering.
- Detection normalization deduplicates genera per clip, retaining the highest-confidence species match and persisting results into SQLite (`data/birdsong.db`).

## Data & Enrichment
- New species trigger GBIF taxonomy lookups followed by Wikimedia summary/media fetches; successful payloads populate `species` plus `data_citations` for provenance with caching to avoid repeat calls.
- NOAA ingest (via `python -m app.jobs.noaa_update`) populates the `days` table with dawn/dusk, forecast highs/lows, and observation backfills tied to stream or microphone coordinates.
- Core tables in SQLite include `days`, `species`, `data_citations`, `recordings`, and `idents`; recordings now persist source metadata (`source_id`, `source_name`, `source_display_name`, `source_location`) so downstream APIs no longer rely on inferred paths.

## API Surface
- FastAPI service exposes `GET /health`, `GET /detections`, `GET /detections/timeline`, `GET /detections/quarters`, `GET /species/{id}`, `GET /days/{date}`, `GET /recordings/{wav_id}`, and `POST /ears`.
- Detection feed responses now include `device_id` and `device_display_name` alongside legacy identifiers; the timeline endpoint groups detections by species within each bucket and surfaces an aggregated `detection_count` with the most recent observation details per species.
- Contract models mirror the front-end draft, returning enriched species metadata, recording pointers, and NOAA-derived daily summaries.

## Alerts & Notifications
- `AlertEngine` evaluates configured rules (`rare_species`, `first_detection`, `first_return`) directly after detections are written and emits structured alert events.
- NotificationService scaffolding enables email (SMTP) and Telegram delivery with real-time or summary modes, drawing channel settings from `notifications.*` config blocks.
- Daily digest support is in place via summary buffers and `flush_summaries` retention settings.

## Operations & Configuration Snapshot
- File storage roots at `data/` with dedicated `audio/`, `images/`, `temp/`, and per-stream directories; microphones are stored under `microphones/`.
- Streams are configured under `birdsong.streams` (e.g., `whobox`, `art-gate`, `north-side`, `south-side`) with RTSP URLs, human-friendly `display_name` labels, and location metadata; microphones inherit default lat/long with individual API keys.
- The default ingest API key (`backyard-mic`) is `setophaga-coronata`; update via `config.yaml` before deployment.
- External services (NOAA, BirdNET, GBIF, Wikimedia) and their user-agent headers are centralized in `birdsong.data_sources`, keeping secrets injectable via environment variables.
- Dedicated debug logging flows to `backend/app/logs/debug.log`, and external-clients emit structured request metrics for future monitoring pipelines.
