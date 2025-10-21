# Implementation Plan (Next Phase)

This document aggregates the planning outputs and lays out a step-by-step roadmap for delivering the next wave of backend features.

## 1. Goals
- Wire external services (GBIF, Wikimedia, NOAA) into the ingest and enrichment pipeline without blocking audio processing.
- Expose consistent REST contract for detections, species details, and daily overviews.
- Keep the database schema aligned with new data requirements and ensure provenance via `data_sources`/`data_citations`.

## 2. Workstreams

### A. Taxonomy & Media Enrichment
1. **Create Wikimedia client module** mirroring the GBIF client (summary + media endpoints, caching, stub support).
2. **Extend species enrichment flow**:
   - On new species detection, fetch GBIF taxonomy (existing client).
   - Call Wikimedia summary and media; store `image_url`, `thumbnail_url`, `summary`, `license`, `attribution`.
   - Persist enrichment metadata and `data_citations` pointing to GBIF/Wikimedia rows.
3. **Add caching/persistence** for enrichment lookups (optional table or JSON cache).
4. **Testing**: Use GBIF/Wikimedia stubs (see `integration_notes.md`) in unit + integration tests.

### B. NOAA Days Population
1. **Implement NOAA client** per `noaa_days_data.md`:
   - Gridpoint lookup cache.
   - Forecast retrieval + dawn/dusk computation.
   - Observation backfill with aggregation.
2. **Scheduler / job runner**:
   - Add management commands or background tasks (e.g., Celery/apscheduler) for forecast/observation jobs.
   - Ensure jobs log successes/failures and respect rate limits.
3. **Update `days` table interactions**:
   - Add helper functions for upserting forecast data and patching actuals.
   - Handle timezone conversions consistently.
4. **Testing**: Replay recorded NOAA responses; cover DST edge cases and missing precipitation.

### C. API Surface
1. **Define Pydantic models** matching `frontend_contract.md` for:
   - Detection feed.
   - Species detail.
   - Daily overview.
2. **Build FastAPI routes**:
   - `GET /detections`, `GET /species/{id}`, `GET /days/{date}`.
   - Add filtering, pagination, and standardized error payloads.
3. **Integrate with DB layer**:
   - Implement queries in `lib/data/crud.py` for retrieving joined data (idents+species, days, citations).
   - Optimize with indexes if necessary (review `tables.py`).
4. **Testing**: API contract tests verifying JSON structure; snapshot tests may be useful.

### D. Infrastructure & Ops
1. **Secrets management**:
   - Load NOAA token from environment / secret store.
   - Prepare configuration hooks (e.g., `.env` support).
2. **Logging + monitoring**:
   - Standardize logging for external calls (provider, endpoint, duration).
   - Add counters/timers for success/failure to feed metrics later.
3. **Resilience patterns**:
   - Implement retry/backoff wrappers as outlined in `external_service_playbook.md`.
   - Consider a circuit-breaker flag stored in DB or memory for disabling enrichment jobs on repeated failure.

## 3. Sequence Recommendation
1. **Database prep**: Confirm `species` fields can hold Wikimedia data (add columns if required), and `data_citations` structure fits the new payloads.
2. **Implement Wikimedia client** → integrate into species enrichment → add tests.
3. **Implement NOAA client + jobs**, populate `days` table, verify via manual run.
4. **CRUD/query layer** to surface combined data.
5. **FastAPI endpoints** with contract validation.
6. **Operational polish**: logging, retries, secrets, docs updates.

## 4. Dependencies & Risks
- NOAA API variability: plan for partial data and rate limiting; jobs should gracefully skip updates when responses are missing.
- Wikimedia image licensing: ensure attribution is captured before the front end launches.
- Performance: DB queries for the detection feed may need batching/caching if volumes grow; monitor after initial deployment.

## 5. Deliverables Checklist
- [x] Wikimedia client with caching + stub support (tests pending).
- [x] Enrichment pipeline updating `species` + `data_citations`.
- [x] NOAA forecast & observation jobs populating `days`.
- [x] CRUD helpers for detections/species/daily views.
- [x] FastAPI routes delivering contract-compliant JSON.
- [x] Retry/backoff helpers shared across clients (GBIF, Wikimedia, NOAA).
- [x] Secrets loading for NOAA token.
- [x] Monitoring/logging updates for external calls (structured records + counters).
- [x] Documentation refresh (if necessary) once implementation details solidify.

Following this plan should move the backend from planning artifacts into a fully integrated system ready for front-end consumption.

## 6. Stream B Readiness
- [x] Added unit tests for `SpeciesEnricher` using deterministic GBIF/Wikimedia stubs.
- [x] Introduced in-process species caching to minimize repeat enrichment calls.
- [x] Migrated `days` table to capture forecast/actual metadata for NOAA ingest.
- [x] Documented `WIKIMEDIA_USER_AGENT` and `NOAA_API_TOKEN` environment overrides for deployment-specific contact info.

## 7. Stream C Readiness
- [x] Defined Pydantic response schemas for detections, species, and day overview endpoints.
- [x] Wired FastAPI routes to emit typed responses aligned with the front-end contract.
- [x] Added schema-focused tests ensuring the contract remains stable.

## 8. Stream D Progress
- [x] Parsed `data_sources` headers/user-agents during setup so clients reuse the configured identity strings.
- [x] Wired the enrichment pipeline to instantiate `WikimediaClient` with the config-specified user agent.
- [x] Added a NOAA update CLI (`python -m app.jobs.noaa_update`) that loads config-specified user agents and runs forecast/backfill tasks on demand.
- [ ] Implement notification channels (email, Telegram) and scheduler wiring.
- [ ] Surface metrics/counters externally (pending dashboards/alerts).
