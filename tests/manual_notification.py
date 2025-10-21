from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    raise SystemExit("Missing dependency 'pyyaml'. Run `pip install -r requirements.txt`.") from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / "app"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

try:
    from app.lib.alerts import AlertEvent  # noqa: E402
    from app.lib.notifications import NotificationService  # noqa: E402
    from app.lib.setup import initialize_environment  # noqa: E402
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    if exc.name == "httpx":
        raise SystemExit("Missing dependency 'httpx'. Run `pip install -r requirements.txt`.") from exc
    raise


CONFIG_PATH = APP_DIR / "config.yaml"


def _load_notification_service() -> tuple[NotificationService, Dict[str, Any]]:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Missing config file at {CONFIG_PATH}")

    config_text = CONFIG_PATH.read_text(encoding="utf-8")
    config_data = yaml.safe_load(config_text)

    _app_config, resources = initialize_environment(config_data, base_dir=APP_DIR)

    notifications_config = resources.get("notifications_config") or {}
    if not notifications_config:
        raise SystemExit("No notifications section found in config.yaml")

    storage_paths = resources.get("storage_paths") or {}
    temp_path = storage_paths.get("temp_path") or storage_paths.get("base_path") or (APP_DIR / "data" / "temp")
    temp_path = Path(temp_path)
    temp_path.mkdir(parents=True, exist_ok=True)
    summary_storage_path = temp_path / "alerts_summary.json"

    service = NotificationService(notifications_config, summary_storage_path)
    return service, notifications_config


def _build_sample_event(common_name: str, scientific_name: str, confidence: float, recording_path: str) -> AlertEvent:
    detected_at = datetime.now(timezone.utc)
    return AlertEvent(
        name="manual_test",
        severity="info",
        detected_at=detected_at,
        species={
            "common_name": common_name,
            "scientific_name": scientific_name,
            "id": scientific_name.lower().replace(" ", "-"),
        },
        detection={
            "confidence": confidence,
            "recording_path": recording_path,
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a manual notification using the configured channels.")
    parser.add_argument(
        "--mode",
        choices=("alert", "summary", "both"),
        default="both",
        help="Choose whether to send a real-time alert, flush summaries, or both.",
    )
    parser.add_argument(
        "--common-name",
        default="Common Raven",
        help="Common name to include in the sample alert payload.",
    )
    parser.add_argument(
        "--scientific-name",
        default="Corvus corax",
        help="Scientific name to include in the sample alert payload.",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.95,
        help="Detection confidence to include in the sample alert payload.",
    )
    parser.add_argument(
        "--recording-path",
        default="/tmp/sample_recording.wav",
        help="Recording path to include in the sample alert payload.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    service, config = _load_notification_service()

    enabled_channels = []
    for channel_name in ("email", "telegram"):
        channel_config = config.get(channel_name)
        if isinstance(channel_config, dict) and channel_config.get("enabled"):
            enabled_channels.append(channel_name)

    if not enabled_channels:
        print("No notification channels are enabled. Update config.yaml before running the test.")
        return

    print(f"Loaded notification channels: {', '.join(enabled_channels)}")

    try:
        if args.mode in {"alert", "both"}:
            event = _build_sample_event(
                common_name=args.common_name,
                scientific_name=args.scientific_name,
                confidence=args.confidence,
                recording_path=args.recording_path,
            )
            service.handle_alert(event)
            print("✔ Sent real-time alert payload.")

        if args.mode in {"summary", "both"}:
            service.flush_summaries()
            print("✔ Flushed summary notifications.")
    finally:
        service.close()


if __name__ == "__main__":
    main()
