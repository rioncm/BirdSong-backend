# Audio Analysis Scalability Guide

This note captures the staged plan for keeping `/remote/upload` fast while giving the analyzer room to grow. Each stage builds on the previous one, so we only pay the complexity cost when the workload proves it is needed.

## Stage 0: Current Async Hand-Off (default)
- **Mechanism**: FastAPI endpoint writes the WAV to disk and schedules analysis via `asyncio.create_task`/`asyncio.to_thread`.
- **Why**: Zero extra dependencies, sub-second API latency, good for a single-node install.
- **Operate here when**:
  - Analyzer host stays below ~70 % CPU on average.
  - Concurrent uploads < number of CPU cores.
  - Losing a process (or host) does not risk data loss because clients can retry.

## Stage 1: Local Worker Queue
- **Mechanism**: Replace the in-process task with an internal queue (e.g., `asyncio.Queue`, `rq`, or a lightweight Redis-backed list) and a dedicated worker process on the same host.
- **Benefits**:
  - Protects API processes from analyzer crashes.
  - Allows multiple local workers to drain the queue when CPU bursts happen.
- **Adopt when**:
  - API latency is still acceptable but analyzer spikes create back-pressure.
  - You need per-task persistence/retries without managing multiple machines.
  - You can tolerate a single Redis instance or similar lightweight broker.

## Stage 2: Distributed Task Queue (Celery, Dramatiq, Huey)
- **Mechanism**: API publishes jobs to a broker (Redis/RabbitMQ); one or more remote workers execute analyzer tasks.
- **Benefits**:
  - Horizontal scaling—spin up workers close to the microphones or in GPU-friendly regions.
  - Built-in retries, monitoring, rate limiting, and scheduled jobs.
  - Decoupled releases: API and analyzer can ship independently.
- **Adopt when**:
  - Single-host worker pool cannot keep the backlog <5 minutes during the peak hour.
  - You need durable guarantees (job survives restarts) and auditability.
  - Multiple teams need to add new analysis pipelines without touching API servers.

## Stage 3: Analyzer Fleet / Auto-Scaling
- **Mechanism**: Containerized Celery (or equivalent) workers behind auto-scaling rules (Kubernetes HPA, AWS ECS, etc.).
- **Extras**:
  - Central metrics + tracing (Prometheus, OpenTelemetry) to measure throughput per worker.
  - Queue length-based scaling to keep SLA (e.g., max wait < 2 minutes).
- **Adopt when**:
  - Analyzer CPU hours exceed the capacity of a static fleet.
  - There is a business SLA on turnaround time or per-microphone latency.

## Decision Gates Summary
| Gate | Metric | Action |
| --- | --- | --- |
| G0 | API p95 latency > 500 ms because analyzer blocks | Move from in-request analysis to Stage 0 hand-off (done). |
| G1 | Analyzer CPU ≥ 80 % for >15 min/day or >4 pending background tasks | Introduce Stage 1 local queue with multiple workers. |
| G2 | Backlog wait > 5 min or need multi-host resilience | Adopt Stage 2 distributed queue (Celery/Dramatiq/etc.). |
| G3 | Need elastic capacity or >10 concurrent workers | Deploy Stage 3 auto-scaling fleet with centralized monitoring. |

## Migration Checklist
1. **Observability**: Log queue depth, task durations, and failures before scaling so you know which gate you’ve crossed.
2. **Idempotent jobs**: Analyzer tasks must tolerate replays (e.g., de-duplicate on `(microphone_id, timestamp)`).
3. **Shared storage**: Ensure workers can reach the WAV files (object storage or replicated volume).
4. **Secrets/config**: API publishes API keys or metadata the worker needs; store in env vars or secret manager.
5. **Rollback plan**: Keep the previous stage deployable until the new stage proves stable for one full monitoring window.

## Playback Scalability Lane
- **Dedicated service**: `playback_api` runs as a separate container and handles `/playback/recordings/{wav_id}` live transcoding/filtering so API pods stay focused on metadata and ingest.
- **Scale model**: Start with one playback replica, then scale horizontally behind Traefik as concurrent listening grows.
- **Contract**:
  - Query `format=mp3|wav|ogg` controls output codec/container.
  - Query `filter=none|enhanced` toggles noise-reduced EQ/compression chain.
- **Routing**:
  - Configure `BIRDSONG_PLAYBACK_SERVICE_ENABLED=true`.
  - Set `BIRDSONG_PLAYBACK_SERVICE_BASE_URL` (for example, `https://playback.api.birdsong.diy`).
  - API detection payloads then emit playback URLs pointing at the playback tier.
