from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
import os
from lib.analyzer import BaseAnalyzer
from lib.capture import AudioCapture
from lib.setup import initialize_environment


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = os.getenv("BIRDSONG_CONFIG", PROJECT_ROOT / "config.yaml")


def load_configuration():
    with open(CONFIG_PATH, "r", encoding="utf-8") as config_file:
        config_data = yaml.safe_load(config_file)
    return initialize_environment(config_data, base_dir=PROJECT_ROOT)


def run_capture_loop(
    max_runtime: Optional[float] = None,
    loop_interval: float = 1.0,
) -> None:
    """
    Process recordings for each configured camera indefinitely.

    When max_runtime is provided, the loop stops after the given number
    of seconds—handy for testing to avoid long-running sessions.
    """
    app_config, _resources = load_configuration()
    birdnet_config = app_config.birdsong.config
    analyzer = BaseAnalyzer(
        birdnet_config=birdnet_config,
        log_path=PROJECT_ROOT / "logs" / "analyzer.log",
    )
    start_time = time.monotonic()

    while True:
        for camera_name, camera_config in app_config.birdsong.cameras.items():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            output_path = Path(camera_config.output_folder) / f"{timestamp}.wav"
            capture = AudioCapture(
                camera_config=camera_config,
                birdnet_config=birdnet_config,
                output_file=str(output_path),
            )
            capture.capture()
            print(f"[{timestamp}] Captured audio for {camera_name} -> {output_path}")

            try:
                analyze_result = analyzer.analyze(
                    output_path,
                    latitude=camera_config.latitude,
                    longitude=camera_config.longitude,
                    camera_id=camera_config.camera_id,
                )
                if analyze_result.detections:
                    top_detection = analyze_result.detections[0]
                    print(
                        f"    Top detection: {top_detection.common_name} "
                        f"({top_detection.confidence:.2f})"
                    )
                else:
                    print("    No detections above threshold.")
            except Exception as exc:  # noqa: BLE001 - top-level loop should never crash
                print(f"    Analysis failed: {exc}")

            if max_runtime is not None:
                elapsed = time.monotonic() - start_time
                if elapsed >= max_runtime:
                    print(
                        f"Reached max runtime ({max_runtime}s). "
                        "Stopping capture loop."
                    )
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
