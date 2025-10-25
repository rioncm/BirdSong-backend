# External Data Integration Notes

## GBIF Backbone lookups
- Use `GbifTaxaClient` from `lib/source.py` for production calls.
- For tests or local validation, inject a deterministic fetcher:

```python
from lib.source import GbifTaxaClient, build_gbif_stub

stub_payloads = {
    "Aphelocoma californica": {
        "usageKey": 2492484,
        "scientificName": "Aphelocoma californica (Vigors, 1839)",
        "canonicalName": "Aphelocoma californica",
        "rank": "SPECIES",
        "matchType": "EXACT",
        "kingdom": "Animalia",
        "phylum": "Chordata",
        "class": "Aves",
        "order": "Passeriformes",
        "family": "Corvidae",
        "genus": "Aphelocoma",
        "species": "Aphelocoma californica",
        "vernacularName": "California Scrub-Jay",
    }
}

client = GbifTaxaClient(fetch_func=build_gbif_stub(stub_payloads))
taxon = client.lookup("Aphelocoma californica")
```

- Missing names in the stub return an empty dict, triggering the usual `TaxonNotFoundError` path in the client.
- Add negative cases to ensure the analyzer handles absent taxonomy without breaking the ingest flow.

## Wikimedia summaries and media
- Plan to wrap the REST endpoints:
  - `GET https://en.wikipedia.org/api/rest_v1/page/summary/{title}` for descriptions.
  - `GET https://en.wikipedia.org/api/rest_v1/page/media/{title}` for licensing-aware images.
- During testing, stub HTTP calls using your preferred library (e.g., `httpx_mock`, `responses`) or inject deterministic fixtures via `build_wikimedia_stub` from `lib.clients.wikimedia`.
- Persist the fetched `license` + `author` alongside media URLs so the UI can display proper credit.
- Cache successful responses locally (SQLite table or file cache) with a slow expiry to stay within Wikimedia's courtesy limits.

## Validation checklist
- [ ] Successful BirdNET detection triggers a GBIF lookup (or stub) exactly once per new species.
- [ ] Enrichment gracefully skips when taxonomy or media are unavailable and logs the reason.
- [ ] Citations reference the matching `data_sources` row (GBIF, Wikimedia) for auditability.
- [ ] Integration tests cover GBIF success, GBIF miss, Wikimedia success, and Wikimedia 404 scenarios.

## NOAA weather updates
- Use `lib.clients.noaa.NoaaClient` for production; expose credentials via `NOAA_API_TOKEN` and set `NOAA_USER_AGENT` or the `data_sources` headers in `config.yaml` to identify your deployment.
- `lib.noaa.refresh_daily_forecast` and `backfill_observations` parse NOAA payloads and persist results through `store_forecast` / `store_observations`.
- `lib.noaa.update_daily_weather_from_config` wraps the refresh/backfill cycle, selecting coordinates from the first configured microphone or stream.
- When testing, create a stub client implementing `get_point`, `get_forecast`, `get_observation_stations`, and `get_observations` (see `backend/tests/test_noaa.py`).
