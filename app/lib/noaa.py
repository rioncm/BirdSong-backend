from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun

from lib.clients.noaa import NoaaClient, NoaaClientError
from lib.config import AppConfig, StreamConfig, MicrophoneConfig
from lib.data import crud
from lib.data.db import get_session


NOAA_SOURCE_LABEL = "NOAA NWS"


@dataclass(frozen=True)
class ForecastResult:
    target_date: date
    forecast_high: Optional[float]
    forecast_low: Optional[float]
    forecast_rain: Optional[float]
    dawn: Optional[time]
    sunrise: Optional[time]
    solar_noon: Optional[time]
    sunset: Optional[time]
    dusk: Optional[time]
    season: Optional[str]
    issued_at: Optional[datetime]
    timezone: str


@dataclass(frozen=True)
class ObservationResult:
    target_date: date
    actual_high: Optional[float]
    actual_low: Optional[float]
    actual_rain: Optional[float]
    updated_at: Optional[datetime]


def determine_season(target_date: date, latitude: float) -> str:
    """
    Return a simple meteorological season label based on latitude.
    """
    month = target_date.month
    northern = latitude >= 0

    if northern:
        return {
            12: "winter",
            1: "winter",
            2: "winter",
            3: "spring",
            4: "spring",
            5: "spring",
            6: "summer",
            7: "summer",
            8: "summer",
            9: "autumn",
            10: "autumn",
            11: "autumn",
        }[month]
    return {
        12: "summer",
        1: "summer",
        2: "summer",
        3: "autumn",
        4: "autumn",
        5: "autumn",
        6: "winter",
        7: "winter",
        8: "winter",
        9: "spring",
        10: "spring",
        11: "spring",
    }[month]


def _iso_to_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _parse_forecast(
    forecast_payload: Dict[str, Any],
    target_date: date,
    tz: ZoneInfo,
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[datetime]]:
    properties = forecast_payload.get("properties") or {}
    periods = properties.get("periods") or []

    high: Optional[float] = None
    low: Optional[float] = None
    rain_probability: Optional[float] = None

    for period in periods:
        if not isinstance(period, dict):
            continue
        start_time_raw = period.get("startTime")
        if not isinstance(start_time_raw, str):
            continue
        try:
            start_time = _iso_to_datetime(start_time_raw).astimezone(tz)
        except ValueError:
            continue
        if start_time.date() != target_date:
            continue

        temperature = period.get("temperature")
        if isinstance(temperature, (int, float)):
            if period.get("isDaytime"):
                high = temperature if high is None else max(high, float(temperature))
            else:
                low = temperature if low is None else min(low, float(temperature))

        probability = period.get("probabilityOfPrecipitation") or {}
        probability_value = probability.get("value")
        if isinstance(probability_value, (int, float)):
            probability_fraction = float(probability_value) / 100.0
            rain_probability = (
                probability_fraction
                if rain_probability is None
                else max(rain_probability, probability_fraction)
            )

    generated_at_raw = properties.get("generatedAt")
    issued_at = None
    if isinstance(generated_at_raw, str):
        try:
            issued_at = _iso_to_datetime(generated_at_raw)
        except ValueError:
            issued_at = None

    return high, low, rain_probability, issued_at


def _compute_solar_events(
    latitude: float,
    longitude: float,
    tz_name: str,
    target_date: date,
) -> Tuple[Optional[time], Optional[time], Optional[time], Optional[time], Optional[time]]:
    try:
        location = LocationInfo(latitude=latitude, longitude=longitude, timezone=tz_name)
        observer = location.observer
        times = sun(observer, date=target_date, tzinfo=ZoneInfo(tz_name))
        return (
            times.get("dawn").time() if times.get("dawn") else None,
            times.get("sunrise").time() if times.get("sunrise") else None,
            times.get("noon").time() if times.get("noon") else None,
            times.get("sunset").time() if times.get("sunset") else None,
            times.get("dusk").time() if times.get("dusk") else None,
        )
    except Exception:  # noqa: BLE001 - astral can raise for polar conditions
        return None, None, None, None, None


def refresh_daily_forecast(
    *,
    client: NoaaClient,
    latitude: float,
    longitude: float,
    target_date: Optional[date] = None,
    timezone_hint: Optional[str] = None,
) -> ForecastResult:
    target = target_date or datetime.now().date()
    point = client.get_point(latitude, longitude)
    properties = point.get("properties") or {}
    grid_id = properties.get("gridId")
    grid_x = properties.get("gridX")
    grid_y = properties.get("gridY")

    if grid_id is None or grid_x is None or grid_y is None:
        raise NoaaClientError("Grid metadata missing from NOAA point response")

    tz_name = timezone_hint or properties.get("timeZone") or "UTC"
    tz = ZoneInfo(tz_name)

    forecast_payload = client.get_forecast(str(grid_id), int(grid_x), int(grid_y))
    forecast_high, forecast_low, rain_probability, issued_at = _parse_forecast(
        forecast_payload,
        target,
        tz,
    )

    dawn, sunrise, solar_noon, sunset, dusk = _compute_solar_events(
        latitude,
        longitude,
        tz_name,
        target,
    )

    season = determine_season(target, latitude)

    return ForecastResult(
        target_date=target,
        forecast_high=forecast_high,
        forecast_low=forecast_low,
        forecast_rain=rain_probability,
        dawn=dawn,
        sunrise=sunrise,
        solar_noon=solar_noon,
        sunset=sunset,
        dusk=dusk,
        season=season,
        issued_at=issued_at,
        timezone=tz_name,
    )


