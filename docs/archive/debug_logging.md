# Debug Logging Playbook

To unblock deep debugging, the backend now writes a dedicated diagnostic log separate from the analyzer output. Use this guide to understand the structure and toggle points.

## Log Targets
- **File:** `backend/app/logs/debug.log`
- **Logger namespace:** `birdsong.debug`
- **Format:** `YYYY-MM-DDTHH:MM:SS±HHMM | LEVEL | logger-name | message`

## Coverage
- **Capture loop (`main.py`):**
  - Stream iteration start/stop
  - FFmpeg command execution and return codes
  - Analyzer summary per recording (detection counts, duration)
  - Persistence results (rows inserted, skipped)
- **Microphone ingest (`/ears`):**
  - Request metadata (mic id, payload size)
  - Analyzer execution outcome
  - Persistence outcome and alert dispatch footprint
- **Persistence layer (`lib/persistence.py`):**
  - Day/recording upsert key values
  - Detection-level insertion decisions (created vs skipped)
  - Species enrichment fallbacks

## Usage Tips
- Tail live: `tail -f backend/app/logs/debug.log`
- Filter by module: grep the third column (logger name), e.g. `grep "capture"`.
- Pair with analyzer log to trace detector confidence vs. storage behavior.

## Extending
- Acquire loggers with `logging.getLogger("birdsong.debug.<component>")`.
- Keep per-message payloads concise—prefer IDs over full objects.
- For heavy payloads (e.g., JSON config), log once at DEBUG and refer to the timestamp.

This logging channel is intended for engineers; rotate/prune the file before shipping to production monitoring.
