# BirdNET Analyzer Output

BirdNET Analyzer writes one result row per detected vocalization to a CSV file placed next to the analyzed audio (or in the directory passed with `--results`/`--o`). Each row contains:

- **Start (s)** – start time of the segment in seconds.
- **End (s)** – end time of the segment in seconds. The difference is usually the inference window length (e.g. 3 s or 5 s).
- **Scientific name** – Latin binomial of the top species.
- **Common name** – human-readable species name.
- **Confidence** – model probability (0–1) for this detection.
- **Latitude / Longitude** – coordinates supplied on the command line, if any.
- **Week** – eBird week index derived from observation date (if provided).
- **Sensitivity / Overlap / Sample Rate** – echo the runtime parameters when they were set explicitly.
- **Time / Notes** – optional metadata columns if you annotate results.
- **File** – absolute or relative path of the analyzed audio chunk.

Other optional fields may appear when specific BirdNET flags are used; treat unknown columns as additional metadata.

## Suggested Database Mapping

Create a `recordings` table for each captured audio file and a `detections` table keyed to `recordings`. Store at least:

- `start_sec`, `end_sec`
- `scientific_name`, `common_name`
- `confidence`
- `latitude`, `longitude`, `week`
- optional metadata (sensitivity, overlap, notes, etc.), either as nullable columns or a JSON blob.

Add supporting tables—for example `cameras` for capture hardware and `species` for normalised species metadata—if you need richer relationships.
