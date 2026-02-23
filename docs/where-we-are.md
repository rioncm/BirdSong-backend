# Where We Are

## Ingest & Analysis
- RTSP stream capture runs through `AudioCapture`, recording 30s clips to `streams/<stream_id>/` before passing them to BirdNET.
- BirdNET operates with the bundled v2.4 TFLite model (`models/BirdNET_v2.4_tflite`) at 48 kHz, 3 s windows, 0.5 overlap, and a 0.30 confidence floor; only the top five predictions are kept for downstream filtering.
- Detection normalization deduplicates genera per clip, retaining the highest-confidence species match and persisting results into SQLite (`data/birdsong.db`).

## Data & Enrichment
- New species trigger GBIF taxonomy lookups followed by Wikimedia summary/media fetches; successful payloads populate `species` plus `data_citations` for provenance with caching to avoid repeat calls.
- NOAA ingest (via `python -m app.jobs.noaa_update`) populates the `days` table with dawn/dusk, forecast highs/lows, and observation backfills tied to stream or microphone coordinates.
- eBird taxonomy is queried for each new species to capture the canonical `species_code`, identification summary text, and canonical info link; summaries are scraped from the `Identification` tab and cached locally.
- Core tables in SQLite include `days`, `species`, `data_citations`, `recordings`, and `idents`; recordings now persist source metadata (`source_id`, `source_name`, `source_display_name`, `source_location`) so downstream APIs no longer rely on inferred paths. Species rows store cached summary text, first/last detection timestamps, and a rolling `id_days` count of unique detection dates.

## API Surface
- FastAPI service exposes `GET /health`, `GET /detections`, `GET /detections/timeline`, `GET /detections/quarters`, `GET /species/{id}`, `GET /days/{date}`, `GET /recordings/{wav_id}`, and `POST /remote/upload`.
- Optional playback tier exposes `GET /playback/recordings/{wav_id}` with live transcode/filter support (`format=mp3|wav|ogg`, `filter=none|enhanced`) for horizontal scale-out.
- Detection feed responses now include `device_id` and `device_display_name` alongside legacy identifiers; the timeline endpoint groups detections by species within each bucket and surfaces an aggregated `detection_count` with the most recent observation details per species.
- Cached species imagery is served from the backend via `/images/{filename}`, mirroring the existing `/recordings/{wav_id}` flow.
- Contract models mirror the front-end draft, returning enriched species metadata, recording pointers, and NOAA-derived daily summaries.

## Alerts & Notifications
- `AlertEngine` evaluates configured rules (`rare_species`, `first_detection`, `first_return`) directly after detections are written and emits structured alert events.
- NotificationService scaffolding enables email (SMTP) and Telegram delivery with real-time or summary modes, drawing channel settings from `notifications.*` config blocks.
- Daily digest support is in place via summary buffers and `flush_summaries` retention settings.

## Operations & Configuration Snapshot
- File storage roots at `data/` with dedicated `audio/`, `images/`, `temp/`, and per-stream directories; microphones are stored under `microphones/`.
- Recording storage now supports S3-compatible object storage (MinIO/AWS S3). Storage and playback settings are env-driven (`BIRDSONG_S3_*`, `BIRDSONG_PLAYBACK_FORMAT`, etc.; see `backend/.env.example`).
- API can delegate recording URLs to a dedicated playback container via `BIRDSONG_PLAYBACK_SERVICE_*` settings, keeping live transcoding off the primary API pods.
- Recommended playback format is MP3 for browser compatibility; optional raw WAV copies can be retained in object storage for archival/debug.
- Streams are configured under `birdsong.streams` (e.g., `whobox`, `art-gate`, `north-side`, `south-side`) with RTSP URLs, human-friendly `display_name` labels, and location metadata; microphones inherit default lat/long with individual API keys.
- Runtime config path resolution order is: `BIRDSONG_CONFIG` env override, then `/etc/birdsong/config.yaml` (container mount, e.g. `./config.yaml:/etc/birdsong/config.yaml:ro`), then `backend/app/config.yaml`.
- The default ingest API key (`backyard-mic`) is `setophaga-coronata`; update via `config.yaml` before deployment.
- External services (NOAA, BirdNET, GBIF, Wikimedia) and their user-agent headers are centralized in `birdsong.data_sources`, keeping secrets injectable via environment variables.
- Dedicated debug logging flows to `backend/app/logs/debug.log`, and external-clients emit structured request metrics for future monitoring pipelines.
