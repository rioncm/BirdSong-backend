# Recording Storage Migration Plan

This plan migrates existing recordings from local filesystem paths to S3-compatible object storage (MinIO/AWS).

## Goal
- Preserve existing `wav_id` references used by detections.
- Move playback to browser-friendly assets (default `mp3`).
- Optionally retain raw WAV copies in object storage.

## Preconditions
1. MinIO/S3 is reachable.
2. Environment variables are set (see `backend/.env.example`).
3. Backend dependencies include `boto3`.
4. Optional: backup your SQLite DB file before migration.

## Execution Modes
- Dry run:
```bash
cd /Users/rion/VSCode/BirdSong/backend
python -m app.backfill_recordings_to_object_storage --dry-run
```

- Migrate (keep local files):
```bash
cd /Users/rion/VSCode/BirdSong/backend
python -m app.backfill_recordings_to_object_storage
```

- Migrate and delete local files after upload:
```bash
cd /Users/rion/VSCode/BirdSong/backend
python -m app.backfill_recordings_to_object_storage --delete-local
```

## Reliability Options (recommended for busy SQLite instances)
- Batch reads to avoid cursor-reset issues:
```bash
python -m app.backfill_recordings_to_object_storage --batch-size 100
```
- Retry DB updates when SQLite is locked:
```bash
python -m app.backfill_recordings_to_object_storage --max-retries 8 --retry-delay-ms 300
```
- If ingest is actively writing, pause ingest jobs during migration for fastest completion.

## What the migration updates
- Reads rows from `recordings` where `path` is local (not `s3://`).
- Uploads playback object to:
  - `s3://<bucket>/<prefix>/playback/<source_id>/<wav_id>.<format>`
- If configured (`keep_wav_copy=true` and playback format != wav), uploads WAV copy to:
  - `s3://<bucket>/<prefix>/raw/<source_id>/<wav_id>.wav`
- Updates DB `recordings.path` to the playback object `s3://...` URI.

## Rollback strategy
- Restore DB from backup.
- If local files were retained, no further action needed.
- If local files were deleted, restore from object storage raw copies.

## Verification checklist
1. `GET /recordings/{wav_id}/meta` returns `media_type` of `audio/mpeg` (or configured format).
2. `GET /recordings/{wav_id}` streams playable audio.
3. Timeline listen modal plays migrated clips.
