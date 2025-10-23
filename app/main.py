from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import yaml
import os
from lib.analyzer import BaseAnalyzer
from lib.capture import AudioCapture
from lib.clients import WikimediaClient
from lib.enrichment import SpeciesEnricher
from lib.logging_utils import setup_debug_logging
from lib.persistence import persist_analysis_results
from lib.setup import initialize_environment


PROJECT_ROOT = Path(__file__).resolve().parent
_config_override = os.getenv("BIRDSONG_CONFIG")
CONFIG_PATH = Path(_config_override) if _config_override else PROJECT_ROOT / "config.yaml"
DEBUG_LOGGER = setup_debug_logging(PROJECT_ROOT)


def _build_species_enricher(resources: dict) -> SpeciesEnricher:
    headers_map: Dict[str, Dict[str, str]] = {}
    user_agent_map: Dict[str, Optional[str]] = {}

    raw_headers = resources.get("data_source_headers")
    if isinstance(raw_headers, dict):
        headers_map = {
            str(key): dict(value)
            for key, value in raw_headers.items()
            if isinstance(value, dict)
        }

    raw_user_agents = resources.get("data_source_user_agents")
    if isinstance(raw_user_agents, dict):
        user_agent_map = {
            str(key): value
            for key, value in raw_user_agents.items()
            if value
        }

    wikimedia_headers = headers_map.get("Wikimedia Commons", {})
    wikimedia_user_agent = user_agent_map.get("Wikimedia Commons") or wikimedia_headers.get("User-Agent")
    wikimedia_client = WikimediaClient(user_agent=wikimedia_user_agent) if wikimedia_user_agent else None

    return SpeciesEnricher(wikimedia_client=wikimedia_client)


def load_configuration():
    with open(CONFIG_PATH, "r", encoding="utf-8") as config_file:
        config_data = yaml.safe_load(config_file)
    return initialize_environment(config_data, base_dir=PROJECT_ROOT)


def run_capture_loop(
    max_runtime: Optional[float] = None,
    loop_interval: float = 1.0,
) -> None:
    """
    Process recordings for each configured stream indefinitely.

    When max_runtime is provided, the loop stops after the given number
    of seconds—handy for testing to avoid long-running sessions.
    """
    DEBUG_LOGGER.info(
        "capture_loop.start",
        extra={"max_runtime": max_runtime, "loop_interval": loop_interval},
    )
    app_config, resources = load_configuration()
    birdnet_config = app_config.birdsong.config
    analyzer = BaseAnalyzer(
        birdnet_config=birdnet_config,
        log_path=PROJECT_ROOT / "logs" / "analyzer.log",
    )
    species_enricher = _build_species_enricher(resources)
    start_time = time.monotonic()

    while True:
        for stream_name, stream_config in app_config.birdsong.streams.items():
            DEBUG_LOGGER.debug(
                "capture_loop.stream_tick",
                extra={
                    "stream_name": stream_name,
                    "stream_id": stream_config.stream_id,
                    "kind": stream_config.kind,
                },
            )
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            output_path = Path(stream_config.output_folder) / f"{timestamp}.wav"
            capture = AudioCapture(
                stream_config=stream_config,
                birdnet_config=birdnet_config,
                output_file=str(output_path),
            )
            capture.capture()
            print(f"[{timestamp}] Captured audio for {stream_name} -> {output_path}")

            try:
                analyze_result = analyzer.analyze(
                    output_path,
                    latitude=stream_config.latitude,
                    longitude=stream_config.longitude,
                    stream_id=stream_config.stream_id,
                )
                DEBUG_LOGGER.debug(
                    "analysis.complete",
                    extra={
                        "stream_name": stream_name,
                        "stream_id": stream_config.stream_id,
                        "detections": len(analyze_result.detections),
                        "duration": analyze_result.duration_seconds,
                        "frame_rate": analyze_result.frame_rate,
                    },
                )
                if analyze_result.detections:
                    top_detection = analyze_result.detections[0]
                    print(
                        f"    Top detection: {top_detection.common_name} "
                        f"({top_detection.confidence:.2f})"
                    )
                    try:
                        inserted = persist_analysis_results(
                            analyze_result,
                            analyze_result.detections,
                            source_id=stream_config.stream_id,
                            source_name=stream_name,
                            source_location=stream_config.location,
                            species_enricher=species_enricher,
                        )
                        if inserted:
                            print(f"    Stored {inserted} detections.")
                        DEBUG_LOGGER.info(
                            "persistence.complete",
                            extra={
                                "stream_name": stream_name,
                                "stream_id": stream_config.stream_id,
                                "inserted": inserted,
                                "wav_path": str(output_path),
                            },
                        )
                    except Exception as persist_exc:  # noqa: BLE001
                        print(f"    Persistence failed: {persist_exc}")
                        DEBUG_LOGGER.exception(
                            "persistence.error",
                            extra={
                                "stream_name": stream_name,
                                "stream_id": stream_config.stream_id,
                                "wav_path": str(output_path),
                            },
                        )
                else:
                    print("    No detections above threshold.")
                    DEBUG_LOGGER.debug(
                        "analysis.no_detections",
                        extra={
                            "stream_name": stream_name,
                            "stream_id": stream_config.stream_id,
                            "wav_path": str(output_path),
                        },
                    )
                    try:
                        output_path.unlink(missing_ok=True)
                        DEBUG_LOGGER.debug(
                            "capture.cleanup_deleted",
                            extra={
                                "stream_name": stream_name,
                                "stream_id": stream_config.stream_id,
                                "wav_path": str(output_path),
                            },
                        )
                        print(f"    Removed recording with no detections: {output_path}")
                    except OSError as cleanup_exc:
                        DEBUG_LOGGER.warning(
                            "capture.cleanup_failed",
                            extra={
                                "stream_name": stream_name,
                                "stream_id": stream_config.stream_id,
                                "wav_path": str(output_path),
                                "error": str(cleanup_exc),
                            },
                        )
            except Exception as exc:  # noqa: BLE001 - top-level loop should never crash
                print(f"    Analysis failed: {exc}")
                DEBUG_LOGGER.exception(
                    "analysis.error",
                    extra={
                        "stream_name": stream_name,
                        "stream_id": stream_config.stream_id,
                        "wav_path": str(output_path),
                    },
                )

            if max_runtime is not None:
                elapsed = time.monotonic() - start_time
                if elapsed >= max_runtime:
                    print(
                        f"Reached max runtime ({max_runtime}s). "
                        "Stopping capture loop."
                    )
                    DEBUG_LOGGER.info("capture_loop.stop", extra={"reason": "max_runtime"})
                    return
        time.sleep(loop_interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Birdsong capture loop controller.")
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Optional maximum runtime in seconds before exiting the loop.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds to sleep between capture cycles.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_capture_loop(max_runtime=args.duration, loop_interval=args.interval)
