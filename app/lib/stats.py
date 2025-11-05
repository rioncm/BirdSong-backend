from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .data.tables import idents, recordings, species

_WINDOW_PATTERN = re.compile(r"^(?P<value>\d+)(?P<unit>[smhdw])$")


@dataclass(frozen=True)
class TimeWindow:
    """Represents an absolute UTC window."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError("time window start must be before end")
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("time window values must include timezone info")

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO datetime '{value}'") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_window_shorthand(value: str) -> timedelta:
    """
    Convert a compact duration string (e.g. '24h', '7d', '15m') into a timedelta.
    Supported unit suffixes: s (seconds), m (minutes), h (hours), d (days), w (weeks).
    """
    if not value:
        raise ValueError("window must be a non-empty string")

    match = _WINDOW_PATTERN.match(value.strip().lower())
    if not match:
        raise ValueError(f"Invalid window string '{value}'")

    amount = int(match.group("value"))
    unit = match.group("unit")
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    if unit == "w":
        return timedelta(weeks=amount)
    raise ValueError(f"Unsupported window unit '{unit}'")


def resolve_time_window(
    *,
    start: Optional[str],
    end: Optional[str],
    window: Optional[str],
    now: Optional[datetime] = None,
    default_duration: timedelta = timedelta(hours=24),
    max_duration: timedelta = timedelta(days=31),
) -> TimeWindow:
    """
    Resolve query parameters into a concrete UTC time window.
    Priority order:
      1. Explicit start + end
      2. start + window duration
      3. end + window duration (backwards)
      4. window duration ending "now"
      5. default duration ending "now"
    """
    current = _ensure_utc(now or datetime.now(timezone.utc))

    start_dt: Optional[datetime] = _ensure_utc(_parse_iso_datetime(start)) if start else None
    end_dt: Optional[datetime] = _ensure_utc(_parse_iso_datetime(end)) if end else None

    if start_dt and end_dt:
        resolved_start = start_dt
        resolved_end = end_dt
    elif start_dt:
        delta = parse_window_shorthand(window) if window else default_duration
        resolved_start = start_dt
        resolved_end = start_dt + delta
    elif end_dt:
        delta = parse_window_shorthand(window) if window else default_duration
        resolved_end = end_dt
        resolved_start = end_dt - delta
    else:
        delta = parse_window_shorthand(window) if window else default_duration
        resolved_end = current
        resolved_start = current - delta

    if resolved_start >= resolved_end:
        raise ValueError("start must be before end")

    duration = resolved_end - resolved_start
    if duration > max_duration:
        raise ValueError("requested window exceeds allowed duration")

    return TimeWindow(start=resolved_start, end=resolved_end)


def _format_sqlite_datetime(value: datetime) -> str:
    """Return a string formatted the same way SQLite datetime() emits."""
    utc_value = value.astimezone(timezone.utc)
    naive = utc_value.replace(tzinfo=None)
    return naive.strftime("%Y-%m-%d %H:%M:%S")


def _timestamp_expr():
    time_string_expr = func.coalesce(func.strftime("%H:%M:%S", idents.c.time), "00:00:00")
    return func.datetime(idents.c.date, time_string_expr)


def _window_filters(window: TimeWindow):
    start_value = _format_sqlite_datetime(window.start)
    end_value = _format_sqlite_datetime(window.end)
    timestamp_expr = _timestamp_expr()
    return timestamp_expr >= start_value, timestamp_expr < end_value


def _build_device_lookup(index: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for entry in index:
        for key_field in ("id", "name"):
            key = entry.get(key_field)
            if key:
                lookup.setdefault(str(key), entry)
    return lookup


def fetch_overview_stats(
    session: Session,
    window: TimeWindow,
    *,
    device_index: Sequence[Dict[str, Any]] = (),
    top_species_limit: int = 5,
    top_hours_limit: int = 5,
    top_streams_limit: int = 5,
) -> Dict[str, Any]:
    filters = _window_filters(window)
    timestamp_expr = _timestamp_expr()

    detections_total_stmt = (
        select(func.count())
        .select_from(idents)
        .where(and_(*filters))
    )
    detections_total = int(session.execute(detections_total_stmt).scalar_one())

    unique_species_stmt = (
        select(func.count(func.distinct(idents.c.species_id)))
        .select_from(idents)
        .where(and_(*filters))
    )
    unique_species_count = int(session.execute(unique_species_stmt).scalar_one())

    avg_confidence_stmt = (
        select(func.avg(idents.c.confidence))
        .select_from(idents)
        .where(and_(*filters))
    )
    avg_confidence = session.execute(avg_confidence_stmt).scalar_one()
    avg_confidence_value = float(avg_confidence) if avg_confidence is not None else 0.0

    device_key_expr = func.coalesce(
        recordings.c.source_id,
        recordings.c.source_name,
        recordings.c.source_display_name,
        idents.c.wav_id,
    )
    active_devices_stmt = (
        select(func.count(func.distinct(device_key_expr)))
        .select_from(idents.join(recordings, idents.c.wav_id == recordings.c.wav_id, isouter=True))
        .where(and_(*filters))
    )
    active_devices = int(session.execute(active_devices_stmt).scalar_one())

    top_species_stmt = (
        select(
            idents.c.species_id,
            species.c.common_name,
            func.count().label("detections"),
            func.avg(idents.c.confidence).label("avg_confidence"),
        )
        .join(species, idents.c.species_id == species.c.id, isouter=True)
        .where(and_(*filters))
        .group_by(idents.c.species_id, species.c.common_name)
        .order_by(func.count().desc(), func.avg(idents.c.confidence).desc().nullslast())
        .limit(max(top_species_limit, 0))
    )
    top_species_rows = session.execute(top_species_stmt).mappings().all()
    top_species: list[Dict[str, Any]] = []
    for row in top_species_rows:
        top_species.append(
            {
                "species_id": row["species_id"],
                "common_name": row["common_name"],
                "detections": int(row["detections"]),
                "avg_confidence": float(row["avg_confidence"]) if row["avg_confidence"] is not None else None,
            }
        )

    bucket_expr = func.strftime("%Y-%m-%dT%H:00:00", timestamp_expr)
    top_hours_stmt = (
        select(
            bucket_expr.label("bucket_start"),
            func.count().label("detections"),
            func.count(func.distinct(idents.c.species_id)).label("unique_species"),
        )
        .select_from(idents)
        .where(and_(*filters))
        .group_by(bucket_expr)
        .order_by(func.count().desc(), bucket_expr.desc())
        .limit(max(top_hours_limit, 0))
    )
    top_hours_rows = session.execute(top_hours_stmt).mappings().all()
    top_hours: list[Dict[str, Any]] = []
    for row in top_hours_rows:
        bucket_start_raw: Optional[str] = row["bucket_start"]
        bucket_iso: Optional[str] = None
        if bucket_start_raw:
            bucket_dt = datetime.fromisoformat(bucket_start_raw)
            bucket_iso = _ensure_utc(bucket_dt).isoformat().replace("+00:00", "Z")
        top_hours.append(
            {
                "bucket_start": bucket_iso,
                "detections": int(row["detections"]),
                "unique_species": int(row["unique_species"]),
            }
        )

    streams_stmt = (
        select(
            device_key_expr.label("device_key"),
            func.max(recordings.c.source_id).label("source_id"),
            func.max(recordings.c.source_name).label("source_name"),
            func.max(recordings.c.source_display_name).label("source_display_name"),
            func.count().label("detections"),
            func.count(func.distinct(idents.c.species_id)).label("unique_species"),
        )
        .select_from(idents.join(recordings, idents.c.wav_id == recordings.c.wav_id, isouter=True))
        .where(and_(*filters))
        .group_by(device_key_expr)
        .order_by(func.count().desc(), func.max(recordings.c.source_display_name).desc().nullslast())
        .limit(max(top_streams_limit, 0))
    )
    streams_rows = session.execute(streams_stmt).mappings().all()
    device_lookup = _build_device_lookup(device_index)
    top_streams: list[Dict[str, Any]] = []
    for row in streams_rows:
        source_id = row["source_id"]
        source_name = row["source_name"]
        source_display_name = row["source_display_name"]
        device_key = row["device_key"]

        display_name = source_display_name or source_name or source_id or device_key
        canonical_id = source_id or device_key

        if canonical_id and canonical_id in device_lookup:
            entry = device_lookup[canonical_id]
            display_name = entry.get("display_name") or display_name
            canonical_id = entry.get("id") or canonical_id
        elif source_name and source_name in device_lookup:
            entry = device_lookup[source_name]
            display_name = entry.get("display_name") or display_name
            canonical_id = entry.get("id") or canonical_id

        top_streams.append(
            {
                "device_id": canonical_id or device_key,
                "display_name": display_name,
                "detections": int(row["detections"]),
                "unique_species": int(row["unique_species"]),
            }
        )

    return {
        "detections_total": detections_total,
        "unique_species": unique_species_count,
        "active_devices": active_devices,
        "avg_confidence": avg_confidence_value,
        "top_species": top_species,
        "top_hours": top_hours,
        "top_streams": top_streams,
    }


def _shift_months(value: datetime, months: int) -> datetime:
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    max_day = calendar.monthrange(year, month)[1]
    day = min(value.day, max_day)
    return value.replace(year=year, month=month, day=day)


def _shift_years(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # Handle February 29th on non-leap years by clamping to Feb 28th.
        if value.month == 2 and value.day == 29:
            return value.replace(year=value.year + years, day=28)
        raise


def derive_comparison_window(window: TimeWindow, selector: str) -> TimeWindow:
    selector_normalized = selector.strip().lower()
    if selector_normalized == "prior_range":
        delta = window.duration
        return TimeWindow(start=window.start - delta, end=window.start)
    if selector_normalized == "prior_month":
        return TimeWindow(
            start=_shift_months(window.start, -1),
            end=_shift_months(window.end, -1),
        )
    if selector_normalized == "prior_year":
        return TimeWindow(
            start=_shift_years(window.start, -1),
            end=_shift_years(window.end, -1),
        )
    raise ValueError(f"Unknown comparison selector '{selector}'")


def _metric_statement(
    metric: str,
    window: TimeWindow,
    *,
    species_id: Optional[str] = None,
    device_id: Optional[str] = None,
):
    filters = _window_filters(window)
    metric_key = metric.strip().lower()

    join_recordings = device_id is not None or metric_key == "active_devices"
    from_clause = (
        idents.join(recordings, idents.c.wav_id == recordings.c.wav_id, isouter=True)
        if join_recordings
        else idents
    )

    if metric_key == "detections_total":
        return (
            select(func.count())
            .select_from(from_clause)
            .where(and_(*filters))
        )
    if metric_key == "unique_species":
        return (
            select(func.count(func.distinct(idents.c.species_id)))
            .select_from(from_clause)
            .where(and_(*filters))
        )
    if metric_key == "avg_confidence":
        return (
            select(func.avg(idents.c.confidence))
            .select_from(from_clause)
            .where(and_(*filters))
        )
    if metric_key == "active_devices":
        device_key_expr = func.coalesce(
            recordings.c.source_id,
            recordings.c.source_name,
            recordings.c.source_display_name,
            idents.c.wav_id,
        )
        return (
            select(func.count(func.distinct(device_key_expr)))
            .select_from(from_clause)
            .where(and_(*filters))
        )
    raise ValueError(f"Unsupported metric '{metric}'")


def fetch_metric_value(
    session: Session,
    metric: str,
    window: TimeWindow,
    *,
    species_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> float:
    stmt = _metric_statement(
        metric,
        window,
        species_id=species_id,
        device_id=device_id,
    )
    if species_id:
        stmt = stmt.where(idents.c.species_id == species_id)
    if device_id:
        stmt = stmt.where(
            or_(
                recordings.c.source_id == device_id,
                recordings.c.source_name == device_id,
                recordings.c.source_display_name == device_id,
                idents.c.wav_id == device_id,
            )
        )
    value = session.execute(stmt).scalar_one_or_none()
    if value is None:
        return 0.0
    return float(value)


def fetch_data_comparison(
    session: Session,
    *,
    metric: str,
    primary_window: TimeWindow,
    selector: str,
    species_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    comparison_window = derive_comparison_window(primary_window, selector)
    primary_value = fetch_metric_value(
        session,
        metric,
        primary_window,
        species_id=species_id,
        device_id=device_id,
    )
    comparison_value = fetch_metric_value(
        session,
        metric,
        comparison_window,
        species_id=species_id,
        device_id=device_id,
    )

    absolute_change = primary_value - comparison_value
    if comparison_value == 0:
        percent_change: Optional[float] = None
    else:
        percent_change = (absolute_change / comparison_value) * 100.0

    return {
        "metric": metric,
        "primary_value": primary_value,
        "comparison_value": comparison_value,
        "absolute_change": absolute_change,
        "percent_change": percent_change,
        "comparison_selector": selector,
        "comparison_window": comparison_window,
    }
