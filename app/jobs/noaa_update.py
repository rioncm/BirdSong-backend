from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml

# Ensure the backend/app parent directory is discoverable when invoked via `python -m`
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.clients.noaa import NoaaClient
from lib.noaa import resolve_noaa_user_agent, update_daily_weather_from_config
from lib.setup import initialize_environment


logger = logging.getLogger("birdsong.jobs.noaa")
def run_update(
    *,
    config_path: Path,
    target: Optional[date] = None,
    include_actuals: bool = False,
) -> None:
    logger.info(
        "Starting NOAA daily update (config=%s, target=%s, include_actuals=%s)",
        config_path,
        target or "today",
        include_actuals,
    )
    logger.warning("noaa_update CLI is deprecated; rely on automated scheduling for routine updates.")

    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    app_config, resources = initialize_environment(config_data=config_data, base_dir=config_path.parent)

    user_agent = resolve_noaa_user_agent(resources)
    client = NoaaClient(user_agent=user_agent)

    try:
        forecast, observations = update_daily_weather_from_config(
            app_config,
            client=client,
            target_date=target,
            include_actuals=include_actuals,
            user_agent=user_agent,
        )

        logger.info(
            "Stored NOAA forecast for %s (high=%s low=%s rain_prob=%s)",
            forecast.target_date,
            forecast.forecast_high,
            forecast.forecast_low,
            forecast.forecast_rain,
        )
        if observations:
            for observation in observations:
                logger.info(
                    "Stored NOAA observations for %s (high=%s low=%s rain_total=%s)",
                    observation.target_date,
                    observation.actual_high,
                    observation.actual_low,
                    observation.actual_rain,
                )
    finally:
        client.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the NOAA daily forecast/observation update."
    )
    parser.add_argument(
        "--config",
        default=Path(__file__).resolve().parents[1] / "config.yaml",
        type=Path,
        help="Path to the BirdSong configuration file (default: backend/app/config.yaml)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="ISO date (YYYY-MM-DD) to update. Defaults to today.",
    )
    parser.add_argument(
        "--include-actuals",
        action="store_true",
        help="Also backfill observed highs/lows/precip for the target date.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    target_date: Optional[date] = None
    if args.date:
        try:
            target_date = datetime.fromisoformat(args.date).date()
        except ValueError as exc:
            raise SystemExit(f"Invalid --date value: {args.date}") from exc

    run_update(
        config_path=args.config,
        target=target_date,
        include_actuals=args.include_actuals,
    )


if __name__ == "__main__":
    main()
