# Backend API Reference

Use this guide to exercise the BirdSong backend with tools such as Postman or curl. Unless otherwise noted, all endpoints respond with JSON and sit under the base URL where the FastAPI service is running (e.g. `http://localhost:8000` or `https://api.birdsong.diy`).

## Authentication

Most read endpoints are open. The `/ears` ingestion endpoint requires the `X-API-Key` header that matches the configured microphone.

---

## Health Check

- **Method / Path:** `GET /health`
- **Description:** Simple readiness probe.
- **Request Headers:** none
- **Response 200:**

```json
{
  "status": "ok",
  "timestamp": "2024-01-12T08:32:49.287Z"
}
```

---

## Detection Feed

- **Method / Path:** `GET /detections`
- **Description:** Returns paginated detections as flat records.
- **Query Parameters (optional):**
  - `date` — `YYYY-MM-DD` filter (UTC).
  - `species_id` — filter by BirdSong species identifier.
  - `min_confidence` — float `0.0`–`1.0`.
  - `page` — 1-based page index (default `1`).
  - `page_size` — items per page (default `25`, max `200`).
- **Response 200:** `DetectionFeedResponse`

```json
{
  "date": "2024-01-12",
  "summary": {
    "total_detections": 42,
    "unique_species": 7,
    "first_detection": "05:02:11",
    "last_detection": "07:18:44",
    "page": 1,
    "page_size": 25
  },
  "detections": [
    {
      "id": 123,
      "recorded_at": "2024-01-12T07:18:44Z",
      "device_name": "north-side",
      "confidence": 0.91,
      "start_time": 4.5,
      "end_time": 7.5,
      "location_hint": "North Side",
      "species": {
        "id": "junco-hyemalis",
        "common_name": "Dark-eyed Junco",
        "scientific_name": "Junco hyemalis",
        "genus": "Junco",
        "family": "Passerellidae",
        "image_url": "https://...",
        "info_url": "https://...",
        "summary": "Short AI generated summary."
      },
      "recording": {
        "wav_id": "20240112T071844Z_north-side",
        "path": "/Users/.../streams/north-side/20240112T071844Z.wav",
        "url": "http://localhost:8000/recordings/20240112T071844Z_north-side"
      }
    }
  ]
}
```

---

## Detection Timeline

- **Method / Path:** `GET /detections/timeline`
- **Description:** Buckets detections into rolling windows for infinite scroll.
- **Query Parameters (optional):**
  - `bucket_minutes` — bucket size in minutes (default `5`, max `120`).
  - `limit` — maximum buckets to return (default `24`, max `288`).
  - `before` — ISO timestamp cursor; returns buckets strictly before this moment.
  - `after` — ISO timestamp cursor; mutually exclusive with `before`.
- **Response 200:** `DetectionTimelineResponse`

```json
{
  "bucket_minutes": 5,
  "has_more": true,
  "next_cursor": "2024-01-12T05:05:00+00:00",
  "previous_cursor": "2024-01-12T07:20:00+00:00",
  "buckets": [
    {
      "bucket_start": "2024-01-12T07:15:00+00:00",
      "bucket_end": "2024-01-12T07:20:00+00:00",
      "total_detections": 3,
      "unique_species": 2,
      "detections": [
        { "...": "same fields as /detections items" }
      ]
    }
  ]
}
```

Use `next_cursor` as the `before` value to page backward.

---

## Quarter Presets

- **Method / Path:** `GET /detections/quarters`
- **Description:** Returns the four 6-hour windows for a given day, plus the “current” quarter.
- **Query Parameters (optional):**
  - `date` — `YYYY-MM-DD` (defaults to current UTC date).
- **Response 200:**

```json
{
  "date": "2024-01-12",
  "current_label": "Q3",
  "quarters": [
    { "label": "Q1", "start": "2024-01-12T00:00:00+00:00", "end": "2024-01-12T06:00:00+00:00" },
    { "label": "Q2", "start": "2024-01-12T06:00:00+00:00", "end": "2024-01-12T12:00:00+00:00" },
    { "label": "Q3", "start": "2024-01-12T12:00:00+00:00", "end": "2024-01-12T18:00:00+00:00" },
    { "label": "Q4", "start": "2024-01-12T18:00:00+00:00", "end": "2024-01-13T00:00:00+00:00" }
  ]
}
```

