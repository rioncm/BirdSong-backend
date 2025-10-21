# NOAA Data Integration Guide

This document outlines how to populate the `days` table using NOAA National Weather Service (NWS) resources. It covers both forecast and historical observations so daily rows can be updated incrementally.

## 1. Prerequisites
- NOAA no longer requires an API key, but the service still accepts one. If you have a token, expose it via the `NOAA_API_TOKEN` environment variable.
- Include a descriptive `User-Agent` header in all requests (e.g., `BirdSong/1.0 (contact@example.com)`); populate it in `config.yaml` under `data_sources` so `initialize_environment` can pass it to the NOAA client.
- Determine the latitude/longitude for each monitoring site (camera, microphone, or region).  
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
4. **Configuration Helper**
   - `lib.noaa.update_daily_weather_from_config(app_config, include_actuals=True)` wraps the above calls, using the first available microphone/camera coordinates.
   - Run from the CLI with `python -m app.jobs.noaa_update --include-actuals` (optionally pass `--date YYYY-MM-DD`). The job automatically applies the `User-Agent` strings defined in `config.yaml`.
5. **Error Handling**
   - Respect HTTP 503 retry headers (`Retry-After`).
   - Log failures with context; do not overwrite existing values with `None`.

## 5. Rate Limits & Caching
- NOAA asks for a descriptive `User-Agent` and RFC-compliant `If-Modified-Since` headers when polling.  
- Cache successful responses (ETag, Last-Modified) to reduce unnecessary calls.
- Stagger jobs across devices to avoid bursts (simple `asyncio.sleep` or cron offsets).

## 6. Testing Notes
- Mock the HTTP requests using recorded fixtures (e.g., `responses`, `pytest-httpx`).
- Validate:
  - Gridpoint lookup path (ensure fallback when NOAA relocates stations).
  - Forecast parsing with both day and night periods.
  - Observation aggregation with missing precipitation values.
  - Timezone conversion edge cases (DST transitions).
- Unit tests in `backend/tests/test_noaa.py` provide a reference stub that exercises the full refresh/store workflow without hitting the live service.

Following this workflow keeps the `days` table synchronized with NOAA data without overwhelming their API and ensures consistent inputs for downstream analytics and visualization.
