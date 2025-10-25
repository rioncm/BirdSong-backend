# Alerts Pipeline Plan

This document defines how BirdSong decides *when* to raise alert events. The notifications plan (email/Telegram) describes *how* those events are delivered once emitted.

## Goals
- Provide a simple, deterministic alerting pipeline for v1.
- Keep alert logic distinct from notification delivery.
- Alerts are broadcast events; subscribers (notification channels, summaries) consume them independently.
- Notification frequency and fan-out are handled entirely inside the notification module.
- Keep detection rules straightforward; no per-user preferences in v1.
- Author alert rules via YAML configuration.

## Terminology
- **Detection**: Individual BirdNET result (species, confidence, timestamps).
- **Alert Event**: Structured payload describing why we’re notifying (type, summary, detection refs).
- **AlertRule**: Condition that converts detections into alert events.
- **EventPublisher**: Internal bus that forwards events to the notification layer.

## High-Level Flow

```
BirdNET Analyzer emits Detection ➜ AlertEngine.process_detection()
     │
     └── evaluate rule YAML ➜ emit AlertEvent(s)

Notification module subscribes to AlertEvent stream
     ├── Immediate broadcasts (real-time channels)
     └── Daily summary builder (notification concern)
```

## Core Components

### Package Layout

```
alerts/
  __init__.py
  engine.py           # AlertEngine: orchestrates rule evaluation and publishing
  models.py           # AlertEvent, AlertContext dataclasses / enums
  rules/
    __init__.py
    base.py           # AlertRule protocol + helper utilities
    rare_species.py
    first_detection.py
    first_return.py
  registry.py         # Rule loading from YAML configuration
```

- `alerts.engine.AlertEngine` owns the rule registry, processes detections, and emits events.
- `alerts.models` defines the shared event schema consumed by notifications.
- Each rule lives in `alerts.rules.<name>`; adding new rules is as simple as creating a module and registering it.
- Future optional components (e.g., persistence adapters) can live alongside the core engine.

### AlertEngine
- Exposed methods:
  - `process_detection(detection)` – called synchronously after BirdNET analysis.
  - `flush_all()` – maintenance helper (ensures buffered events are emitted on shutdown if needed).
- Implements a thin evaluation loop that loads enabled rules from configuration and forwards generated events to the publisher.
- Does **not** buffer daily summaries or control notification frequency.

### AlertRule Interface
```python
class AlertRule(Protocol):
    def evaluate(self, detection: DetectionItem, context: AlertContext) -> Iterable[AlertEvent]:
        ...
```

For v1 we’ll implement two rules:
1. **RareSpeciesRule** – triggers immediately if detection.scientific_name (or common name alias) matches an entry in `config.alerts.rules.rare_species`.
2. **FirstDetection** – triggers the first time a species has been identified by the app
3. **FirstReturn** - triggers on detection of a species which has not been detected in x period (Default > 2 months )

Both rules generate a simple event payload (`type`, `title`, `message`, `detection` metadata). Rules run in order; each returned event is forwarded to the publisher.

> Daily summaries, batching, and per-channel throttling are handled entirely inside the notification module. Alerts remain focused on rule evaluation only.

### EventPublisher
- Internal mediator with a single method `broadcast(event: AlertEvent)`.
- Immediately delegates to `NotificationService.send_alert(event)`.
- Ensures alerts remain decoupled from transport logic.

#### Event Payload Structure
Alert events are exchanged as lightweight JSON-friendly dictionaries so they can be serialized or logged without a schema registry. The `AlertEvent` dataclass will expose a `.model_dump()` (or `.dict()`) returning:

```json
{
  "name": "rare_species",
  "severity": "info",
  "detected_at": "2025-10-20T18:40:43Z",
  "species": {
    "scientific_name": "Gymnorhinus cyanocephalus",
    "common_name": "Pinyon Jay",
    "id": "gymnorhinus-cyanocephalus"
  },
  "detection": {
    "confidence": 0.96,
    "recording_path": "/captures/whobox/20251020_184043.wav",
    "start_time": 4.5,
    "end_time": 7.2
  },
  "context": {
    "rule": "rare_species",
    "notes": "Matched rare species list"
  }
}
```

- `name`: Identifier of the alert rule (e.g., `rare_species`, `first_detection`).
- `species`: Includes pointers the notification layer can use (scientific/common names, canonical id).
- `detection`: Minimal metadata needed for messages (confidence, recording link, timing). No heavy payloads; audio or large attachments remain outside the event (notifications can fetch them as needed).
- `context`: Free-form extras the rule wishes to pass downstream (e.g., streak counters, rarity reason).

Notification channels consume the `AlertEvent` object directly, using whichever fields they need. This keeps the alert hand-off compact while still providing enough context to craft meaningful messages.



## Configuration Additions (config.yaml)

```yaml
alerts:
  rules:
    rare_species:
      enabled: true
      scientific_names:
        - Gymnorhinus cyanocephalus
        - Buteo swainsoni
    first_detection:
      enabled: true
    first_return:
        enabled: true
        period: 2 months # understands days. weeks, months, or years
```

- Notifications module will load additional keys (e.g., summary schedule, channel settings) from `notifications.*` sections; alerts only care about the `alerts.rules` block.

## Operational Considerations
- Real-time alerts run inline with the analyzer; keep rule evaluation fast or async to avoid blocking ingestion.
- Persist rule configuration in YAML so ops can add/remove triggers without redeploying.
- Provide a CLI (`python -m app.jobs.alerts_flush`) to emit any buffered events (if we introduce buffering later) – optional for v1 since alerts are push-only.
- Implement logging per event at INFO level (rule name, species, metadata).

## Post-v1 Enhancements
- User-scoped subscriptions (alerts by location or species).
- Sophisticated deduplication (e.g., throttle repeated detections within X minutes).
- Weighted rarity scoring (e.g., eBird frequency data).
- Automated test coverage for rule combinations.
