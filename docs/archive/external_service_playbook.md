# External Service Playbook

Guidelines for working with third-party APIs in the BirdSong pipeline.

## 1. GBIF Backbone (pygbif)
- **Purpose**: Resolve taxonomy details for detected species.
- **Library**: `pygbif.species.name_backbone`.
- **Authentication**: None required.
- **Rate limits**: GBIF recommends staying under ~10 requests/sec. Cache results (`GbifTaxaClient` does this) and debounce retries.
- **Timeouts**: Set a client-side timeout (3–5s); the client now retries transient failures with exponential backoff and jitter.
- **Error handling**:
  - `matchType == "NONE"` → surface `TaxonNotFoundError`.
  - Network/HTTP errors → wrap in `ThirdPartySourceError`; log details.
  - Malformed responses → log at warning level and continue without enrichment.
- **Caching**:
  - In-memory LRU (already implemented).
  - Optionally persist lookups in SQLite to share across runs.
- **Testing**: Use `build_gbif_stub` to inject deterministic responses into `GbifTaxaClient`.

## 2. Wikimedia REST API
- **Purpose**: Fetch species summaries and CC-licensed media.
- **Endpoints**:
  - `GET /api/rest_v1/page/summary/{title}`
  - `GET /api/rest_v1/page/media/{title}`
- **Authentication**: None; provide descriptive `User-Agent` (load from `config.yaml` via `initialize_environment`).
- **User-Agent override**: Set the `WIKIMEDIA_USER_AGENT` environment variable (or configure via `config.yaml` under `data_sources`) to customize the contact string per deployment.
- **Rate limits**: Courtesy limit of ~200 req/s. Stay well below (<1 req/s) via caching.
- **Timeouts**: 3–5s per call. Built-in retry/backoff covers HTTP 429/5xx responses with exponential delay and jitter.
- **Error handling**:
  - 404 → fall back to common name variant or skip enrichment.
  - Non-200 → log and mark species as `media_pending`.
- **Caching**: Store summary + media metadata locally with long expiry (weeks). Include `ETag`/`Last-Modified` headers when revalidating.
- **Attribution**: Persist `license`, `author`, and `credit` from the media payload for front-end display.
- **Testing**: Mock with local fixtures via HTTP interceptors (`responses`, `pytest-httpx`).

## 3. NOAA Weather (NWS API)
- **Purpose**: Populate `days` forecast and actual weather metrics.
- **Endpoints**:
  - `GET /points/{lat},{lon}`
  - `GET /gridpoints/{gridId}/{x},{y}/forecast`
  - `GET /stations/{stationId}/observations`
- **Authentication**: NOAA token via `token` header (optional; configure via `NOAA_API_TOKEN`). Also set informative `User-Agent` and `Accept` headers (configure via `data_sources` headers in `config.yaml`).
- **Rate limits**: Not strictly published; NOAA requests considerate usage. Cache gridpoint/station lookups; throttle forecast/observation calls (≤1 req/min per grid).
- **Timeouts**: 5–8s (API can be slow). Automatic exponential backoff with jitter handles 5xx responses.
- **Error handling**:
  - 404 on observations → station down; retry later, log warning.
  - Missing precipitation values → treat as 0 but note `data_gap` flag.
  - If all retries fail, leave existing `days` values untouched.
- **Caching**:
  - Persist gridpoint + station mapping to avoid repeated `/points` calls.
  - Cache forecast responses keyed by issuance time for difference detection.
- **Testing**: Record responses and replay. Validate DST transitions and incomplete observation sets.

## 4. Cross-cutting Concerns
- **User-Agent**: `"BirdSong/<version> (contact@example.com)"` to comply with provider policies.
- **Retry strategy**: exponential backoff (e.g., 1s, 2s, 4s) capped at 3 attempts; jitter helps avoid thundering herds.
- **Circuit breaker**: track consecutive failures; temporarily disable enrichment/forecast jobs if thresholds exceeded.
- **Logging**: Service clients emit structured `gbif_request`, `wikimedia_request`, and `noaa_request` log events with status codes and durations—pipe these into your monitoring stack alongside error logs.
- **Secrets management**: Store NOAA token (and any future API keys) in environment variables or a secret store, not in `config.yaml`.
- **Monitoring**: Add metrics for request latency, success/failure counts, and cache hit ratio.

Following these guidelines keeps integrations resilient, compliant with provider policies, and testable without hitting live services during development.
