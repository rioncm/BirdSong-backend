from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Sequence

from sqlalchemy.exc import SQLAlchemyError

from lib.analyzer import AnalyzeResult, DetectionResult
from lib.data import crud
from lib.data.db import get_session
from lib.enrichment import SpeciesEnricher, SpeciesEnrichmentError


logger = logging.getLogger("birdsong.persistence")
debug_logger = logging.getLogger("birdsong.debug.persistence")


def persist_analysis_results(
    analysis: AnalyzeResult,
    detections: Sequence[DetectionResult],
    *,
    source_id: Optional[str],
    source_name: Optional[str],
    source_location: Optional[str] = None,
    source_display_name: Optional[str] = None,
    species_enricher: Optional[SpeciesEnricher] = None,
    species_id_map: Optional[Dict[str, str]] = None,
) -> int:
    """
    Store analyzer detections, guaranteeing related day and recording rows exist.
    Returns the number of new detection rows inserted.
    """
    if not detections:
        return 0

    wav_path = Path(analysis.input_file)
    wav_id = wav_path.stem or wav_path.name

    capture_dt = _determine_capture_datetime(analysis, wav_path)
    detection_date = capture_dt.date()
    detection_time = capture_dt.timetz().replace(tzinfo=None)

    inserted = 0
    session = get_session()
    try:
        day_id = crud.ensure_day(session, detection_date)
        crud.ensure_recording(
            session,
            wav_id,
            str(wav_path),
            source_id=source_id or analysis.stream_id,
            source_name=source_name,
            source_display_name=source_display_name or source_name or source_id,
            source_location=source_location,
        )
        debug_logger.debug(
            "persistence.context_ready",
            extra={
                "wav_id": wav_id,
                "day_id": day_id,
                "date": detection_date.isoformat(),
                "time": detection_time.isoformat() if detection_time else None,
            },
        )

        for detection in detections:
            scientific = (detection.scientific_name or detection.label or "").strip()
            if not scientific:
                continue

            species_key = scientific.lower()
            species_id = species_id_map.get(species_key) if species_id_map else None
            if not species_id:
                species_id = _ensure_species(session, detection, species_enricher)
            if not species_id:
                logger.debug(
                    "Skipping detection without species id (source=%s, wav=%s)",
                    source_id,
                    wav_id,
                )
                continue

            created = crud.insert_detection(
                session,
                day_id=day_id,
                species_id=species_id,
                date_value=detection_date,
                time_value=detection_time,
                common_name=detection.common_name or detection.label,
                scientific_name=scientific,
                confidence=detection.confidence,
                wav_id=wav_id,
                start_time=detection.start_time,
                end_time=detection.end_time,
            )
            if created:
                inserted += 1
                debug_logger.debug(
                    "persistence.detection_inserted",
                    extra={
                        "wav_id": wav_id,
                        "species_id": species_id,
                        "confidence": detection.confidence,
                        "start_time": detection.start_time,
                        "end_time": detection.end_time,
                    },
                )
            else:
                debug_logger.debug(
                    "persistence.detection_skipped_duplicate",
                    extra={
                        "wav_id": wav_id,
                        "species_id": species_id,
                        "start_time": detection.start_time,
                        "end_time": detection.end_time,
                    },
                )

        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Failed to persist detections for wav %s", wav_path)
        raise
    finally:
        session.close()

    if inserted:
        logger.info(
            "Persisted %s detections from %s (%s) at %s",
            inserted,
            source_name or source_id or "unknown-source",
            source_location or "unknown-location",
            detection_date.isoformat(),
        )
    debug_logger.info(
        "persistence.summary",
        extra={
            "wav_id": wav_id,
            "source_id": source_id,
            "inserted": inserted,
            "total_detections": len(detections),
        },
    )
    return inserted


def _determine_capture_datetime(analysis: AnalyzeResult, wav_path: Path) -> datetime:
    try:
        stat = wav_path.stat()
        return datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    except (FileNotFoundError, OSError):
        dt = analysis.timestamp
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)


def _ensure_species(
    session,
    detection: DetectionResult,
    species_enricher: Optional[SpeciesEnricher],
) -> Optional[str]:
    scientific = (detection.scientific_name or detection.label or "").strip()
    if not scientific:
        return None

    if species_enricher is not None:
        try:
            result = species_enricher.ensure_species(
                scientific,
                common_name=detection.common_name or detection.label,
            )
            return result.species_id
        except SpeciesEnrichmentError as exc:
            logger.warning(
                "Species enrichment failed for '%s': %s",
                scientific,
                exc,
            )
            debug_logger.warning(
                "persistence.enrichment_failed",
                extra={"scientific_name": scientific, "reason": str(exc)},
            )

    species_id = crud.generate_species_id(scientific)
    payload = {
        "id": species_id,
        "sci_name": scientific,
        "common_name": detection.common_name or detection.label,
    }
    crud.upsert_species(session, payload)
    debug_logger.debug(
        "persistence.species_upserted",
        extra={"species_id": species_id, "scientific_name": scientific},
    )
    return species_id
