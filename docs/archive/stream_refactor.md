# Stream Refactor Summary

BirdSong no longer treats ingest sources as "cameras". The pipeline now uses the generic `streams` concept so FFmpeg can capture audio from any real-time endpoint that emits audio—RTSP cameras, IP microphones, HTTP radio feeds, or future adapters.

## Configuration Highlights
- `birdsong.streams` replaces the old `birdsong.cameras` block in `config.yaml`.
- Each stream entry requires:
  ```yaml
  birdsong:
    streams:
      rookery:
        stream_id: rookery
        kind: rtsp              # rtsp | http | https | file
        url: rtsps://...
        record_time: 30         # seconds per capture
        output_folder: /path/to/storage
        location: Backyard
        latitude: 36.81
        longitude: -119.83
  ```
- `kind` defaults to `rtsp` when omitted. All existing RTSP camera configs only need the new `url` field and updated key names.
- Base path helpers (`base_path`, `default_latitude`, `default_longitude`) continue to work, now scoped under `streams`.

## Code Changes
- `StreamConfig` supersedes `CameraConfig`, and `BirdsongConfig.streams` replaces the `cameras` dictionary.
- `AudioCapture` accepts a `StreamConfig`, building FFmpeg commands per stream `kind`.
- Analyzer results expose `stream_id`; log summaries, API payloads, and downstream consumers now rely on that field.
- NOAA integrations look up coordinates from either microphones or streams, whichever is populated first.
- Setup helpers (`initialize_environment`) resolve stream directories and include them in the shared `device_index` as `type: "stream"`.

## Migration Notes
1. Rename the `birdsong.cameras` section to `birdsong.streams` and update each entry with `stream_id`, `kind`, and `url`.
2. Update any custom scripts or dashboards that read `camera_*` fields to the new `stream_*` counterparts.
3. Validate deployments by running a short capture loop (`python main.py --duration 60`) and ensuring analyzer logs record the expected `stream_id`.

All unit tests and NOAA backfill helpers have been updated accordingly. No compatibility shim remains for the old `cameras` terminology since the project is still pre-release.
