# Settings and Configuration

BirdSong currently relies on a single `config.yaml` to define everything from NOAA credentials to alert routing. Phase 4 moves editable settings into the database, adds an admin API/UI for live management, and leaves only bootstrap secrets on disk. The sections below outline the schema, API, bootstrap plan, frontend, and migration.

## 1. Runtime Settings Schema

Goal: persist all values that may change after installation (thresholds, feature flags, source credentials, coordinates) in normalized tables.

### 1.1 Core tables
- `settings_categories`
  - `category_id` (PK), `name`, `description`
  - Seeds for `system`, `alerts`, `data_sources`, `streams`, `microphones`, `notifications`
- `settings_keys`
  - `setting_id` (PK), `category_id` (FK), `key`, `data_type` (`string|int|float|bool|json|secret`), `default_value`, `constraints` (JSON schema), `editable` flag
  - Represents canonical setting definitions (e.g., `alerts.rare_species.enabled`)
- `settings_values`
  - `setting_id` (FK), `scope` (`global|stream|microphone|integration`), `scope_ref` (nullable), `value`, `updated_at`, `updated_by`
  - Supports overrides per entity (e.g., per stream thresholds) and audit trail
- `data_source_credentials`
  - `source_name`, `api_key` (encrypted), `headers` (JSON), `expires_at`
  - Kept separate to apply encryption-at-rest and rotation policies
- `bootstrap_state`
  - Tracks one-off items (`admin_initialized`, `default_stream_id`, etc.) for first-run workflows

### 1.2 Supporting constructs
- Constraints enforced via JSON schema stored on `settings_keys`
- Secrets stored encrypted using application key + envelope (reuse existing secret mgmt library if present)
- Use SQLAlchemy models mirroring tables; add `SettingsRepository` abstraction to share validation logic between API and CLI

## 2. Admin Settings API

Expose read/write endpoints under `/admin/settings` (FastAPI router with dependency on admin auth).

| Endpoint | Purpose |
| --- | --- |
| `GET /admin/settings` | List categories with current values + metadata |
| `GET /admin/settings/{key}` | Detailed definition, effective value, scope overrides |
| `PUT /admin/settings/{key}` | Update global value; validate against schema/data type |
| `PUT /admin/settings/{key}/scopes/{scope}/{scope_ref}` | Create or update scoped override |
| `DELETE /admin/settings/{key}/scopes/{scope}/{scope_ref}` | Remove override (falls back to global) |
| `POST /admin/data-sources/{source_name}/credentials` | Rotate API keys/headers |
| `GET /admin/bootstrap/state` | Report which bootstrap steps remain |

Implementation details:
- Use Pydantic models mirroring schema, ensuring values cast to native types before persistence.
- All mutations log to `settings_audit` (new table) capturing user, request ID, previous value.
- Include optimistic locking via `updated_at` or `version` column to avoid clobbers.

## 3. Bootstrap & First-Run Configuration

The only required artifacts before first startup:
1. Minimal `config.yaml` (or `.env`) containing:
   - Database connection URI
   - Encryption key for settings secrets
   - Initial admin user (email + temporary password hash or token)
   - Optional NOAA/eBird API tokens for immediate use
2. Environment variables override for sensitive fields (`BIRDSONG_DB_URL`, `BIRDSONG_SECRET_KEY`, `BIRDSONG_ADMIN_TOKEN`) to support container deployments.

Startup flow:
1. `initialize_environment` loads bootstrap config -> ensures DB migrations run.
2. If `bootstrap_state.admin_initialized` is false:
   - Create admin user with forced password reset
   - Seed `settings_categories/keys` from YAML manifest
3. Admin signs in via `/admin/login`, completes password reset + MFA (if enabled), then uses settings UI/API to finish setup (e.g., upload NOAA keys, set default coordinates).

Authorization model:
- Reuse existing authn (JWT) but add `role` claims (`admin`, `operator`, `viewer`).
- Admin API requires `role == admin`.
- Provide script `python -m app.manage bootstrap-admin --email ...` for headless installs.

## 4. Frontend Components

Add an “Admin → Settings” area within the React SPA:
- `SettingsLayout` route guard (checks JWT role; redirects unauthorized users)
- `SettingsOverviewPage`
  - Lists categories with summary + “Configured/Incomplete” badges (driven by API)
