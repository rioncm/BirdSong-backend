# Using the iNaturalist API for Bird Images

The iNaturalist public API exposes species (taxa) data, including photos with Creative Commons licenses. You can use it to fetch representative images for BirdNET detections by querying with the species’ scientific name.

## Quick Start

1. **Lookup the taxon** by scientific or common name:
   ```
   GET https://api.inaturalist.org/v1/taxa?q=Turdus%20migratorius&per_page=1
   ```
   - `q`: search term; prefer the scientific name from BirdNET.
   - `per_page`: set to `1` if you only need the top match.

2. **Parse the response**:
   - `results[0].id`: numeric taxon ID.
   - `results[0].preferred_common_name`, `results[0].name`: common and scientific names.
   - `results[0].default_photo` or `results[0].photos`: each photo object contains:
     - `url`: base image URL (endings like `/square.jpg`). Replace size suffix (`square`, `small`, `medium`, `large`, `original`) to request different resolutions.
     - `license_code`: e.g. `cc-by-nc`.
     - `attribution`: ready-to-display photographer credit.
     - `native`: boolean indicating if the species is native to the search place (not always present).

3. **Build the image URL**:
   - Example: `url` = `https://static.inaturalist.org/photos/12345/square.jpg`
   - Derive `large`: `https://static.inaturalist.org/photos/12345/large.jpg`
   - `original` provides the full-resolution file when available.

4. **Respect licensing**:
   - Only use images with `license_code` that fits your project policies (e.g. `cc-by`, `cc-by-sa`, `cc0`).
   - Display `attribution` wherever you show the image.

## Handling No Results

- Retry using `?q=<common_name>` if the scientific name yields nothing.
- Set `?is_active=true` to avoid inactive records.
- Use `/v1/taxa/autocomplete` for faster typeahead-style lookups.
- Fallback to Wikidata/Wikimedia for species missing iNaturalist photos.

## Example Python Snippet

```python
import requests

def fetch_inaturalist_photo(scientific_name: str) -> dict | None:
    resp = requests.get(
        "https://api.inaturalist.org/v1/taxa",
        params={"q": scientific_name, "per_page": 1},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("results", [])
    if not data:
        return None

    taxon = data[0]
    photo = taxon.get("default_photo") or next(
        (p for p in taxon.get("photos", []) if p.get("license_code")), None
    )
    if not photo:
        return None

    url = photo["url"]
    large_url = url.replace("square", "large")
    return {
        "scientific_name": taxon.get("name"),
        "common_name": taxon.get("preferred_common_name"),
        "license": photo.get("license_code"),
        "attribution": photo.get("attribution"),
        "thumbnail_url": url,
        "image_url": large_url,
    }
```

## Rate Limits

iNaturalist currently allows moderate unauthenticated usage (documented at <https://api.inaturalist.org/v1/docs/>). Implement basic caching and respect HTTP status codes to stay within limits.
