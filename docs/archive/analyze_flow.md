# Analyze Pipeline Flow

## 1. Audio Intake & BirdNET Analysis
1. Recorder uploads a WAV file (from stream or microphone) into the analyzer queue.
2. `BaseAnalyzer` runs BirdNET with configured sample rate, chunk size, overlap, and confidence threshold.
3. Analyzer log receives a structured entry regardless of outcome (`id=<device> | date=<UTC> | … | detections=<count>`).

### No detections
- Detection set is empty after thresholding.
- Analyzer still records the log entry for traceability.
- Source WAV is deleted unless the capture policy forces retention (keeps storage requirements bounded).

### Detections present
- Analyzer logs detection summary.
- WAV file is retained at the destination path noted in the log for later auditing or labeling.

## 2. Detection Normalization
1. For each detection, build a deterministic species hash (e.g., normalized scientific name) to act as the primary key into the `species` table.
2. Query `species` for an existing record.
   - **If found**: reuse stored taxonomy, common name, images, etc.
   - **If missing**: trigger the enrichment pipeline (below) before persisting.

## 3. Taxonomy & Enrichment
1. Call `GbifTaxaClient.lookup(scientific_name)` to resolve taxonomy:
   - Results are cached; errors raise `TaxonNotFoundError` for clean handling.
   - The normalized payload includes canonical name, rank, family → species hierarchy, and confidence.
2. If GBIF lookup succeeds:
   - Persist taxonomy attributes into the new `species` row.
   - Use the scientific name to request supporting data from Wikimedia:
     - `GET /api/rest_v1/page/summary/{scientific_name}` → short description + thumbnail.
     - `GET /api/rest_v1/page/media/{scientific_name}` → lead image and licensing metadata.
   - Save the selected media URL(s) alongside attribution requirements.
   - Insert one or more `data_citations` rows pointing back to the relevant `data_sources` entry (e.g., GBIF, Wikimedia).
3. If taxonomy cannot be resolved:
   - Record analyzer warning and skip enrichment.
   - Optionally persist the raw detection with `is_predicted_for_location=False` so downstream review can resolve the mismatch.

## 4. Identification Deduplication & Persistence
1. Group detections in the same WAV by genus.
2. For each genus, keep the species with highest BirdNET confidence.
3. Write an `idents` record per genus winner containing:
   - Reference to the `days` row (creating one if the date is new).
   - Reference to the `species` row.
   - Confidence, start/end times, device metadata, and associated `recordings` entry (linked to the stored WAV).

## 5. Suggested Enhancements
- **Retry & cooldown**: Cache negative GBIF/Wikimedia lookups to avoid hammering APIs on repeated failures (consider exponential backoff).
- **Attribution storage**: Capture license + author data in dedicated fields to guarantee proper credit on the front end.
- **Notification hooks**: After `idents` insert, trigger downstream channels (Telegram/SMS) based on species rarity or first-in-season sightings.
- **Data quality checks**: Flag low-confidence matches or taxonomy mismatches for manual review.
- **Async pipelines**: Offload enrichment and media downloads to a background worker so the ingest path stays fast.
- **Integration tests**: See `backend/docs/integration_notes.md` for GBIF/Wikimedia stubbing patterns to validate the enrichment flow offline.

This flow keeps the analyzer loop lean while enriching successful detections with taxonomy, images, and citations the moment new species are encountered. Subsequent detections reuse the cached database entries to stay responsive. 
