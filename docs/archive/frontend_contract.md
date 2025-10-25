# Front-End Data Contract Draft

Proposed JSON payloads for front-end consumption. Field names favor `snake_case` to match API models; adjust if the UI prefers camelCase.

## 1. Detection Feed (`GET /detections?date=YYYY-MM-DD`)
```json5
{
  "date": "2025-10-18",
  "summary": {
    "total_detections": 12,
    "unique_species": 7,
    "first_detection": "11:40:43",
    "last_detection": "16:12:09"
  },
  "detections": [
    {
      "id": 9821,
      "recorded_at": "2025-10-18T18:40:43Z",
      "device_name": "whobox",
      "confidence": 0.92,
      "start_time": 4.5,
      "end_time": 7.2,
      "species": {
        "id": "apca",
        "common_name": "California Scrub-Jay",
        "scientific_name": "Aphelocoma californica",
        "genus": "Aphelocoma",
        "family": "Corvidae",
        "image_url": "https://upload.wikimedia.org/...",
        "image_attribution": "© Photographer / CC BY-SA 4.0",
        "summary": "The California Scrub-Jay is a medium-sized bird..."
      },
      "recording": {
        "wav_id": "20251018_114043",
        "path": "/captures/whobox/20251018_114043.wav",
        "duration_seconds": 30.0
      },
      "location_hint": "predicted"
    }
  ]
}
```

## 2. Species Detail (`GET /species/{id}`)
```json5
{
  "id": "apca",
  "common_name": "California Scrub-Jay",
  "scientific_name": "Aphelocoma californica",
  "taxonomy": {
    "kingdom": "Animalia",
    "phylum": "Chordata",
    "class": "Aves",
    "order": "Passeriformes",
    "family": "Corvidae",
    "genus": "Aphelocoma"
  },
  "summary": "The California Scrub-Jay is a medium-sized...",
  "image": {
    "url": "https://upload.wikimedia.org/...",
    "thumbnail_url": "https://upload.wikimedia.org/.../200px-...",
    "license": "CC BY-SA 4.0",
    "attribution": "© Photographer",
    "source_url": "https://en.wikipedia.org/wiki/California_scrub_jay"
  },
  "detections": {
    "first_seen": "2024-03-11",
    "last_seen": "2025-10-18",
    "total_count": 84
  },
  "citations": [
    {
      "source_name": "Global Biodiversity Information Facility",
      "data_type": "taxa",
      "content": "...raw gbif payload...",
      "last_updated": "2025-10-18T11:42:00Z"
    },
    {
      "source_name": "Wikimedia Commons",
      "data_type": "image",
      "content": "{\"title\": \"Aphelocoma californica\", ...}",
      "last_updated": "2025-10-18T11:42:00Z"
    }
  ]
}
```

## 3. Daily Overview (`GET /days/{date}`)
```json5
{
  "date": "2025-10-18",
  "season": "autumn",
  "dawn": "06:12",
  "sunrise": "06:39",
  "solar_noon": "12:51",
  "sunset": "19:02",
  "dusk": "19:29",
  "forecast": {
    "high": 78.0,
    "low": 56.0,
    "rain_probability": 0.15,
    "issued_at": "2025-10-18T06:00:00Z",
    "source": "NOAA NWS"
  },
  "actual": {
    "high": 76.4,
    "low": 55.1,
    "rain_total": 0.0,
    "updated_at": "2025-10-19T02:15:00Z",
    "source": "NOAA NWS"
  }
}
```

## 4. API Considerations
- Paginate detection feeds (`page`, `page_size`) and allow filters (`device`, `species_id`, `min_confidence`).
- Include `etag`/`last_modified` headers for caching.
- Standardize error payloads:
```json5
{ "error": { "code": "not_found", "message": "Species not found" } }
```
- When data is pending (e.g., actuals not yet backfilled), use `null` values or include a `status: "pending"` field.

## 5. Next Steps
- Align these payloads with Pydantic models in the FastAPI layer.
- Share the draft with front-end developers to confirm field names and structures.
- Add integration tests ensuring API responses match the agreed contract.
