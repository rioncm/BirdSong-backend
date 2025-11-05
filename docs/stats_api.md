# Stats API Proposal

This document outlines a proposed set of REST endpoints that expose the identification statistics needed for dashboard-style visualisations on the BirdSong front end. The intent is to keep response payloads concise, support fast refreshes (≤10 s polling), and provide a consistent aggregation model across different widgets.

## Guiding Principles

- **Consistency:** Every stats endpoint lives under a `/stats` prefix and returns a `generated_at` ISO timestamp for cache/coherency checks.
- **Time windows:** Default to the last 24 hours (rolling) but allow callers to override with `start` / `end` ISO timestamps or a `window` shorthand (e.g. `24h`, `7d`).
- **Aggregation bins:** When an endpoint supports custom grouping (e.g. hourly vs. daily), expose a `bucket_minutes` or `granularity` parameter.
- **Pagination:** Leaderboards return a capped list by default (top 10) with optional pagination for deeper exploration.
- **Caching:** These endpoints are excellent candidates for short-lived in-memory caching (e.g. 30–60 s) because they aggregate historical data.

## Endpoint Suite

### 1. Overview Snapshot

- **Method / Path:** `GET /stats/overview`
- **Purpose:** Single request that powers “hero numbers” on the dashboard (detections, species, devices, confidence) and supplies the fixed set of “top” lists required for the landing view.
- **Query Parameters (optional):**
  - `start`, `end` — ISO-8601 timestamps (UTC).
  - `window` — convenience duration string (`1h`, `24h`, `7d`); ignored if `start`/`end` provided.
- **Notes:**
  - `top_species`, `top_hours`, and `top_streams` return fixed-length lists (5–10 items) so the dashboard does not need to issue follow-up leaderboard calls during initial load.
  - These lists act as drill-down teasers; the UI can pivot into `GET /stats/species/top` or `GET /stats/devices/activity` for the full context.
- **Response 200:**

```json
{
  "generated_at": "2024-04-08T10:15:00Z",
  "window": {
    "start": "2024-04-07T10:15:00Z",
    "end": "2024-04-08T10:15:00Z"
  },
  "detections_total": 1245,
  "unique_species": 37,
  "active_devices": 5,
  "avg_confidence": 0.82,
  "top_species": [
    {
      "species_id": "cardinalis-cardinalis",
      "common_name": "Northern Cardinal",
      "detections": 182,
      "avg_confidence": 0.89
    },
    {
      "species_id": "zenaida-macroura",
      "common_name": "Mourning Dove",
      "detections": 141,
      "avg_confidence": 0.76
    }
  ],
  "top_hours": [
    {
      "bucket_start": "2024-04-07T12:00:00Z",
      "detections": 37,
      "unique_species": 9
    },
    {
      "bucket_start": "2024-04-07T13:00:00Z",
      "detections": 41,
      "unique_species": 11
    }
  ],
  "top_streams": [
    {
      "device_id": "north-side",
      "display_name": "North Side",
      "detections": 214,
      "unique_species": 19
    },
    {
      "device_id": "river-bend",
      "display_name": "River Bend",
      "detections": 187,
      "unique_species": 16
    }
  ]
}
```


### 2. Data Comparison

- **Method / Path:** `GET /stats/data-comparison`
- **Purpose:** Provides percentage and absolute deltas for dashboard metrics without bloating the primary payloads.
- **Query Parameters:**
  - `metric` — required; one of `detections_total`, `unique_species`, `avg_confidence`, `active_devices`.
  - `start`, `end` — optional ISO-8601 timestamps (UTC) for the primary window.
  - `window` — optional duration shorthand (`1h`, `24h`, `7d`); ignored if `start`/`end` provided.
  - `comparison` — required; one of `prior_range`, `prior_month`, `prior_year`.
  - `species_id`, `device_id` — optional; scope comparison to a specific species or device when drilling down from summary widgets.
- **Response 200:**

```json
{
  "generated_at": "2024-04-08T10:15:00Z",
  "metric": "detections_total",
  "primary_window": {
    "start": "2024-04-07T10:15:00Z",
    "end": "2024-04-08T10:15:00Z",
    "value": 1245
  },
  "comparison_window": {
    "start": "2024-04-06T10:15:00Z",
    "end": "2024-04-07T10:15:00Z",
    "value": 1106,
    "selector": "prior_range"
  },
  "absolute_change": 139,
  "percent_change": 12.57
}
```


### 3. Detection Time Series

- **Method / Path:** `GET /stats/detections/series`
- **Purpose:** Supplies line/area charts showing detection counts over time.
- **Query Parameters (optional):**
  - `start`, `end` or `window`.
  - `bucket_minutes` — aggregation size (default `60`, max `1440`).
  - `group_by` — `species`, `device`, or `overall` (default `overall`).
