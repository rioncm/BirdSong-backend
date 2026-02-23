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
from lib.object_storage import (
    RecordingStorageConfig,
    S3RecordingStore,
    SUPPORTED_PLAYBACK_FORMATS,
    build_object_key,
    transcode_audio_for_playback,
)


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
    recording_storage: Optional[S3RecordingStore] = None,
    recording_storage_config: Optional[RecordingStorageConfig] = None,
) -> int:
    """
    Store analyzer detections, guaranteeing related day and recording rows exist.
    Returns the number of new detection rows inserted.
    """
    if not detections:
        return 0

    wav_path = Path(analysis.input_file)
    wav_id = wav_path.stem or wav_path.name
    persisted_path = _prepare_recording_path(
        wav_path=wav_path,
        wav_id=wav_id,
        source_id=source_id or analysis.stream_id,
        recording_storage=recording_storage,
        recording_storage_config=recording_storage_config,
    )

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
            persisted_path,
            duration_seconds=analysis.duration_seconds,
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
                crud.update_species_detection_stats(session, species_id, capture_dt)
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


def _prepare_recording_path(
    *,
    wav_path: Path,
    wav_id: str,
    source_id: Optional[str],
    recording_storage: Optional[S3RecordingStore],
    recording_storage_config: Optional[RecordingStorageConfig],
) -> str:
    if (
        recording_storage is None
        or recording_storage_config is None
        or not recording_storage_config.enabled
    ):
        return str(wav_path)

    playback_format = recording_storage_config.normalized_playback_format
    playback_content_type = SUPPORTED_PLAYBACK_FORMATS.get(playback_format)
    source_segment = source_id or "unknown-source"
    transcoded_path: Optional[Path] = None

    try:
        local_playback_path = wav_path
        if playback_format != "wav":
            local_playback_path = transcode_audio_for_playback(
                wav_path,
                output_format=playback_format,
            )
            transcoded_path = local_playback_path

        playback_key = build_object_key(
            recording_storage_config.prefix,
            category="playback",
            wav_id=wav_id,
            source_id=source_segment,
            extension=playback_format,
        )
        persisted_path = recording_storage.upload_file(
            local_playback_path,
            playback_key,
            content_type=playback_content_type,
        )

        if recording_storage_config.keep_wav_copy and playback_format != "wav":
            raw_key = build_object_key(
                recording_storage_config.prefix,
                category="raw",
                wav_id=wav_id,
                source_id=source_segment,
                extension="wav",
            )
            recording_storage.upload_file(
                wav_path,
                raw_key,
                content_type=SUPPORTED_PLAYBACK_FORMATS["wav"],
            )

        if recording_storage_config.delete_local_after_upload:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to delete local recording %s", wav_path, exc_info=True)

        return persisted_path
    except Exception:  # noqa: BLE001 - fallback to local path if storage/transcode fails
        logger.exception("Failed to persist recording '%s' to object storage; keeping local path", wav_path)
        return str(wav_path)
    finally:
        if transcoded_path is not None and transcoded_path != wav_path:
            try:
                transcoded_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to remove temporary transcoded file %s",
                    transcoded_path,
                    exc_info=True,
                )


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
