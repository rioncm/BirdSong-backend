from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun

from lib.clients.noaa import NoaaClient, NoaaClientError
from lib.config import AppConfig, StreamConfig, MicrophoneConfig
from lib.data import crud
from lib.data.db import get_session


NOAA_SOURCE_LABEL = "NOAA NWS"
SITE_REFRESH_INTERVAL = timedelta(days=7)


@dataclass(frozen=True)
class WeatherSite:
    site_id: int
    site_key: str
    latitude: float
    longitude: float
    timezone: str
    grid_id: str
    grid_x: int
    grid_y: int
    forecast_office: Optional[str]
    station_id: Optional[str]
    station_name: Optional[str]
    last_refreshed: Optional[datetime]


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
    grid_id: Optional[str]
    grid_x: Optional[int]
    grid_y: Optional[int]
    forecast_office: Optional[str]


@dataclass(frozen=True)
class ObservationResult:
    target_date: date
    actual_high: Optional[float]
    actual_low: Optional[float]
    actual_rain: Optional[float]
    updated_at: Optional[datetime]
    station_id: Optional[str]
    station_name: Optional[str]


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


def _normalize_db_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            try:
                return _iso_to_datetime(value)
            except ValueError:
                return None
    return None


def _build_site_key(latitude: float, longitude: float) -> str:
    return f"{latitude:.4f},{longitude:.4f}"