- `SettingsCategoryPanel`
  - Accordion or tabs per category; renders fields using metadata (type, description, constraints)
- `SettingField` components
  - Types: text, number, toggle, JSON editor, credential upload
  - Support scoped overrides via modal (select stream/microphone)
- `AuditDrawer`
  - Displays last N changes for the selected setting
- `BootstrapChecklist`
  - Shown until all required settings are populated; links to relevant panels

UX considerations:
- Inline validation + optimistic updates with revert option
- Mask secrets by default with “Reveal” toggles requiring confirmation
- Use existing design system components (Mantine/Tailwind) for consistency

## 5. Migration Plan

1. **Schema initialization**
   - Add Alembic migration creating `settings_*`, `data_source_credentials`, `bootstrap_state`, `settings_audit`
   - Seed `settings_keys` via migration script reading manifest (YAML/JSON)
2. **Repository layer**
   - Introduce `settings.py` module handling get/set/validate via SQLAlchemy
   - Update services (alerts, NOAA, notifications) to consume repository instead of reading `config.yaml`
3. **Dual-read phase**
   - For each subsystem, read from DB if value present else fallback to legacy config
   - Add background task to sync config file values into DB (one-time import command)
4. **Admin API/UI rollout**
   - Deploy backend endpoints + frontend screens
   - Enable feature flag for authorized testers; monitor audit log + error metrics
5. **Cutover**
   - Once all settings exist in DB, mark `config.yaml` values deprecated
   - Update documentation instructing operators to use admin UI for changes
   - Lock down config file so only bootstrap secrets remain; optionally validate on startup
6. **Cleanup**
   - Remove fallback-to-file logic after confirming no regressions
   - Provide migration guide for existing deployments (export script + how-to)

Open questions:
- Do we need versioned settings snapshots for rollback? (Could add `settings_history`)
    - A: Not at this time. 
- Should certain high-frequency settings use caching layer (Redis) to limit DB hits? (Likely yes; add simple in-process cache with invalidation on change events.)
    - A: Yes—see "Redis Settings Cache" appendix.


This plan moves BirdSong from a static file to a managed, auditable configuration system that operators can adjust in real time without redeploying. Let me know if you’d like deeper schema diagrams or endpoint contracts.

## Appendix: User & Auth Foundation

Although full end-user accounts are a future phase, the admin/config work needs a minimal user concept so settings can be protected properly.

### A. Objectives
- Introduce core `User` entity with roles (currently `admin`, `user`).
- Provide authentication flow compatible with future social login or email/password.
- Keep the public site anonymous by default; only gated areas (settings, personal prefs) require auth.

### B. Data model
- `users` table:
  - `id` (UUID), `email`, `role`, `password_hash` (nullable if using social-only), `created_at`, `last_login_at`, profile JSON for future preferences.
- `social_accounts` table (future use):
  - `user_id`, `provider`, `provider_user_id`, `access_token`, `refresh_token`, `expires_at`.
- `user_preferences`:
  - key/value rows for personal viewing options, notification targets, etc.

### C. Auth flow
1. **Bootstrap admin**
   - During first run, create an admin user with temporary password or invite token.
   - Admin must complete password reset + optional MFA before accessing `/admin`.
2. **Email/password users**
   - Basic registration disabled until the user-facing experience is ready; only admins can create user records initially.
   - Implement standard login endpoint returning JWT with `sub` + `role`.
3. **Social login (future-ready)**
   - Design the auth service to support OAuth providers by plugging into the same user table.
   - Until providers are configured, keep the social-login routes hidden or behind a feature flag.

### D. Authorization strategy
- `role == admin`: full access (settings API/UI, data management).
- `role == user`: limited to personal preferences API; cannot read/write global settings.
- Anonymous visitors:
  - Can view timeline and set temporary client-side preferences (stored in local storage).
  - Server rejects any write operations without auth.

### E. Implementation sequence
1. Add auth tables + repositories (can be part of the same migration bundle as settings schema).
2. Build simple auth service issuing JWTs, reusing FastAPI dependencies for role enforcement.
3. Update admin API routes to require admin role.
4. Expose a placeholder “profile/preferences” endpoint for future user features; for now, it can simply echo stored values.
5. When ready, extend the frontend:
   - Add login modal with email/password.
   - Guard admin settings routes behind auth.
   - Provide optional “Sign in” entry point for users wanting persistent preferences.

