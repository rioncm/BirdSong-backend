# Phase 1 Delivery Plan

Phase 1 spans the backend schema/API adjustments and the front-end experience refresh that were prioritized in the v1.x roadmap. This document captures detailed requirements, sequencing, and validation criteria so implementation can proceed without further clarification.

---

## Backend

### 1. Stream & Microphone `display_name`
- **Schema / Config**
  - Add `display_name` fields to stream and microphone config models, keeping `location` intact for NOAA and ops usage.
  - Update serializers and config loaders to persist `display_name` into the device registry consumed by ingest and REST layers.
  - Add a nullable `display_name` column to the relevant database tables (`streams`, `microphones`, or device metadata store) with a migration that backfills existing rows from `location` until explicit values are provided.
- **API Surface**
  - Extend detection payloads (`/detections`, `/detections/timeline`, `/detections/quarters`) to expose `display_name` and keep `location` for compatibility during the transition.
  - Update OpenAPI docs and `backend/docs/api_reference.md` (now archived) replacement notes to reflect the new field.
- **Testing**
  - Unit tests across serializers, migrations, and API responses covering: existing data without `display_name`, new configs with display overrides, and mixed scenarios.
  - Integration smoke test verifying NOAA jobs still resolve coordinates via `location` while the UI receives the new `display_name`.

### 2. Timeline Endpoint Refinement
- **Contract**
  - Continue 5-minute bucket aggregation keyed by `bucket_start`/`bucket_end`. Each bucket returns species detections grouped by canonical species id with count, highest confidence, and latest recording reference.
  - Pagination remains cursor-based (`next_cursor`, `previous_cursor`) with existing parameter names; ensure the response includes `has_more`.
- **Implementation Tasks**
  - Add aggregation helpers in the data layer to collapse ident rows per species within the bucket.
  - Surface `display_name` per device for each grouped entry.
  - Preserve performance at current volumes; add SQL explain plan notes if new joins are introduced.
- **Testing**
  - API contract tests verifying grouping rules (multiple detections in a window → single entry with aggregated metadata).
  - Load-test or benchmark against representative detection sets to confirm bucket aggregation does not regress latency.

### 3. Documentation & Ops Deliverables
- Update `where-we-are.md` and `roadmap.md` once migration ships, summarizing the schema change and timeline contract.
- Provide a short migration guide for operators (how to add `display_name` values per device after deploy).

---

## Frontend

### 1. UI System Refresh
- **Design Inputs**
  - Apply the mockup in `images/iPhoneMock.png`, style tokens in `images/style_guide.md`, and app icon set in `images/app-icons`.
  - Align the favicon set in `favicon/` with the new color palette.
- **Implementation Scope**
  - Create shared theme utilities (colors, typography, spacing) and ensure global CSS variables or Tailwind tokens match the style guide.
  - Update layout scaffolding to target mobile-first rendering, scaling gracefully up to a centered 13" viewport without introducing a separate desktop design.
  - Ensure component libraries (buttons, cards, list items) reflect the new visual system for reuse beyond the detection feed.
- **Validation**
  - Cross-browser/device testing (iOS Safari, Android Chrome, desktop Chrome/Firefox) covering typography scaling and color contrast.
  - Lint/storybook updates if applicable to capture the refreshed look for designers.

### 2. Detection Feed Enhancements
- **Data Requirements**
  - Consume `display_name`, species image URL, species summary, and placeholder info link from the updated API.
  - Display aggregated timeline entries with per-species grouping, showing the detection count and most recent timestamp inside the bucket.
  - Implement scrolling window updates so time ranges adjust as users load more buckets.
- **Interaction Details**
  - Image + summary appear inline with the detection card, using the style guide for typography and spacing.
  - Info link points to a placeholder destination for Phase 1 (update roadmap item to select a definitive source later).
  - Loading states and empty results should follow the new design language.
- **Testing**
  - Component/unit tests verifying timeline grouping rendering.
  - Playwright/cypress (if available) smoke tests to validate infinite scroll and detail toggles.

### 3. Asset & Build Outputs
- Replace legacy icons/favicons in the build pipeline with the refreshed assets.
- Confirm bundler references (React Native, Web, etc.) align with the new file locations/naming.

---

## Cross-Cutting Development Plan
1. **Backend groundwork**: implement migrations + API enhancements (`display_name`, timeline aggregation) with full tests.
2. **Front-end integration**: update data models/clients to consume the new fields, then implement the UI refresh and detection view per the design kit.
3. **QA cycle**: run end-to-end regression (manual + automated) to confirm ingest → API → UI flow. Validate NOAA and alert pipelines are unaffected.
4. **Documentation & Release**: refresh developer docs, update release notes, and ensure config guidance highlights the optional `display_name`.

---

## Resolved Clarifications
- `display_name` is additive; `location` remains for background jobs. All inputs already have `display_name` in `config.yaml`.
- Timeline endpoint keeps existing pagination and cursor behavior; 5-minute grouping remains standard, so work centers on data shaping/UX.
- External info link can remain a placeholder in Phase 1; roadmap tracks the decision for iNaturalist vs. Wikipedia vs. Cornell.
- Mobile-first design with center-aligned scaling up to 13" displays; no dedicated desktop variant required in 1.x.
- Project remains private; all work targets a single locally hosted development instance with no external users or deployments yet.

## Phase 1 Status (In Progress)
- ✅ Backend now persists `display_name` metadata in `StreamConfig`/`MicrophoneConfig`, seeds it through setup, and stores it on each recording (`source_display_name`). API payloads expose `device_id` and `device_display_name` for detections.
- ✅ `/detections/timeline` aggregates detections by species per bucket and returns `detection_count` plus the most recent observation context, aligning the contract with the updated front-end needs.
- ✅ Front-end timeline view incorporates the new design palette, typography, and aggregated species cards, consuming the updated API fields. Remaining UI polish will follow once we select the canonical “learn more” endpoint per species.

> If a Figma export can de-risk CSS/token translation, please drop it in `images/` (or share the URL) before the frontend sprint.