def _pick_station(stations_payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    features = stations_payload.get("features") or []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        station_id = props.get("stationIdentifier") or props.get("station_id") or props.get("id")
        if not station_id:
            continue
        name = props.get("name")
        return str(station_id), name if isinstance(name, str) else None
    return None, None


def _ensure_weather_site(
    *,
    client: NoaaClient,
    latitude: float,
    longitude: float,
    timezone_hint: Optional[str] = None,
) -> WeatherSite:
    site_key = _build_site_key(latitude, longitude)
    session = get_session()
    try:
        record = crud.get_weather_site_by_key(session, site_key)
        now = datetime.utcnow()
        record_timezone = record.get("timezone") if record else None
        if timezone_hint and timezone_hint != record_timezone:
            record_timezone = timezone_hint

        def _needs_refresh(entry: Optional[Dict[str, Any]]) -> bool:
            if entry is None:
                return True
            required_fields = ("grid_id", "grid_x", "grid_y", "station_id")
            for field in required_fields:
                if entry.get(field) in (None, ""):
                    return True
            last_refreshed = _normalize_db_datetime(entry.get("last_refreshed"))
            if last_refreshed is None:
                return True
            return now - last_refreshed.replace(tzinfo=None) > SITE_REFRESH_INTERVAL

        if _needs_refresh(record):
            point = client.get_point(latitude, longitude)
            properties = point.get("properties") or {}
            grid_id = properties.get("gridId")
            grid_x = properties.get("gridX")
            grid_y = properties.get("gridY")
            tz_name = (
                timezone_hint
                or properties.get("timeZone")
                or record_timezone
                or "UTC"
            )
            forecast_office = properties.get("forecastOffice")
            stations_url = properties.get("observationStations")
            station_id: Optional[str] = None
            station_name: Optional[str] = None
            if isinstance(stations_url, str) and stations_url:
                stations_payload = client.get_observation_stations(stations_url)
                station_id, station_name = _pick_station(stations_payload)

            record = crud.upsert_weather_site(
                session,
                site_key=site_key,
                latitude=latitude,
                longitude=longitude,
                timezone=tz_name,
                grid_id=str(grid_id) if grid_id is not None else None,
                grid_x=int(grid_x) if grid_x is not None else None,
                grid_y=int(grid_y) if grid_y is not None else None,
                forecast_office=forecast_office,
                station_id=station_id,
                station_name=station_name,
                last_refreshed=now,
            )
            session.commit()
        elif timezone_hint and record_timezone != timezone_hint:
            record = crud.upsert_weather_site(
                session,
                site_key=site_key,
                latitude=latitude,
                longitude=longitude,
                timezone=timezone_hint,
                grid_id=record.get("grid_id"),
                grid_x=record.get("grid_x"),
                grid_y=record.get("grid_y"),
                forecast_office=record.get("forecast_office"),
                station_id=record.get("station_id"),
                station_name=record.get("station_name"),
                last_refreshed=record.get("last_refreshed"),
            )
            session.commit()
        else:
            session.rollback()

        final_record = crud.get_weather_site_by_key(session, site_key)
        if final_record is None:
            raise RuntimeError("Failed to persist NOAA site metadata")

        tz_name = (
            final_record.get("timezone")
            or timezone_hint
            or "UTC"
        )

        return WeatherSite(
            site_id=int(final_record["site_id"]),
            site_key=site_key,
            latitude=float(final_record["latitude"]),
            longitude=float(final_record["longitude"]),
            timezone=str(tz_name),
            grid_id=str(final_record.get("grid_id")) if final_record.get("grid_id") is not None else None,
            grid_x=int(final_record["grid_x"]) if final_record.get("grid_x") is not None else None,
            grid_y=int(final_record["grid_y"]) if final_record.get("grid_y") is not None else None,
            forecast_office=final_record.get("forecast_office"),
            station_id=str(final_record.get("station_id")) if final_record.get("station_id") else None,
            station_name=final_record.get("station_name"),
            last_refreshed=_normalize_db_datetime(final_record.get("last_refreshed")),
        )
    finally:
        session.close()

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
    site: WeatherSite,
    target_date: Optional[date] = None,
) -> ForecastResult:
    target = target_date or datetime.now().date()
    if site.grid_id is None or site.grid_x is None or site.grid_y is None:
        raise NoaaClientError("Weather site missing grid metadata; refresh required.")

    tz_name = site.timezone or "UTC"
    tz = ZoneInfo(tz_name)

    forecast_payload = client.get_forecast(str(site.grid_id), int(site.grid_x), int(site.grid_y))
    forecast_high, forecast_low, rain_probability, issued_at = _parse_forecast(
        forecast_payload,
        target,
        tz,
    )

    dawn, sunrise, solar_noon, sunset, dusk = _compute_solar_events(
        site.latitude,
        site.longitude,
        tz_name,
        target,
    )

    season = determine_season(target, site.latitude)

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
        grid_id=site.grid_id,
        grid_x=site.grid_x,
        grid_y=site.grid_y,
        forecast_office=site.forecast_office,
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
            forecast_office=result.forecast_office,
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
    site: WeatherSite,
    target_date: Optional[date] = None,
) -> ObservationResult:
    if site.station_id is None:
        raise NoaaClientError("Weather site missing observation station metadata; refresh required.")
    tz_name = site.timezone or "UTC"
    tz = ZoneInfo(tz_name)

    if target_date is None:
        local_today = datetime.now(tz).date()
        target = local_today - timedelta(days=1)
    else:
        target = target_date

    start_dt = datetime.combine(target, time(0, 0), tzinfo=tz).astimezone(ZoneInfo("UTC"))
    end_dt = (datetime.combine(target, time(23, 59), tzinfo=tz) + timedelta(minutes=59)).astimezone(
        ZoneInfo("UTC")
    )

    observations_payload = client.get_observations(
        site.station_id,
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

    rain_total_final = rain_total if has_precip else 0.0

    return ObservationResult(
        target_date=target,
        actual_high=high,
        actual_low=low,
        actual_rain=rain_total_final,
        updated_at=latest_timestamp,
        station_id=site.station_id,
        station_name=site.station_name,
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
            station_id=result.station_id,
            station_name=result.station_name,
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

    if birdsong.default_latitude is not None and birdsong.default_longitude is not None:
        return float(birdsong.default_latitude), float(birdsong.default_longitude)

    return None


def resolve_noaa_user_agent(resources: Dict[str, object]) -> Optional[str]:
    user_agents = resources.get("data_source_user_agents") or {}
    if isinstance(user_agents, dict):
        user_agent = user_agents.get(NOAA_SOURCE_LABEL)
        if user_agent:
            return str(user_agent)

    headers = resources.get("data_source_headers") or {}
    if isinstance(headers, dict):
        entry = headers.get(NOAA_SOURCE_LABEL)
        if isinstance(entry, dict):
            ua = entry.get("User-Agent")
            if ua:
                return str(ua)
    return None


def update_daily_weather_from_config(
    config: AppConfig,
    *,
    client: Optional[NoaaClient] = None,
    target_date: Optional[date] = None,
    include_actuals: bool = False,
    timezone_hint: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Tuple[ForecastResult, Sequence[ObservationResult]]:
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
        site = _ensure_weather_site(
            client=client,
            latitude=coordinates[0],
            longitude=coordinates[1],
            timezone_hint=timezone_hint,
        )
        forecast = refresh_daily_forecast(
            client=client,
            site=site,
            target_date=target_date,
        )
        store_forecast(forecast)

        observation_results: List[ObservationResult] = []
        if include_actuals:
            tz_name = site.timezone or "UTC"
            tz = ZoneInfo(tz_name)
            anchor_date = target_date or datetime.now(tz).date()

            session = get_session()
            try:
                missing_dates = crud.list_days_missing_actuals(
                    session,
                    before_date=anchor_date + timedelta(days=1),
                    limit=14,
                )
                previous_day = anchor_date - timedelta(days=1)
                previous_day_needs = False
                if previous_day < anchor_date:
                    day_row = crud.get_day(session, previous_day)
                    if day_row is None or any(
                        day_row.get(field) is None for field in ("actual_high", "actual_low", "actual_rain")
                    ):
                        previous_day_needs = True
            finally:
                session.close()

            observation_targets = {day for day in missing_dates if day < anchor_date}
            if target_date is not None and target_date <= anchor_date:
                observation_targets.add(target_date)
            if previous_day < anchor_date and previous_day_needs:
                observation_targets.add(previous_day)

            for observation_date in sorted(observation_targets):
                result = backfill_observations(
                    client=client,
                    site=site,
                    target_date=observation_date,
                )
                store_observations(result)
                observation_results.append(result)

        return forecast, observation_results
    finally:
        if close_client:
            client.close()