By laying down this minimal user/auth foundation alongside the settings refactor, the admin workflow remains secure now and can expand naturally as full user capabilities come online.

## Appendix: Redis Settings Cache

High-frequency settings (API headers, alert thresholds, webhook URLs) should be served from a shared cache backed by Redis, with the database remaining the source of truth.

### A. Cache layout & refresh
- Keys: `settings:{key}` for globals, `settings:{key}:scope:{type}:{id}` for overrides.
- Background refresher task (every 60s or configurable) pulls the latest `settings_values` and rewrites Redis. Runs at startup to prime cache.
- Each Redis entry can optionally have a TTL (e.g., 300s) as a safety net in case the refresher stalls.

### B. Write path
1. Admin API validates and writes to DB.
2. Attempt to update Redis immediately; on failure, log and rely on the background refresher to catch up.
3. Expose `POST /admin/settings/cache/clear` so an admin can force a full refresh (endpoint deletes the `settings:*` keys or bumps a cache version).

### C. Read path
- Services call `SettingsService.get(...)` which:
  - Hits Redis first.
  - On miss, reads from DB, stores into Redis, returns value.
- Optionally wrap with an in-process LRU cache for per-request hot paths, but Redis is the shared authority for runtime reads.

### D. Failure handling
- Redis down? Log warning, fall back to DB + small per-process cache to avoid hammering the database; retry connecting to Redis asynchronously.
- Background refresher ensures eventual consistency even if immediate cache writes fail.
- Manual “clear cache” action provides operational escape hatch for edge cases.

This keeps read latency low, avoids per-process caches and complex pub/sub invalidation, and gives operators a straightforward way to refresh settings when needed.

## change_log.md (tracking)

| Date | Author | Summary |
| --- | --- | --- |
| 2025-03-17 | Codex Agent | Added Phase 4 configuration proposal covering DB schema, admin API, bootstrap flow, frontend, migration, user/auth foundation, and Redis cache plan. |
| 2025-03-17 | Codex Agent | Kicked off Chunk 1 (Foundation & Schema): documented deliverables and plan for migrations, repositories, and auth scaffolding. |
| 2025-03-17 | Codex Agent | Planned Chunk 2 (Settings Service & Redis Cache): outlined deliverables, steps, dependencies. |
| 2025-03-17 | Codex Agent | Planned Chunk 3 (Admin API): listed endpoints, schemas, audit hooks, and dependencies. |
| 2025-03-17 | Codex Agent | Planned Chunk 4 (Frontend Admin Console): specified UI components, flows, and dependencies. |
| 2025-03-17 | Codex Agent | Planned Chunk 5 (Migration & Cleanup): described importer, feature flag strategy, docs/monitoring updates. |
| 2025-03-17 | Codex Agent | Implemented Chunk 1 schema/auth scaffolding (new tables, migration 0008, settings manifest/loader, repository, password & JWT helpers, `app/manage.py bootstrap-admin`). |
| 2025-03-17 | Codex Agent | Began Chunk 2: added Redis-backed settings cache/service, manifest-driven redis config, NOAA integration, and cache refresher wiring. |
| 2025-03-17 | Codex Agent | Began Chunk 3: scaffolded admin settings router + schemas, cache-clear endpoint, and placeholder admin dependency. |
| 2025-03-17 | Codex Agent | Expanded Chunk 3: list/get/update/delete scoped settings, wired responses to metadata, and added settings audit logging. |
| 2025-03-17 | Codex Agent | Added Chunk 3 endpoints for definitions, bootstrap state, and data-source credentials; SettingsService now exposes these helpers. |
| 2025-03-17 | Codex Agent | Refined Chunk 4 & 5 plans (frontend admin console scope, migration/import strategy, feature flags). |
| 2025-03-17 | Codex Agent | Began Chunk 4 implementation: added admin auth context, login form, timeline/admin toggle, and basic settings UI wired to new APIs. |
| 2025-03-17 | Codex Agent | Expanded Chunk 4: scoped override UI, audit placeholder, enhanced Tailwind palette, and improved credential controls. |

## Phase 4 Implementation Chunks

