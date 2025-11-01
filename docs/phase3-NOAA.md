# Phase 3 NOAA completion 

## NOAA Weather (NWS API)
- **Purpose**: Populate `days` forecast and actual weather metrics.
- **Endpoints**:
  - `GET /points/{lat},{lon}`
  - `GET /gridpoints/{gridId}/{x},{y}/forecast`
  - `GET /stations/{stationId}/observations`
- **Authentication**:  Also set informative `User-Agent` and `Accept` headers (configure via `data_sources` headers in `config.yaml`).
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

## Scheduling & Automation
- Move the manual NOAA CLI (`python -m app.jobs.noaa_update`) into a recurring job with health checks and failure notifications.
- Add automation hooks for alert flush tasks or other background routines that should run outside the ingest loop.


# NOAA Data Integration Guide

This document outlines how to populate the `days` table using NOAA National Weather Service (NWS) resources. It covers both forecast and historical observations so daily rows can be updated incrementally.

## 1. Prerequisites
- NOAA no longer requires an API key, but the service still accepts one. If you have a token, expose it via the `NOAA_API_TOKEN` environment variable.
- Include a descriptive `User-Agent` header in all requests (e.g., `BirdSong/1.0 (contact@example.com)`); populate it in `config.yaml` under `data_sources` so `initialize_environment` can pass it to the NOAA client.
- Determine the latitude/longitude for each monitoring site (stream, microphone, or region).  
  - Coordinates are already available in the BirdSong config; reuse them to avoid drift.
- Decide on a primary timezone for daily aggregation (e.g., America/Los_Angeles). NOAA data is UTC; convert timestamps before writing local-day rows.

## 2. Forecast Data (populate `forecast_*` columns)
Forecast values should be recorded when the audio analyzer runs so the UI can show expected conditions.

### 2.1 Identify Gridpoint
```
GET https://api.weather.gov/points/{lat},{lon}
Headers: User-Agent, Accept=application/ld+json, token=<NOAA_TOKEN>
```
Response includes:
- `gridId`, `gridX`, `gridY`: use for subsequent gridpoint queries.
- `timeZone`: preferred local timezone for forecast interpretation.

Cache this mapping; grid assignments rarely change.

### 2.2 Fetch Forecast
```
GET https://api.weather.gov/gridpoints/{gridId}/{gridX},{gridY}/forecast
```
Use the periods with `detailedForecast` or break apart the `temperature` and `probabilityOfPrecipitation` values.  
Recommended mapping:
- `forecast_high`: max day temperature in Fahrenheit (convert to Celsius if desired).
- `forecast_low`: min night temperature.
- `forecast_rain`: take the highest probability-of-precipitation for the day (scale to 0–1 float).

Optional: Call `/forecast/hourly` if you need more precision for dawn/dusk calculations.

### 2.3 Compute Day Phases
NOAA gridpoint data does not provide sunrise/sunset. Use an astronomy library:
```python
from astral.sun import sun
from astral import LocationInfo
```
Inputs: latitude, longitude, timezone, date.  
Outputs: dawn, sunrise, solar noon, sunset, dusk → populate the matching columns in `days`.

## 3. Observed Data (populate `actual_*` columns)
Run a nightly job to backfill yesterday’s actual high/low and precipitation once data is available.

### 3.1 Station Selection
- From `/points/{lat},{lon}` response, follow the `observationStations` link.
- Choose the nearest station (first entry) or prefer airports/ASOS for completeness.
- Store `stationId` alongside the site metadata to reuse later.

### 3.2 Daily Observations
```
GET https://api.weather.gov/stations/{stationId}/observations
  ?start={ISO8601 UTC start}
  &end={ISO8601 UTC end}
```
Aggregate the returned hourly observations:
- `actual_high`: max `temperature.value`.
- `actual_low`: min `temperature.value`.
- `actual_rain`: sum of `precipitationLastHour.value`, converting mm→in if necessary.

If the API returns sparse data, retry after a few hours—stations sometimes publish later in the day.

## 4. Implementation Workflow
1. **Initialization**
   - Cache the mapping `{device -> (gridId, gridX, gridY, stationId, timezone)}`.