def store_forecast(result: ForecastResult) -> None:
    session = get_session()
    try:
        crud.upsert_day_forecast(
            session,
            target_date=result.target_date,
            dawn=result.dawn,
            sunrise=result.sunrise,
            solar_noon=result.solar_noon,
            sunset=result.sunset,
            dusk=result.dusk,
            forecast_high=result.forecast_high,
            forecast_low=result.forecast_low,
            forecast_rain=result.forecast_rain,
            season=result.season,
            issued_at=result.issued_at,
            source=NOAA_SOURCE_LABEL,
        )
        session.commit()
    finally:
        session.close()


def _convert_temperature(value: Optional[float], unit: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    if not unit or "degC" in unit:
        return (float(value) * 9 / 5) + 32
    return float(value)


def _convert_precipitation(value: Optional[float], unit: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    if not unit:
        return float(value)
    if "mm" in unit.lower():
        return float(value) / 25.4
    return float(value)


def backfill_observations(
    *,
    client: NoaaClient,
    latitude: float,
    longitude: float,
    target_date: Optional[date] = None,
    timezone_hint: Optional[str] = None,
) -> ObservationResult:
    target = target_date or (datetime.now().date() - timedelta(days=1))
    point = client.get_point(latitude, longitude)
    properties = point.get("properties") or {}
    tz_name = timezone_hint or properties.get("timeZone") or "UTC"
    tz = ZoneInfo(tz_name)

    stations_url = properties.get("observationStations")
    stations_payload = client.get_observation_stations(stations_url)
    features = stations_payload.get("features") or []
    if not features:
        raise NoaaClientError("No NOAA observation stations returned for location")
    station_id = (
        features[0]
        .get("properties", {})
        .get("stationIdentifier")
    )
    if not station_id:
        raise NoaaClientError("Unable to resolve station identifier for observations")

    start_dt = datetime.combine(target, time(0, 0), tzinfo=tz).astimezone(ZoneInfo("UTC"))
    end_dt = (datetime.combine(target, time(23, 59), tzinfo=tz) + timedelta(minutes=59)).astimezone(
        ZoneInfo("UTC")
    )

    observations_payload = client.get_observations(
        station_id,
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
        limit=500,
    )

    features = observations_payload.get("features") or []

    high: Optional[float] = None
    low: Optional[float] = None
    rain_total = 0.0
    has_precip = False
    latest_timestamp: Optional[datetime] = None

    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}

        obs_time_raw = props.get("timestamp")
        if isinstance(obs_time_raw, str):
            try:
                obs_time = _iso_to_datetime(obs_time_raw)
                latest_timestamp = obs_time if latest_timestamp is None else max(latest_timestamp, obs_time)
            except ValueError:
                pass

        temperature = props.get("temperature") or {}
        temp_value = temperature.get("value")
        temp_unit = temperature.get("unitCode")
        converted_temp = _convert_temperature(temp_value, temp_unit)
        if converted_temp is not None:
            high = converted_temp if high is None else max(high, converted_temp)
            low = converted_temp if low is None else min(low, converted_temp)

        precip = props.get("precipitationLastHour") or {}
        precip_value = precip.get("value")
        precip_unit = precip.get("unitCode")
        converted_precip = _convert_precipitation(precip_value, precip_unit)
        if converted_precip is not None:
            rain_total += converted_precip
            has_precip = True

    rain_total_final = rain_total if has_precip else None

    return ObservationResult(
        target_date=target,
        actual_high=high,
        actual_low=low,
        actual_rain=rain_total_final,
        updated_at=latest_timestamp,
    )


def store_observations(result: ObservationResult) -> None:
    session = get_session()
    try:
        crud.update_day_actuals(
            session,
            target_date=result.target_date,
            actual_high=result.actual_high,
            actual_low=result.actual_low,
            actual_rain=result.actual_rain,
            updated_at=result.updated_at,
            source=NOAA_SOURCE_LABEL,
        )
        session.commit()
    finally:
        session.close()


def _pick_primary_coordinates(config: AppConfig) -> Optional[Tuple[float, float]]:
    birdsong = config.birdsong

    def _extract_from_mic(mic: MicrophoneConfig) -> Optional[Tuple[float, float]]:
        if mic.latitude is not None and mic.longitude is not None:
            return float(mic.latitude), float(mic.longitude)
        return None

    def _extract_from_stream(stream: StreamConfig) -> Optional[Tuple[float, float]]:
        if stream.latitude is not None and stream.longitude is not None:
            return float(stream.latitude), float(stream.longitude)
        return None

    for mic in birdsong.microphones.values():
        coords = _extract_from_mic(mic)
        if coords:
            return coords

    for stream in birdsong.streams.values():
        coords = _extract_from_stream(stream)
        if coords:
            return coords

    return None


def update_daily_weather_from_config(
    config: AppConfig,
    *,
    client: Optional[NoaaClient] = None,
    target_date: Optional[date] = None,
    include_actuals: bool = False,
    timezone_hint: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Tuple[ForecastResult, Optional[ObservationResult]]:
    coordinates = _pick_primary_coordinates(config)
    if coordinates is None:
        raise ValueError(
            "Unable to determine coordinates for NOAA update; configure latitude/longitude."
        )

    close_client = False
    if client is None:
        client = NoaaClient(user_agent=user_agent)
        close_client = True

    try:
        forecast = refresh_daily_forecast(
            client=client,
            latitude=coordinates[0],
            longitude=coordinates[1],
            target_date=target_date,
            timezone_hint=timezone_hint,
        )
        store_forecast(forecast)

        observation_result = None
        if include_actuals:
            observation_result = backfill_observations(
                client=client,
                latitude=coordinates[0],
                longitude=coordinates[1],
                target_date=target_date,
                timezone_hint=timezone_hint,
            )
            store_observations(observation_result)

        return forecast, observation_result
    finally:
        if close_client:
            client.close()