1. **Foundation & Schema**
   - Add Alembic migrations for settings tables, auth tables, Redis cache metadata.
   - Seed `settings_keys` manifest; introduce core SQLAlchemy models and repositories.
   - Implement minimal user/auth services (admin bootstrap + JWT).
2. **Settings Service & Redis Cache**
   - Build `SettingsService` that reads/writes DB + Redis.
   - Implement background refresher, manual “clear cache” endpoint, and Redis failure handling.
   - Update existing backend code paths (e.g., NOAA, alerts) to consume the service (dual-read with config file fallback).
3. **Admin API**
   - Expose `/admin/settings` CRUD endpoints, credential management, cache controls.
   - Add auditing, optimistic locking, scoped overrides.
   - Secure with admin-auth middleware.
4. **Frontend Admin Console**
   - Create admin login UI, settings overview, category panels, field components, audit drawer, cache-clear action.
   - Integrate with new API endpoints; add role-based route guards.
5. **Migration & Cleanup**
   - Provide CLI/import tool to move legacy `config.yaml` values into DB.
   - Disable file-based settings writes, keep read fallback during verification, then remove.
   - Update docs/runbooks, monitor deployment, finalize change log entries.

### Chunk 1 – Foundation & Schema (In progress)

_Status (2025-03-17)_: New settings/auth tables and migration (`0008_settings_schema`), manifest loader (`settings_manifest.yaml`), `lib.settings` models/repository, password/JWT helpers, and `python -m app.manage bootstrap-admin` CLI scaffolding are in place. Remaining work: extend repositories with richer queries and wire admin auth middleware once API routes exist.

Deliverables:
- Alembic migration scripts creating:
  - `settings_categories`, `settings_keys`, `settings_values`, `settings_audit`
  - `data_source_credentials`, `bootstrap_state`
  - `users`, `social_accounts`, `user_preferences`
- Seed manifest loader (`backend/app/setup/settings_manifest.py`) invoked during migration/boot to populate categories/keys from YAML.
- SQLAlchemy ORM models + repositories (`lib/settings/models.py`, `lib/settings/repository.py`).
- Auth scaffolding:
  - Password hashing helpers, JWT issuer/validator.
  - Admin bootstrap CLI (`python -m app.manage bootstrap-admin`).
- Documentation updates describing schema fields + ERD snippet.

Plan:
1. Draft migration scripts offline (ensure reversible).
2. Add manifest loader + seed data.
3. Implement repositories + auth skeleton (no routes yet).
4. Update change log, hand off to Chunk 2 once migrations reviewed.

### Chunk 2 – Settings Service & Redis Cache (In progress)

_Status (2025-03-17)_: Redis dependency added with configurable URL/TTL, `SettingsCache` + `SettingsService` modules created (with serialization, scoped lookups, manual clear), manifest defaults wired through `initialize_environment`, resources now expose the service/cache, NOAA user-agent resolution consumes settings (with file fallback), and the API starts a background cache refresher thread when Redis is enabled. Remaining work for this chunk: wire additional subsystems (alerts, notifications) to `SettingsService` and add manual cache-clear endpoint (coming in Chunk 3).

Deliverables:
- `lib/settings/service.py` with `SettingsService` exposing `get`, `set`, `list`, `clear_cache`.
- Redis integration module (`lib/settings/cache.py`) handling key construction, serialization, error handling.
- Background refresher (async task or thread) triggered from API startup (configurable interval).
- FastAPI dependency `get_settings_service()` for injection into clients (NOAA, alerts, notifications).
- Test coverage for cache hit/miss, fallback to DB, Redis failure path.

Implementation steps:
1. Define cache key schema, serialization format, TTL defaults.
2. Implement Redis client wrapper (reuse existing redis-py or add dependency) with connection pooling.
3. Build service methods:
   - Reads → Redis → DB fallback (write-through to cache)
   - Writes → DB + cache update
   - Scoped overrides resolution (global + specific)
4. Implement background refresher + manual clear endpoint stub (actual endpoint shipped in Chunk 3).
5. Update at least one subsystem (e.g., NOAA) to use `SettingsService` behind feature flag, ensuring dual-read with `config.yaml`.

Dependencies:
- Requires Chunk 1 migrations + repositories.
- Needs Redis configuration entry (host/port/password) in bootstrap config (now parsed via `birdsong.redis`).

