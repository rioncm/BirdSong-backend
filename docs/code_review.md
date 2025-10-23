# Backend Code Review – Documentation vs Implementation

## Findings

- **High – Genus deduplication step described in docs is missing in code**  
  - Docs: `backend/docs/analyze_flow.md` (Section 4) and `backend/docs/features.md` (“detection cleanup… only the highest confidence is logged”) specify grouping detections by genus and persisting a single winner.  
  - Reality: `persist_analysis_results` simply iterates over every detection and inserts them (`backend/app/lib/persistence.py:49-80`), so duplicates per genus/species are written verbatim.  
  - Impact: violates the documented data contract and inflates `idents` records, which affects downstream alerts and analytics.

- **High – Stream capture loop never invokes alerting despite documentation**  
  - Docs: `backend/docs/features.md` (“notify all first detections”, “notify on configured long interval”) and `backend/docs/notifications_plan.md` describe analyzer-triggered alerts for every ingest path.  
  - Reality: `run_capture_loop` stores detections but never calls `AlertEngine` or `NotificationService` (`backend/app/main.py:76-115`). Only the `/ears` endpoint fires alerts (`backend/app/api.py:600-614`), so automated stream captures will never send notifications.

- **Medium – WAV cleanup for empty detections not implemented**  
  - Docs: `backend/docs/analyze_flow.md` (“Source WAV is deleted unless the capture policy forces retention”) and `backend/docs/features.md` (“discard files without matches”).  
  - Reality: both the stream loop and the `/ears` upload retain every file regardless of detection outcome (`backend/app/main.py:88-115`, `backend/app/api.py:529-618`), leading to unbounded storage growth contrary to the plan.

- **Medium – NotificationService API diverges from documented contract**  
  - Docs: `backend/docs/notifications_plan.md` calls for a `NotificationService.send_detection_alert(detection, extras)` entry point.  
  - Reality: the concrete API exposes `handle_alert(self, event)` (`backend/app/lib/notifications/service.py:16-74`). Any integration written against the documented signature will fail without adapter code.

- **Medium – API responses omit documented fields**  
  - Docs: `backend/docs/frontend_contract.md` expects `recording.duration_seconds` and enriched image metadata (`thumbnail_url`, `license`, `attribution`).  
  - Reality: `_build_detection_item` never sets `duration_seconds` (`backend/app/api.py:284-311`), and `get_species_detail` returns a `SpeciesImage` with only `url`/`source_url` (`backend/app/api.py:1006-1044`), so the frontend cannot display the promised metadata.

- **Medium – Documented SMS notification channel absent**  
  - Docs: `backend/docs/features.md` lists SMS alongside Telegram as supported channels.  
  - Reality: only email and Telegram channel implementations exist (`backend/app/lib/notifications/channels`), so SMS alerts are unsupported.

- **Medium – Timeline buckets can violate schema when timestamps are missing**  
  - Docs: `backend/docs/api_reference.md` (timeline section) model `bucket_start`/`bucket_end` as strings.  
  - Reality: `_group_detections_into_buckets` emits `None` for detections without timestamps (`backend/app/api.py:341-351`), but the Pydantic schema requires `str`, causing FastAPI validation errors if legacy rows lack `time`.

- **Low – Front-end contract’s error handling guidance unimplemented**  
  - Docs: `backend/docs/frontend_contract.md` recommends standardized `{ "error": { ... } }` payloads.  
  - Reality: endpoints rely on FastAPI default exceptions with no wrapper, so the documented error shape is not honored.

- **Low – “Normalize id results to estimate count” feature never surfaced**  
  - Docs: `backend/docs/features.md` lists an ID normalization/count estimation task, but there is no corresponding implementation in the ingestion or analytics codebase.
