# Notifications Plan

This document outlines how BirdSong will deliver real-time alerts in v1 using two outbound channels: Email and Telegram. Both channels share a common publisher interface so the analyzer can trigger alerts without caring about message transport.

## Goals
- Broadcast only 
    - users configured in config.yaml on a per alert basis
    - No per user customization (on/off) only
- Notify maintainers when notable detections occur (e.g., rare species, first-of-season).
- Keep configuration minimal while allowing per-channel overrides.
- Avoid mandatory third-party accounts for consumers who only want email.
- limit emails to summeries only 
- Alerts are aggragated for summaries
- Summaries contain Alerts from a 24hour period in a reasonable format
- Alerts / Summaries are flushed every x period according to settings

```yaml
notifications:
    flush_summaries: true
    retain_period: 7 [days | weeks | months ]
```

## Architecture Overview

```
BirdNET Analyzer
     │
     ├──> NotificationService
     │        ├──> EmailNotifier (enabled if SMTP settings provided)
     │        └──> TelegramNotifier (enabled if bot token + chat IDs provided)
     │
     └──> Rules (thresholds, rarity lists) → triggers alerts
```

## Email Notifications

### Required configuration
- SMTP host/port
- SMTP username/password (or App Password if using Gmail/Outlook)
- `from_address` (alerts@domain.com)
- One or more `to_addresses`

Configuration will live in `config.yaml` under `notifications.email`:

```yaml
notifications:
  email:
    enabled: true
    real_time: false
    summary: true
    summary_schedule: 24 hour time i.e. 20:00 or 17:30 
    smtp_host: smtp.mailgun.org
    smtp_port: 587
    username: postmaster@sandbox.mailgun.org
    password: "${EMAIL_SMTP_PASSWORD}"
    from_address: alerts@birdsong.diy
    to_addresses:
      - ranger@example.com
      - curator@example.com
```

> **Secrets**: Use environment variables (`EMAIL_SMTP_PASSWORD`) loaded at runtime. We’ll map them in `initialize_environment`.

### Optional configuration
- `use_tls` / `use_ssl` flags (default true for port 587).
- Template overrides for subject/body (e.g., include location, detection details).
- Minimum confidence threshold specific to email alerts.
- Batch mode (daily summary) — out of scope for v1.

## Telegram Notifications

### Required configuration
- Bot token (from @BotFather).
- One or more chat IDs (individual or group). 

`config.yaml` snippet:

```yaml
notifications:
  telegram:
    enabled: true
    real_time: true
    summary: true
    summary_schedule: 24 hour time i.e. 20:00 or 17:30 
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    chats:
      - -1001234567890   # group chat id
      - 987654321        # individual user id
```

> Use environment variable injections for secrets at runtime.

### Optional configuration
- Custom message template per chat (e.g., one group includes photos, another simple text).
- Rate limit per chat (prevent spamming groups for common species).
- Include inline buttons linking to recordings/photos.

## Shared NotificationService

- Implement `NotificationService` with `send_detection_alert(detection: DetectionItem, extras: dict)` signature.
- On startup, load configuration → instantiate enabled notifiers.
- Provide utilities for formatting human-readable summaries (common name, confidence, location, link to WAV).
- If no channels enabled, log a warning and no-op.

## Operational Notes
- Ensure SMTP credentials and Telegram tokens are not committed; rely on `.env` or platform secrets.
- Add simple health check (e.g., `/notifications/test` endpoint or CLI) to verify delivery.
- Document fallback: if a channel errors out, log and continue; do not crash ingestion.

## Future Enhancements
- Slack/Teams webhook providers (share same NotificationService).
- SMS (Twilio) for high-priority alerts.
- User-defined subscription filters (per species, time windows).
- Digest summaries (daily/weekly).