### Chunk 3 – Admin API (In progress)

_Status (2025-03-17)_: Admin router now lists settings and definitions, supports scoped CRUD, exposes bootstrap state, and allows updating data-source credentials. SettingsService records audits, and responses include metadata. Remaining work: credentials rotation history, data-source validation, and replacing the stub auth with real JWT.

Deliverables (remaining):
- Expand router to cover listing, scoped overrides, data-source credential rotation, and bootstrap-state inspection.
- Add validation/error handling tied to `settings_keys` metadata.
- Write integration tests covering success/failure paths and cache interactions.
- Integrate with `settings_audit` for change tracking.

Dependencies:
- Requires Chunk 2 service in place (done).
- Needs auth middleware from Chunk 1 (stubbed now, replace later).

### Chunk 4 – Frontend Admin Console (In progress)

_Status (2025-03-17)_: First iteration shipped with an inline admin view (toggleable from the main shell), stub login form accepting bearer tokens, React Query-powered settings list, cache controls, and credential rotation UI. Recent tweaks added Tailwind signal palette, category sections, and an audit placeholder. Upcoming work: dedicated routing/layout, polished design system, scoped override modal, audit drawer, and real auth integration once backend tokens are available.

Scope:
- **Auth experience**: add login modal/page that exchanges credentials for JWT, persists in local storage, and exposes a global `useAuth` hook. Until social login lands, email/password or admin token entry is acceptable.
- **Routing/guards**: create `AdminLayout` and `AdminRoute` wrappers. Top-level nav exposes an “Admin” entry that redirects unauthenticated users to the login flow.
- **Data fetching**: extend `frontend/src/api.ts` with `getSettings`, `updateSetting`, `clearCache`, `getBootstrapState`, `updateCredentials`, etc. Centralize fetch logic with error interception (401 → logout).
- **UI components**:
  - Overview dashboard summarizing completion status per category (based on metadata + bootstrap state).
  - Category panels that render fields dynamically from definitions (text, number, toggle, JSON editor, secret reveal).
  - Scoped override dialog allowing selection of scope + reference, reusing existing components.
  - Audit/history drawer (initially stubbed with placeholder data until backend exposes listing endpoint).
  - Cache-clear & credential-rotation controls surfaced via danger-zone cards.
- **State management**: `useSettings` hook caches responses, exposes optimistic mutations with automatic cache invalidation.
- **Testing**: add smoke tests (Cypress/Playwright) covering login, list, edit, cache clear, credential update.

Open questions:
- Visual design system (Material UI vs. Tailwind components) and brand palette.
- How to surface audit history before backend exposes paginated logs (stub vs. wait).
- Accessibility requirements (keyboard nav, screen reader text) for admin UI.

Dependencies:
- Chunk 3 endpoints (especially audit list) finalized.
- Auth backend (JWT issuance/validation) ready or stubbed with an admin token for local use.

### Chunk 5 – Migration & Cleanup (Planned)

Deliverables:
- **Importer CLI** (`python -m app.manage migrate-config`): reads legacy `config.yaml`, maps known keys to `settings_keys`, writes values, reports unmapped entries.
- **Verification report**: script/endpoint that compares DB values vs. legacy config to highlight gaps before cutover.
- **Runtime feature flag**: env var (e.g., `BIRDSONG_SETTINGS_MODE=legacy|db|hybrid`) controlling whether services read from config.yaml, DB, or both.
- **Health/monitoring**: metrics/logging for cache failures, missing settings, or migration drift; alert if importer encounters unknown keys.
- **Docs/runbooks**: operator guide covering migration steps, rollback, and new admin workflow.
- **Cleanup**: once DB mode stable, strip config-file reads, remove redundant manifest fields, and ensure tests target the DB path.

Implementation steps:
1. Build importer + verification command.
2. Introduce settings mode flag and update consumers (alerts, NOAA, etc.) to respect it.
3. Instrument health checks, log anomalies, and add dashboards/alerts.
4. Schedule migration window; run importer, verify, switch flag to DB mode.
5. Remove legacy code paths, update docs/tests, archive old config examples.

Dependencies:
- Chunks 1–4 complete.
- Operator approval for migration timeline/rollback.
