from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Dict

import pytest
from sqlalchemy import select

from lib.clients.noaa import NoaaClient
from lib.config import DatabaseConfig
from lib.data import crud
from lib.data import db as db_module
from lib.data.db import get_session, initialize_database
from lib.data.tables import days
from lib.noaa import (
    ObservationResult,
    ForecastResult,
    WeatherSite,
    backfill_observations,
    refresh_daily_forecast,
    store_forecast,
    store_observations,
    update_daily_weather_from_config,
)


POINT_PAYLOAD: Dict[str, object] = {
    "properties": {
        "gridId": "HNX",
        "gridX": 100,
        "gridY": 80,
        "timeZone": "America/Los_Angeles",
        "observationStations": "https://api.weather.gov/gridpoints/HNX/100,80/stations",
        "forecastOffice": "https://api.weather.gov/offices/HNX",
    }
}

FORECAST_PAYLOAD: Dict[str, object] = {
    "properties": {
        "generatedAt": "2025-10-19T06:00:00+00:00",
        "periods": [
            {
                "startTime": "2025-10-19T09:00:00-07:00",
                "temperature": 78,
                "isDaytime": True,
                "probabilityOfPrecipitation": {"value": 20},
            },
            {
                "startTime": "2025-10-19T21:00:00-07:00",
                "temperature": 55,
                "isDaytime": False,
                "probabilityOfPrecipitation": {"value": 10},
            },
        ],
    }
}

STATIONS_PAYLOAD: Dict[str, object] = {
    "features": [
        {
            "properties": {
                "stationIdentifier": "TEST",
                "name": "Test Station",
            }
        }
    ]
}

OBSERVATIONS_PAYLOAD: Dict[str, object] = {
    "features": [
        {
            "properties": {
                "timestamp": "2025-10-19T08:00:00+00:00",
                "temperature": {"value": 20.0, "unitCode": "wmoUnit:degC"},
                "precipitationLastHour": {"value": 1.0, "unitCode": "wmoUnit:mm"},
            }
        },
        {
            "properties": {
                "timestamp": "2025-10-19T20:00:00+00:00",
                "temperature": {"value": 12.0, "unitCode": "wmoUnit:degC"},
                "precipitationLastHour": {"value": 0.0, "unitCode": "wmoUnit:mm"},
            }
        },
    ]
}

TEST_SITE = WeatherSite(
    site_id=1,
    site_key="36.8000,-119.8000",
    latitude=36.8,
    longitude=-119.8,
    timezone="America/Los_Angeles",
    grid_id="HNX",
    grid_x=100,
    grid_y=80,
    forecast_office="https://api.weather.gov/offices/HNX",
    station_id="TEST",
    station_name="Test Station",
    last_refreshed=None,
)


class StubNoaaClient(NoaaClient):
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:  # type: ignore[override]
        self.closed = True

    def get_point(self, latitude: float, longitude: float) -> Dict[str, object]:  # type: ignore[override]
        return POINT_PAYLOAD

    def get_forecast(self, grid_id: str, grid_x: int, grid_y: int) -> Dict[str, object]:  # type: ignore[override]
        return FORECAST_PAYLOAD

    def get_observation_stations(self, stations_url: str) -> Dict[str, object]:  # type: ignore[override]
        return STATIONS_PAYLOAD

    def get_observations(self, station_id: str, *, start: str, end: str, limit: int = 1000) -> Dict[str, object]:  # type: ignore[override]
        return OBSERVATIONS_PAYLOAD


@pytest.fixture()
def temp_database(tmp_path):
    db_file = Path(tmp_path) / "noaa.db"
    config = DatabaseConfig(engine="sqlite", name=db_file.name, path=db_file.parent)

    db_module._ENGINE = None
    db_module._SESSION_FACTORY = None

    initialize_database(config)  # type: ignore[arg-type]
    try:
        yield config
    finally:
        if db_module._ENGINE is not None:
            db_module._ENGINE.dispose()
        db_module._ENGINE = None
        db_module._SESSION_FACTORY = None


def test_refresh_and_store_forecast(temp_database):
    client = StubNoaaClient()
    target = date(2025, 10, 19)
    forecast = refresh_daily_forecast(
        client=client,
        site=TEST_SITE,
        target_date=target,
    )

    assert isinstance(forecast, ForecastResult)
    assert forecast.forecast_high == 78
    assert forecast.forecast_low == 55
    assert forecast.forecast_rain == 0.2
    assert forecast.season == "autumn"

    store_forecast(forecast)

    session = get_session()
    try:
        stored = session.execute(
            select(days).where(days.c.date == target)
        ).mappings().first()
    finally:
        session.close()

    assert stored is not None
    assert stored["forecast_high"] == 78
    assert stored["forecast_low"] == 55
    assert pytest.approx(stored["forecast_rain"], rel=1e-6) == 0.2
    assert stored["season"] == "autumn"
    assert stored["forecast_office"] == "https://api.weather.gov/offices/HNX"


def test_backfill_and_store_observations(temp_database):
    client = StubNoaaClient()
    target = date(2025, 10, 19)
    observation = backfill_observations(
        client=client,
        site=TEST_SITE,
        target_date=target,
    )
    assert isinstance(observation, ObservationResult)
    assert pytest.approx(observation.actual_high, rel=1e-6) == 68.0
    assert pytest.approx(observation.actual_low, rel=1e-6) == 53.6
    assert pytest.approx(observation.actual_rain, rel=1e-6) == 0.0393700787

    store_observations(observation)

    session = get_session()
    try:
        stored = session.execute(
            select(days).where(days.c.date == target)
        ).mappings().first()
    finally:
        session.close()

    assert stored is not None
    assert pytest.approx(stored["actual_high"], rel=1e-6) == 68.0
    assert pytest.approx(stored["actual_low"], rel=1e-6) == 53.6
    assert pytest.approx(stored["actual_rain"], rel=1e-6) == 0.0393700787
    assert stored["observation_station_id"] == "TEST"
    assert stored["observation_station_name"] == "Test Station"


def test_update_daily_weather_from_config(temp_database):
    client = StubNoaaClient()
    config = SimpleNamespace(
        birdsong=SimpleNamespace(
            microphones={
                "primary": SimpleNamespace(
                    latitude=36.8,
                    longitude=-119.8,
                )
            },
            streams={},
            default_latitude=36.8,
            default_longitude=-119.8,
        )
    )

    forecast, observations = update_daily_weather_from_config(
        config,
        client=client,
        target_date=date(2025, 10, 19),
        include_actuals=True,
    )

    assert forecast.forecast_high == 78
    assert observations
    assert any(obs.actual_high is not None for obs in observations)
