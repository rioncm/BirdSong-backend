from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timezone
from typing import Any, Dict, Optional

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.orm import Session

from .tables import data_citations, data_sources, days, idents, recordings, species


def generate_species_id(scientific_name: str) -> str:
    normalized = scientific_name.strip().lower()
    if not normalized:
        raise ValueError("scientific_name must be a non-empty string")
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    return digest[:12]


def get_species_by_id(session: Session, species_id: str) -> Optional[Dict[str, Any]]:
    result = session.execute(
        select(species).where(species.c.id == species_id)
    ).mappings().first()
    return dict(result) if result is not None else None


def get_species_by_scientific_name(session: Session, scientific_name: str) -> Optional[Dict[str, Any]]:
    normalized = scientific_name.strip()
    if not normalized:
        raise ValueError("scientific_name must be a non-empty string")

    result = session.execute(
        select(species).where(func.lower(species.c.sci_name) == normalized.lower())
    ).mappings().first()
    return dict(result) if result is not None else None


def upsert_species(session: Session, payload: Dict[str, Any]) -> None:
    if "id" not in payload:
        raise ValueError("payload missing required field 'id'")

    species_id = payload["id"]
    existing = get_species_by_id(session, species_id)

    sanitized = {key: value for key, value in payload.items() if value is not None}

    if existing:
        session.execute(
            update(species)
            .where(species.c.id == species_id)
            .values(**sanitized)
        )
    else:
        session.execute(insert(species).values(**sanitized))


def get_data_source_id(session: Session, name: str) -> Optional[int]:
    result = session.execute(
        select(data_sources.c.id).where(data_sources.c.name == name)
    ).scalar_one_or_none()
    return int(result) if result is not None else None


def upsert_data_citation(
    session: Session,
    *,
    source_id: int,
    species_id: str,
    data_type: str,
    content: str,
) -> None:
    existing = session.execute(
        select(data_citations.c.citation_id).where(
            data_citations.c.source_id == source_id,
            data_citations.c.species_id == species_id,
            data_citations.c.data_type == data_type,
        )
    ).scalar_one_or_none()

    sanitized_content = str(content)
    timestamp = datetime.utcnow()

    if existing is not None:
        session.execute(
            update(data_citations)
            .where(data_citations.c.citation_id == existing)
            .values(content=sanitized_content, updated_date=timestamp)
        )
    else:
        session.execute(
            insert(data_citations).values(
                source_id=source_id,
                species_id=species_id,
                data_type=data_type,
                content=sanitized_content,
                created_date=timestamp,
                updated_date=timestamp,
            )
        )


def ensure_day(session: Session, target_date: date) -> int:
    row = session.execute(
        select(days.c.date_id).where(days.c.date == target_date)
    ).first()
    if row is not None:
        return int(row[0])

    result = session.execute(
        insert(days).values(date=target_date)
    )
    day_id = result.inserted_primary_key[0] if result.inserted_primary_key else None
    if day_id is None:
        row = session.execute(
            select(days.c.date_id).where(days.c.date == target_date)
        ).first()
        if row is None:
            raise RuntimeError("Failed to create day record")
        return int(row[0])
    return int(day_id)


def ensure_recording(
    session: Session,
    wav_id: str,
    path: str,
    *,
    source_id: Optional[str] = None,
    source_name: Optional[str] = None,
    source_display_name: Optional[str] = None,
    source_location: Optional[str] = None,
) -> None:
    existing = session.execute(
        select(recordings).where(recordings.c.wav_id == wav_id)
    ).mappings().first()

    payload = {
        "path": path,
        "source_id": source_id,
        "source_name": source_name,
        "source_display_name": source_display_name,
        "source_location": source_location,
    }
    sanitized = {key: value for key, value in payload.items() if value is not None}

    if existing:
        updates: Dict[str, Any] = {}
        if existing.get("path") != path:
            updates["path"] = path
        for key, value in sanitized.items():
            if existing.get(key) != value:
                updates[key] = value
        if updates:
            session.execute(
                update(recordings)
                .where(recordings.c.wav_id == wav_id)
                .values(**updates)
            )
        return

    session.execute(
        insert(recordings).values(
            wav_id=wav_id,
            **sanitized,
        )
    )