---

## Species Detail

- **Method / Path:** `GET /species/{species_id}`
- **Description:** Returns taxonomy, enrichment, and detection stats for a species.
- **Path Parameters:** `species_id` (string).
- **Response 200:**

```json
{
  "id": "junco-hyemalis",
  "common_name": "Dark-eyed Junco",
  "scientific_name": "Junco hyemalis",
  "taxonomy": {
    "kingdom": "Animalia",
    "phylum": "Chordata",
    "class": "Aves",
    "order": "Passeriformes",
    "family": "Passerellidae",
    "genus": "Junco"
  },
  "summary": "Generated summary...",
  "image": {
    "url": "https://...",
    "source_url": "https://..."
  },
  "detections": {
    "first_seen": "2023-11-04",
    "last_seen": "2024-01-12",
    "total_count": 112
  },
  "citations": [
    {
      "source_name": "Wikimedia Commons",
      "data_type": "image",
      "content": {
        "credit": "Author Name",
        "source_url": "https://..."
      },
      "last_updated": "2024-01-10T02:11:00+00:00"
    }
  ]
}
```

---

## Day Overview

- **Method / Path:** `GET /days/{day}`
- **Description:** Retrieves NOAA-derived forecast and observed weather for a specific date.
- **Path Parameters:** `day` — `YYYY-MM-DD`.
- **Response 200:**

```json
{
  "date": "2024-01-12",
  "season": "winter",
  "dawn": "06:14:32",
  "sunrise": "06:42:18",
  "solar_noon": "12:10:03",
  "sunset": "17:38:21",
  "dusk": "18:05:55",
  "forecast": {
    "high": 58.0,
    "low": 42.0,
    "rain_probability": 0.2,
    "issued_at": "2024-01-11T21:00:00+00:00",
    "source": "NOAA NWS"
  },
  "actual": {
    "high": 56.5,
    "low": 40.2,
    "rain_total": 0.03,
    "updated_at": "2024-01-12T23:55:00+00:00",
    "source": "NOAA NWS"
  }
}
```

---

## Recording Download

- **Method / Path:** `GET /recordings/{wav_id}`
- **Description:** Streams the WAV file associated with a detection.
- **Path Parameters:** `wav_id` — identifier from detection payloads.
- **Response 200:** Binary audio (`audio/wav`). Returns `404` if the path cannot be located on disk.

---

## Microphone Upload

- **Method / Path:** `POST /ears`
- **Description:** Receive a WAV clip from a remote microphone, authenticate, and trigger analysis/alerts.
- **Headers:**
  - `X-API-Key` — must match the configured value for the microphone.
- **Form Data (multipart/form-data):**
  - `id` or `microphone_id` — required identifier.
  - `name` — optional friendly name.
  - `latitude`, `longitude` — optional overrides.
  - `wav` — **required** file field with WAV payload.
- **Successful Response 200:** Includes detection summary from the analyzer.

```json
{
  "id": "backyard-mic",
  "recording_path": "/path/to/microphones/backyard-mic/20240112T071844Z.wav",
  "sample_rate": 48000,
  "channels": 1,
  "detections": [
    {
      "common_name": "Northern Mockingbird",
      "scientific_name": "Mimus polyglottos",
      "label": "Mockingbird",
      "confidence": 0.87,
      "start_time": 2.4,
      "end_time": 3.9,
      "location_hint": "Backyard"
    }
  ]
}
```

**Error Responses:**

- `400` — missing fields or empty file.
- `401` — bad API key.
- `404` — unknown microphone id.

---

## Curl Quickstart

Fetch detections (first page):

```sh
curl "http://localhost:8000/detections?page=1&page_size=10"
```

Fetch timeline buckets:

```sh
curl "http://localhost:8000/detections/timeline?bucket_minutes=5&limit=12"
```

Upload a sample recording (requires valid mic id + key):

```sh
curl -X POST "http://localhost:8000/ears" \
  -H "X-API-Key: setophaga-coronata" \
  -F "id=backyard-mic" \
  -F "wav=@/tmp/sample.wav"
```

---

Keep this reference alongside Postman; adjust the base URL or API key to match your deployment.*** End Patch
