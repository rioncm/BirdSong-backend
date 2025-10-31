# Version 1.1
Priority work to be completed for a 1.1 release. These items were identified in a prior code review. Completion status should be verified before starting. Documentation references for backend/docs is no longer valid. Older documents have been moved to backend/docs/archive.

## Update stream and microphone schema for new "disply_name"
**Status: Completed (v1.1)**
- Detection payloads now rely on `device_display_name`; legacy `location` hints are no longer sent to clients.
- Database schema already carried the supporting columns via prior migrations.

## Timeline buckets can violate schema when timestamps are missing  
**Status: Completed (v1.1)**
  - Buckets generated without timestamps now emit the string literal `"unspecified"` so the response model always receives `str` values as documented.
  - Cursor math still relies on actual timestamps, so legacy rows without `time` remain accessible without validation errors.

## Front-end contract’s error handling guidance unimplemented 
**Status: Completed (v1.1)** 
  - Responses now wrap all FastAPI/validation errors in `{ "error": { "code", "message", "details?" } }`.
  - Common HTTP status codes map to canonical error codes (`bad_request`, `not_found`, `validation_error`, etc.); detail payloads bubble up under `error.details`.

## New Frontend enpoint for time line 
***COMPLETED***
- Groups detections of species into one entry 
- Contains more information 
    - Image
    - Copy
    - Link to more info

## “Normalize id results to estimate count” feature never surfaced** 
**REVEIW Implement if missing** 
  - Docs: `backend/docs/features.md` lists an ID normalization/count estimation task, but there is no corresponding implementation in the ingestion or analytics codebase.

## Medium – API responses omit documented fields
**Status: Completed (v1.1)**
 - Detection payloads now surface `recording.duration_seconds` sourced from the analyzer pipeline.
 - Species previews/detail endpoints expose cached media metadata (`thumbnail_url`, `license`, `attribution`, `source_url`) pulled from citation records.
 

## Genus deduplication step described in docs is missing in code 
**HOLD and document current behavior**
  - Docs: `backend/docs/analyze_flow.md` (Section 4) and `backend/docs/features.md` (“detection cleanup… only the highest confidence is logged”) specify grouping detections by genus and persisting a single winner.  
  - Reality: `persist_analysis_results` simply iterates over every detection and inserts them (`backend/app/lib/persistence.py:49-80`), so duplicates per genus/species are written verbatim.  
  - Impact: violates the documented data contract and inflates `idents` records, which affects downstream alerts and analytics.

## Medium – WAV cleanup for empty detections not implemented
**REVEIW Implement if missing** 
  - Docs: `backend/docs/analyze_flow.md` (“Source WAV is deleted unless the capture policy forces retention”) and `backend/docs/features.md` (“discard files without matches”).  
  - Reality: both the stream loop and the `/ears` upload retain every file regardless of detection outcome (`backend/app/main.py:88-115`, `backend/app/api.py:529-618`), leading to unbounded storage growth contrary to the plan.

# BirdSong v1.x Roadmap

Focus areas below capture the remaining work needed to round out the 1.x series now that the ingest and enrichment core is stable.

## Alert & Notification Delivery
- Finish wiring the NotificationService so email and Telegram sends execute end-to-end, including per-channel enable/disable flags and retries on transient failures.
- Implement scheduled summary delivery (cron, APScheduler, or deployment-native tasks) that drains the alert buffer and respects `summary_schedule`.
- Add alert history retention/pruning so summaries and real-time messages can be audited without ballooning storage.

## Scheduling & Automation
- Move the manual NOAA CLI (`python -m app.jobs.noaa_update`) into a recurring job with health checks and failure notifications.
- Add automation hooks for alert flush tasks or other background routines that should run outside the ingest loop.

## Observability & Reliability
- Export structured metrics for external service calls (GBIF, Wikimedia, NOAA) and notification outcomes; surface via Prometheus or another collector.
- Introduce circuit-breaker logic when third-party errors exceed thresholds, taping alerts + NOAA jobs until manual or timed reset.
- Push debug and request logs into a centralized sink (ELK, Loki, etc.) to simplify production troubleshooting.

## Data & Enrichment Enhancements
- Add fallbacks for media enrichment (e.g., iNaturalist, Macaulay Library) when Wikimedia lacks usable assets, preserving license metadata.
- Extend data citations to include NOAA issuance timestamps and observation station IDs for traceability.
- Provide admin tooling to re-run enrichment for existing species when upstream data changes.

## Front-End & User Experience
- Deliver the detection feed UI: infinite scroll timeline, species cards with imagery, and recording playback links.
- Implement date/time pickers and species filters aligned with the `/detections` query parameters.
- Expose per-user alert preferences (e.g., favorite species) in preparation for account support.

## Stretch Items Under Evaluation
- Add SMS/Slack providers reusing the notification interface once email/Telegram production paths harden.
- Build weekly trend analytics (unique species, detection volume) as additional API endpoints for dashboards.
- Explore ambient-device integration (e.g., Home Assistant) powered by alert events.



# BirdSong FUTURE Roadmap

Notes for future development in no particular order

## SMS Notifications
  - Docs: `backend/docs/features.md` lists SMS alongside Telegram as supported channels.  
  - Reality: only email and Telegram channel implementations exist (`backend/app/lib/notifications/channels`), so SMS alerts are unsupported.

## Create USER concept
- Create a users concept to allow for general site users and administrators
- site users functions | launch timing
    - subscribe to alerts | with users roll out
    - social login [google*, apple, microsoft, ??]| future 
- site administrators fuctions | launch timing
    - manage input streams & microphones | with initial launch 
    - manage users [password reset, block] | with initial launch
    - manage alert channel settings | future
    - manage alert rules | future
    - maintain / update data sources | future
 

## Move configuration to database 
- For initial startup a config.yaml should be used to read all initial settings into the database. 
- Create 

## Implement Postgre and MAriaDB options for database back end

- To allow for larger deployments create 

## Add playback of detections 
- Add interface for user to playback wav files with detections. 
- play, pause, restart, download buttons
- Visual display of waveform like iNaturalist with start and stop indicators for each detection 
    - show detected species scientific name and common name with in the indicated section. 