def insert_detection(
    session: Session,
    *,
    day_id: int,
    species_id: str,
    date_value: date,
    time_value: Optional[time],
    common_name: Optional[str],
    scientific_name: str,
    confidence: Optional[float],
    wav_id: Optional[str],
    start_time: Optional[float],
    end_time: Optional[float],
) -> bool:
    existing = session.execute(
        select(idents.c.id).where(
            and_(
                idents.c.wav_id == wav_id,
                idents.c.species_id == species_id,
                idents.c.start_time == start_time,
                idents.c.end_time == end_time,
            )
        )
    ).first()
    if existing:
        return False

    session.execute(
        insert(idents).values(
            date_id=day_id,
            species_id=species_id,
            date=date_value,
            time=time_value,
            common_name=common_name,
            sci_name=scientific_name,
            confidence=confidence,
            wav_id=wav_id,
            start_time=start_time,
            end_time=end_time,
        )
    )
    return True


def update_species_detection_stats(
    session: Session,
    species_id: str,
    detection_dt: datetime,
) -> None:
    species_row = get_species_by_id(session, species_id)
    if not species_row:
        return

    timestamp = detection_dt
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)

    first_id = species_row.get("first_id")
    last_id = species_row.get("last_id")
    id_days = species_row.get("id_days") or 0

    updates: Dict[str, Any] = {}

    if first_id is None or timestamp < first_id:
        updates["first_id"] = timestamp

    increment_day = False
    if last_id is None:
        increment_day = True
    else:
        if timestamp > last_id:
            updates["last_id"] = timestamp
            last_date = last_id.date()
            if timestamp.date() > last_date:
                increment_day = True
        else:
            updates.setdefault("last_id", last_id)

    if increment_day:
        id_days = max(id_days, 0) + 1
        updates["id_days"] = id_days
    elif "last_id" in updates and "id_days" not in updates:
        updates["id_days"] = id_days

    if updates:
        session.execute(
            update(species)
            .where(species.c.id == species_id)
            .values(**updates)
        )


def get_day(session: Session, target_date: date) -> Optional[Dict[str, Any]]:
    result = session.execute(
        select(days).where(days.c.date == target_date)
    ).mappings().first()
    return dict(result) if result is not None else None


def upsert_day_forecast(
    session: Session,
    *,
    target_date: date,
    dawn,
    sunrise,
    solar_noon,
    sunset,
    dusk,
    forecast_high: Optional[float],
    forecast_low: Optional[float],
    forecast_rain: Optional[float],
    season: Optional[str],
    issued_at: Optional[datetime],
    source: Optional[str] = None,
) -> None:
    existing = get_day(session, target_date)

    payload = {
        "date": target_date,
        "dawn": dawn,
        "sunrise": sunrise,
        "solar_noon": solar_noon,
        "sunset": sunset,
        "dusk": dusk,
        "forecast_high": forecast_high,
        "forecast_low": forecast_low,
        "forecast_rain": forecast_rain,
        "season": season,
        "forecast_issued_at": issued_at,
        "forecast_source": source,
    }

    sanitized = {key: value for key, value in payload.items() if value is not None}

    if existing:
        session.execute(
            update(days)
            .where(days.c.date == target_date)
            .values(**sanitized)
        )
    else:
        session.execute(insert(days).values(**sanitized))


def update_day_actuals(
    session: Session,
    *,
    target_date: date,
    actual_high: Optional[float],
    actual_low: Optional[float],
    actual_rain: Optional[float],
    updated_at: Optional[datetime],
    source: Optional[str] = None,
) -> None:
    existing = get_day(session, target_date)

    payload = {
        "actual_high": actual_high,
        "actual_low": actual_low,
        "actual_rain": actual_rain,
        "actual_updated_at": updated_at,
        "actual_source": source,
    }
    sanitized = {key: value for key, value in payload.items() if value is not None}

    if existing:
        session.execute(
            update(days)
            .where(days.c.date == target_date)
            .values(**sanitized)
        )
    else:
        session.execute(
            insert(days).values(
                date=target_date,
                **sanitized,
            )
        )