2. **Daily Forecast Job**
   - For each device/region at local midnight:
     - Call `/forecast`.
     - Compute dawn/dusk via `astral`.
     - Upsert `days` row with forecast values and day-phase times.
   - In code, use `lib.noaa.refresh_daily_forecast` followed by `store_forecast`.
3. **Observation Backfill Job**
   - At local 02:00 next day:
     - Call `/observations` for the previous day’s UTC window.
     - Aggregate high/low/precip.
     - Update the existing `days` row’s `actual_*` columns.
   - Use `lib.noaa.backfill_observations` with `store_observations` to persist the results.
4. **Automation**
   - `lib.noaa.update_daily_weather_from_config(app_config, include_actuals=True)` wraps the above calls, using the first available microphone/stream coordinates or the defaults defined in `config.yaml`.
   - A background `NoaaUpdateScheduler` now runs automatically every six hours once the FastAPI app starts, refreshing forecasts and backfilling any dates that still lack observed values.
   - The legacy CLI (`python -m app.jobs.noaa_update`) remains as a manual escape hatch but logs a deprecation warning now that automation is live.
5. **Error Handling**
   - Respect HTTP 503 retry headers (`Retry-After`).
   - Log failures with context; do not overwrite existing values with `None`.

## 5. Rate Limits & Caching
- NOAA asks for a descriptive `User-Agent` and RFC-compliant `If-Modified-Since` headers when polling.  
- Cache successful responses (ETag, Last-Modified) to reduce unnecessary calls.
- Stagger jobs across devices to avoid bursts (simple `asyncio.sleep` or cron offsets).

## Metadata & Persistence Notes
- Weather site metadata (grid ID, grid coordinates, timezone, preferred station) is cached in the `weather_sites` table so repeated updates avoid extra `/points` and `/stations` lookups.
- Daily rows now track the forecast office (`forecast_office`) and observation station details (`observation_station_id`, `observation_station_name`) for auditability.
- Missing precipitation values are normalized to `0.0` so downstream consumers never see `NULL` when stations omit a reading.

## 6. Testing Notes
- Mock the HTTP requests using recorded fixtures (e.g., `responses`, `pytest-httpx`).
- Validate:
  - Gridpoint lookup path (ensure fallback when NOAA relocates stations).
  - Forecast parsing with both day and night periods.
  - Observation aggregation with missing precipitation values.
  - Timezone conversion edge cases (DST transitions).
- Unit tests in `backend/tests/test_noaa.py` provide a reference stub that exercises the full refresh/store workflow without hitting the live service.

Following this workflow keeps the `days` table synchronized with NOAA data without overwhelming their API and ensures consistent inputs for downstream analytics and visualization.

## Open Questions
- How should we select coordinates when multiple streams/microphones exist—one canonical site, per-device rows, or an aggregated representative location?
    - A: Use default Lat & Long from Config. It is not expected at this time to deploy over such a wide area as to have a substantial impact on relevancy.
- Where should the cached gridpoint/station metadata live long term (database table, config file, or both) so it can be updated without redeploying code?
    - A: Ultimatly all config will live in the database. Develope with that in mind. The config.yaml will be a launch short-cut to operations. 
- When observations omit precipitation values, do we persist `0.0`, leave the prior value intact, or store `NULL` with a separate `data_gap` indicator column?
     - yes 0.0
- What timezone should drive “day” boundaries for NOAA updates—per-device timezone, a default from config, or always UTC?
    - Always local time zone
- Should the forecast job overwrite existing values every run, or keep the first forecast of the day unless a newer issuance timestamp is detected?
    - it can be safely overwritten throughout the day if the forecast changes. 
- Do we want to capture NOAA metadata such as issuance timestamp, station name, or data source links in `days` or `data_citations` for auditability?
    - valid idea, yes
- How frequently may the backfill job hit the observations endpoint before triggering NOAA rate-limit concerns, and is there an expected retry policy?
    - assuming you are referring with backfill the actual. It should backfill any open days when the current day is processed. No reason to have a separate timing. 
- For the CLI (`python -m app.jobs.noaa_update`), should `--include-actuals` be the default behavior, and do we need additional flags (e.g., `--site`, `--dry-run`)?
    - cli should be depricated as the automated process is fully implemented. 