- **Response 200:**

```json
{
  "generated_at": "2024-04-08T10:15:00Z",
  "bucket_minutes": 60,
  "series": [
    {
      "key": "overall",
      "label": "All detections",
      "points": [
        { "bucket_start": "2024-04-07T12:00:00Z", "count": 37 },
        { "bucket_start": "2024-04-07T13:00:00Z", "count": 41 }
      ]
    }
  ]
}
```

### 4. Species Leaderboard

- **Method / Path:** `GET /stats/species/top`
- **Purpose:** Ranks species by detection count or confidence; backs “Top species today” cards.
- **Query Parameters (optional):**
  - `start`, `end` or `window`.
  - `sort` — `detections` (default) or `avg_confidence`.
  - `limit` — default `10`, max `100`.
  - `offset` — for pagination (default `0`).
- **Notes:** Use `GET /stats/data-comparison` with `metric=detections_total` or `metric=avg_confidence` plus `species_id` when a trend indicator is needed alongside leaderboard entries.
- **Response 200:**

```json
{
  "generated_at": "2024-04-08T10:15:00Z",
  "results": [
    {
      "species_id": "cardinalis-cardinalis",
      "common_name": "Northern Cardinal",
      "detections": 182,
      "avg_confidence": 0.89
    }
  ],
  "total": 37
}
```

### 5. Device Performance

- **Method / Path:** `GET /stats/devices/activity`
- **Purpose:** Compares microphones/recorders to spot outages or noisy units.
- **Query Parameters (optional):**
  - `start`, `end` or `window`.
  - `bucket_minutes` — default `1440` (daily totals).
- **Response 200:**

```json
{
  "generated_at": "2024-04-08T10:15:00Z",
  "devices": [
    {
      "device_id": "north-side",
      "display_name": "North Side",
      "buckets": [
        { "bucket_start": "2024-04-02T00:00:00Z", "detections": 214, "unique_species": 19 }
      ],
      "uptime_percent": 97.5
    }
  ]
}
```

### 6. Species Trend Detail

- **Method / Path:** `GET /stats/species/{species_id}/trend`
- **Purpose:** Drives drill-down charts showing how an individual species’ detections change over time.
- **Query Parameters (optional):**
  - `start`, `end` or `window`.
  - `bucket_minutes` — default `180`.
- **Response 200:** Similar to the time series endpoint but scoped to a single `species_id`, including summary statistics (best day, per-device breakdown).

### 7. Activity Heatmap

- **Method / Path:** `GET /stats/activity/heatmap`
- **Purpose:** Supplies a day-of-week × hour-of-day matrix for heatmap visualisations.
- **Query Parameters (optional):**
  - `weeks` — how many weeks to consider (default `4`, max `12`).
  - `species_id` — optional filter.
- **Response 200:**

```json
{
  "generated_at": "2024-04-08T10:15:00Z",
  "weeks_analyzed": 4,
  "cells": [
    { "weekday": 0, "hour": 5, "detections": 42, "unique_species": 7 },
    { "weekday": 0, "hour": 6, "detections": 51, "unique_species": 6 }
  ]
}
```

### 8. Confidence Distribution

- **Method / Path:** `GET /stats/confidence/histogram`
- **Purpose:** Feeds bar charts that show how confident the model has been over a selected window.
- **Query Parameters (optional):**
  - `start`, `end` or `window`.
  - `bins` — number of equal-width bins (default `10`, max `50`).
- **Response 200:**

```json
{
  "generated_at": "2024-04-08T10:15:00Z",
  "bins": [
    { "range": [0.0, 0.1], "detections": 12 },
    { "range": [0.1, 0.2], "detections": 23 }
  ]
}
```

## Implementation Notes

- Derive all aggregates from the existing detections table; consider materialised views if volume grows.
- Use shared helper utilities for parsing `window` strings and enforcing max ranges.
- Comparison helpers should consistently calculate `prior_range` as the immediately preceding window of equal duration, while `prior_month` and `prior_year` shift the requested window by one calendar month or year respectively.
- Ensure the overview “top” lists reuse leaderboard queries under the hood to keep drill-down data aligned across endpoints.
- Ensure each endpoint is covered by both unit tests (aggregation correctness) and integration tests (FastAPI response contracts).
- Add OpenAPI schema annotations so the front end can generate TypeScript types automatically via `openapi-typescript`.


# Additional Direction
- Comparisons are centralised under `GET /stats/data-comparison`, with selectors for `prior_range`, `prior_month`, and `prior_year`; `prior_range` uses the same duration as the client-specified primary window.
- Overview responses always include the curated top lists (species, hours, streams) to avoid redundant follow-up calls during initial dashboard render.
