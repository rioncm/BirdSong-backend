

Bad + Ugly (Findings, highest severity first)
# Hold
[P0] Secrets are committed in plaintext config (config.yaml (line 24), config.yaml (line 125), config.yaml (line 137), config.yaml (line 146)).

- Currently in Alpha development. All code is local and private. Leave on list, don't address now. 

# Needs Implementation

[P1] Notification failures can abort ingest processing before persistence: alerts are published inline (api.py (line 669), api.py (line 837)), handle_alert is not isolated (service.py (line 70)), and channel sends can raise (telegram.py (line 39), email.py (line 53)).

[P1] Stream analyzer path bypasses alert/notification pipeline: capture loop persists detections only (main.py (line 153)) and never initializes/uses AlertEngine there (main.py (line 100)).

- Notifications are not implemented and code here is / should be non-functional

# Needs fixes and updates
## Approved

[P1] weather source type mismatch can break ORM reads: setup allows weather (setup.py (line 15)), config uses it (config.yaml (line 28)), but DB enum excludes it (tables.py (line 27)). I reproduced a LookupError when selecting source_type.

[P3] Timezone handling is inconsistent: API datetime formatter converts to server local timezone (api.py 
(line 139)), while NOAA default target day uses server local date (noaa.py (line 386)) rather than site timezone.

- The most common issue is a bug / break realted to weather and Noaa which causes a nightly restart via cron jod. 
- Implemented in this fork:
  - Added `weather` to SQLAlchemy enums for `data_sources.source_type` and `data_citations.data_type`.
  - Normalized API datetime formatting to UTC (`...Z`) and switched NOAA default forecast date to the site's timezone.

# Opimizations
## Approved

[P2] Upload path is memory-heavy and leaves invalid files on disk: full file read into memory (api.py (line 987)), validation happens after write (api.py (line 1021)), and invalid-file error exits without cleanup (api.py (line 1030)).

[P2] /detections does full-result loading then Python pagination (scalability risk): all rows fetched (api.py (line 1122)) then sliced in memory (api.py (line 1152)).

- Needs review in context of return values to front end and then to conform to best practices for both. 
- Implemented in this fork:
  - Upload ingest now streams chunks to disk (no full in-memory buffer), and invalid uploads are cleaned up before returning errors.
  - `/detections` now performs SQL pagination (`LIMIT/OFFSET`) and computes summary counts via SQL instead of loading all rows into memory.
  - Included `recording.duration_seconds` in `/detections` row selection to keep feed payload aligned with timeline payload.

# Detailed review needed. 

[P2] Docs and runtime contract drift: docs still reference /ears and old sync response (where-we-are.md (line 15), api_reference.md (line 7), api_reference.md (line 327), api_reference.md (line 374)) while code is /remote/upload returning async-accept (api.py (line 922), api.py (line 1061)).
