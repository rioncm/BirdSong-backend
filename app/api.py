from __future__ import annotations

import json
import mimetypes
import logging
import subprocess
import tempfile
from collections import OrderedDict
from datetime import date as date_cls, datetime, time as time_cls, timezone, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

import yaml
from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    Query,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import NoMatchFound
from sqlalchemy import and_, func, or_, select

import asyncio
from lib.analyzer import BaseAnalyzer
from lib.clients import WikimediaClient
from lib.clients.ebird import EbirdClient
from lib.alerts import AlertEngine, AlertEvent
from lib.notifications import NotificationService
from lib.notifications.scheduler import SummaryScheduler
from lib.config import AppConfig, MicrophoneConfig
from lib.config_path import resolve_config_path
from lib.data import crud
from lib.data.db import get_session
from lib.data.tables import (
    data_citations,
    data_sources,
    days as days_table,
    idents,
    recordings,
    species,
)
from lib.enrichment import SpeciesEnricher, SpeciesEnrichmentError
from lib.logging_utils import setup_debug_logging
from lib.persistence import persist_analysis_results
from lib.setup import initialize_environment
from lib.noaa_scheduler import NoaaUpdateScheduler
from lib.object_storage import (
    RecordingStorageConfig,
    S3RecordingStore,
    SUPPORTED_PLAYBACK_FORMATS,
    create_s3_recording_store,
    guess_media_type,
    is_s3_uri,
    parse_s3_uri,
)
from lib.playback_proxy import (
    PlaybackServiceConfig,
    build_playback_service_url,
    normalize_playback_filter,
    normalize_playback_format,
)
from lib.schemas import (
    CitationEntry,
    DataComparisonResponse,
    DayActuals,
    DayForecast,
    DayOverviewResponse,
    DetectionFeedResponse,
    DetectionItem,
    DetectionSummary,
    DetectionTimelineResponse,
    TimelineBucket,
    QuarterPresetsResponse,
    QuarterWindow,
    RecordingPreview,
    RecordingMetadataResponse,
    SpeciesDetections,
    SpeciesDetailResponse,
    SpeciesImage,
    SpeciesPreview,
    StatsMetricWindow,
    StatsOverviewResponse,
    StatsWindow,
    TaxonomyDetail,
)
from lib.stats import TimeWindow, fetch_data_comparison, fetch_overview_stats, resolve_time_window


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = resolve_config_path(PROJECT_ROOT)
API_KEY_HEADER = "X-API-Key"

app = FastAPI(title="BirdSong Ingest API", version="1.0.0")
setup_debug_logging(PROJECT_ROOT)
debug_logger = logging.getLogger("birdsong.debug.api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger = logging.getLogger("birdsong.api")


_ERROR_CODE_MAP = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "validation_error",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "server_error",
}

PLAYBACK_FILTERS: Dict[str, Optional[str]] = {
    "none": None,
    "enhanced": (
        "highpass=f=140,"
        "lowpass=f=9800,"
        "afftdn=nf=-24,"
        "acompressor=threshold=-20dB:ratio=2.2:attack=8:release=120,"
        "alimiter=limit=0.95"
    ),
}


def _parse_optional_float(value: Optional[str], field: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field} value: {value}",
        ) from exc


def _parse_date_param(value: Optional[str], field: str) -> Optional[date_cls]:
    if value in (None, "", "null"):
        return None
    try:
        return date_cls.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field} value: {value}",
        ) from exc


def _format_time(value: Optional[time_cls]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%H:%M:%S")


def _format_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _format_datetime_utc(value)


def _format_datetime_utc(value: datetime) -> str:
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    else:
        normalized = normalized.astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


def _parse_iso_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field} value: {value}",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def _status_to_error_code(status_code: int) -> str:
    return _ERROR_CODE_MAP.get(status_code, f"http_{status_code}")


def _validate_audio_file(file_path: Path) -> Tuple[bool, Optional[str]]:
    """
    Validate that an audio file can be read.
    Returns (is_valid, error_message).
    """
    if not file_path.exists():
        return False, "File does not exist"
    
    if file_path.stat().st_size == 0:
        return False, "File is empty (0 bytes)"
    
    # Use ffprobe to validate the audio file
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path)
            ],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode != 0:
            error_output = result.stderr.strip() if result.stderr else "Unknown error"
            return False, f"Invalid audio format: {error_output}"
        
        # Check if we got a valid duration
        try:
            duration = float(result.stdout.strip())
            if duration <= 0:
                return False, "Audio file has zero duration"
        except (ValueError, AttributeError):
            return False, "Could not determine audio duration"
            
        return True, None
        
    except subprocess.TimeoutExpired:
        return False, "Audio validation timed out"
    except Exception as exc:
        return False, f"Validation error: {str(exc)}"


def _delete_file_best_effort(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to delete temporary file: %s", path, exc_info=True)


def _build_error_payload(status_code: int, detail: Any) -> Dict[str, Any]:
    code = _status_to_error_code(status_code)
    message: Optional[str] = None
    extra: Optional[Any] = None

    if isinstance(detail, dict):
        code = str(detail.get("code") or code)
        message = detail.get("message") or detail.get("detail")
        remaining = {k: v for k, v in detail.items() if k not in {"code", "message", "detail"}}
        if remaining:
            extra = remaining
    elif isinstance(detail, list):
        extra = detail
    elif detail:
        message = str(detail)

    if message is None:
        try:
            message = HTTPStatus(status_code).phrase
        except ValueError:
            message = "Request failed"

    payload: Dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
        }
    }
    if extra is not None:
        payload["error"]["details"] = extra
    return payload


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    payload = _build_error_payload(exc.status_code, exc.detail)
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    detail = {
        "code": "validation_error",
        "message": "Request validation failed",
        "fields": exc.errors(),
    }
    payload = _build_error_payload(status.HTTP_422_UNPROCESSABLE_ENTITY, detail)
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=payload)


def _floor_to_bucket(moment: datetime, bucket_minutes: int) -> datetime:
    bucket_minutes = max(bucket_minutes, 1)
    total_minutes = moment.hour * 60 + moment.minute
    bucket_index = total_minutes // bucket_minutes
    bucket_start_minutes = bucket_index * bucket_minutes
    hour = bucket_start_minutes // 60
    minute = bucket_start_minutes % 60
    return moment.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _resolve_device_metadata(
    recording_path: Optional[str],
    device_index: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not recording_path or not device_index:
        return None

    raw_path = str(recording_path)
    path_obj = Path(raw_path)
    for entry in device_index:
        entry_path = entry.get("path")
        if not entry_path:
            continue
        entry_path_obj = Path(entry_path)
        try:
            path_obj.relative_to(entry_path_obj)
            return dict(entry)
        except ValueError:
            pass
        if raw_path.startswith(str(entry_path_obj)):
            return dict(entry)
    return None


def _coerce_citation_content(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            return {"credit": raw}
        return {}
    return {}


def _load_image_attributions(session, species_ids: List[str]) -> Dict[str, Dict[str, Optional[str]]]:
    if not species_ids:
        return {}
    stmt = (
        select(
            data_citations.c.species_id,
            data_citations.c.content,
        )
        .where(
            data_citations.c.species_id.in_(species_ids),
            data_citations.c.data_type == "image",
        )
        .order_by(
            data_citations.c.species_id,
            data_citations.c.updated_date.desc().nullslast(),
            data_citations.c.created_date.desc(),
        )
    )
    results: Dict[str, Dict[str, Optional[str]]] = {}
    for row in session.execute(stmt).mappings():
        species_id_value = row["species_id"]
        if species_id_value in results:
            continue
        payload = _coerce_citation_content(row["content"])
        results[species_id_value] = {
            "attribution": payload.get("credit") or payload.get("attribution"),
            "source_url": payload.get("source_url") or payload.get("page_url") or payload.get("url"),
            "thumbnail_url": payload.get("thumbnail_url"),
            "license": payload.get("license") or payload.get("license_code"),
            "image_url": payload.get("image_url") or payload.get("url"),
        }
    return results


def _build_quarter_windows(target_date: date_cls) -> List[QuarterWindow]:
    quarters: List[QuarterWindow] = []
    offsets = (
        ("Q1", time_cls(hour=0)),
        ("Q2", time_cls(hour=6)),
        ("Q3", time_cls(hour=12)),
        ("Q4", time_cls(hour=18)),
    )
    for index, (label, start_time) in enumerate(offsets):
        start_dt = datetime.combine(target_date, start_time, tzinfo=timezone.utc)
        if index + 1 < len(offsets):
            end_time = offsets[index + 1][1]
            end_dt = datetime.combine(target_date, end_time, tzinfo=timezone.utc)
        else:
            end_dt = datetime.combine(
                target_date + timedelta(days=1),
                time_cls(hour=0),
                tzinfo=timezone.utc,
            )
        quarters.append(
            QuarterWindow(
                label=label,
                start=start_dt.isoformat(),
                end=end_dt.isoformat(),
            )
        )
    return quarters


def _resolve_playback_service_config(request: Request) -> PlaybackServiceConfig:
    app_obj = getattr(request, "app", None)
    app_state = getattr(app_obj, "state", None)
    if app_state is None:
        return PlaybackServiceConfig()

    candidate = getattr(app_state, "playback_service_config", None)
    if isinstance(candidate, PlaybackServiceConfig):
        return candidate
    if candidate is None:
        return PlaybackServiceConfig()
    return PlaybackServiceConfig(
        enabled=bool(getattr(candidate, "enabled", False)),
        base_url=getattr(candidate, "base_url", None),
        default_filter=str(getattr(candidate, "default_filter", "none")),
        default_format=str(getattr(candidate, "default_format", "mp3")),
    )


def _supported_media_type_for_format(output_format: str) -> str:
    return SUPPORTED_PLAYBACK_FORMATS.get(output_format, "audio/mpeg")


def _path_format(path_or_key: str) -> Optional[str]:
    suffix = Path(path_or_key).suffix.lower().lstrip(".")
    if suffix in SUPPORTED_PLAYBACK_FORMATS:
        return suffix
    guessed, _ = mimetypes.guess_type(path_or_key)
    for fmt, media_type in SUPPORTED_PLAYBACK_FORMATS.items():
        if guessed == media_type:
            return fmt
    return None


def _build_playback_query_params(
    *,
    playback_filter: Optional[str] = None,
    output_format: Optional[str] = None,
) -> Dict[str, str]:
    query: Dict[str, str] = {}
    if playback_filter is not None:
        normalized_filter = normalize_playback_filter(playback_filter)
        if normalized_filter != "none":
            query["filter"] = normalized_filter
    if output_format is not None:
        query["format"] = normalize_playback_format(output_format)
    return query


def _build_recording_url(
    request: Request,
    wav_id: Optional[str],
    *,
    playback_filter: Optional[str] = None,
    output_format: Optional[str] = None,
) -> Optional[str]:
    if not wav_id:
        return None

    playback_service_config = _resolve_playback_service_config(request)
    delegated = build_playback_service_url(
        playback_service_config,
        wav_id,
        playback_filter=playback_filter,
        output_format=output_format,
    )
    if delegated:
        return delegated

    try:
        url = str(request.url_for("get_recording_file", wav_id=wav_id))
    except NoMatchFound:
        return None

    query = _build_playback_query_params(
        playback_filter=playback_filter,
        output_format=output_format,
    )
    if query:
        url = f"{url}?{urlencode(query)}"
    return url


def _build_recording_meta_url(
    request: Request,
    wav_id: Optional[str],
    *,
    playback_filter: Optional[str] = None,
    output_format: Optional[str] = None,
) -> Optional[str]:
    if not wav_id:
        return None
    try:
        url = str(request.url_for("get_recording_metadata", wav_id=wav_id))
    except NoMatchFound:
        return None

    query = _build_playback_query_params(
        playback_filter=playback_filter,
        output_format=output_format,
    )
    if query:
        url = f"{url}?{urlencode(query)}"
    return url


def _resolve_audio_media_type(path_or_key: str, fallback: str = "audio/wav") -> str:
    guessed = guess_media_type(path_or_key)
    if guessed.startswith("audio/"):
        return guessed
    guessed_stdlib, _ = mimetypes.guess_type(path_or_key)
    if guessed_stdlib and guessed_stdlib.startswith("audio/"):
        return guessed_stdlib
    return fallback


def _stream_s3_audio(
    storage: S3RecordingStore,
    *,
    bucket: str,
    key: str,
) -> StreamingResponse:
    try:
        response = storage.get_object(bucket, key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording file not found in object storage",
        ) from exc

    body = response["Body"]
    media_type = response.get("ContentType") or _resolve_audio_media_type(key)
    content_length = response.get("ContentLength")
    headers: Dict[str, str] = {}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)

    def _iter_chunks():
        try:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            body.close()

    return StreamingResponse(_iter_chunks(), media_type=media_type, headers=headers)


def _cleanup_temp(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to delete temporary playback file %s", path, exc_info=True)


def _materialize_s3_object(
    storage: S3RecordingStore,
    *,
    bucket: str,
    key: str,
) -> Path:
    response = storage.get_object(bucket, key)
    body = response["Body"]
    suffix = Path(key).suffix or ".audio"
    temp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    temp_path = Path(temp_file.name)
    try:
        with temp_file:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                if chunk:
                    temp_file.write(chunk)
    finally:
        body.close()

    if not temp_path.exists() or temp_path.stat().st_size == 0:
        _cleanup_temp(temp_path)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording content unavailable in object storage",
        )
    return temp_path


def _build_ffmpeg_command(
    source_path: Path,
    *,
    output_format: str,
    playback_filter: str,
) -> List[str]:
    cmd: List[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "48000",
    ]

    filter_graph = PLAYBACK_FILTERS.get(playback_filter)
    if filter_graph:
        cmd.extend(["-af", filter_graph])

    if output_format == "wav":
        cmd.extend(["-codec:a", "pcm_s16le", "-f", "wav", "pipe:1"])
    elif output_format == "ogg":
        cmd.extend(["-codec:a", "libvorbis", "-qscale:a", "5", "-f", "ogg", "pipe:1"])
    else:
        cmd.extend(["-codec:a", "libmp3lame", "-q:a", "3", "-f", "mp3", "pipe:1"])

    return cmd


def _stream_transcoded_audio(
    source_path: Path,
    *,
    output_format: str,
    playback_filter: str,
    cleanup_source: Optional[Path] = None,
) -> StreamingResponse:
    cmd = _build_ffmpeg_command(
        source_path,
        output_format=output_format,
        playback_filter=playback_filter,
    )
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:
        _cleanup_temp(cleanup_source)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Playback transcode process failed to start",
        )

    first_chunk = process.stdout.read(1024 * 64)
    if not first_chunk:
        stderr = process.stderr.read().decode("utf-8", errors="ignore").strip()
        return_code = process.wait()
        _cleanup_temp(cleanup_source)
        logger.warning(
            "ffmpeg playback transcode failed (return_code=%s): %s",
            return_code,
            stderr,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Playback transcode failed",
        )

    def _iter_chunks():
        try:
            yield first_chunk
            while True:
                chunk = process.stdout.read(1024 * 64)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                process.stdout.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                process.stderr.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
            _cleanup_temp(cleanup_source)

    headers = {"Cache-Control": "public, max-age=3600"}
    media_type = _supported_media_type_for_format(output_format)
    return StreamingResponse(_iter_chunks(), media_type=media_type, headers=headers)


def _combine_datetime(row: Dict[str, Any]) -> Optional[datetime]:
    row_date = row.get("date")
    row_time = row.get("time")
    if row_date is None and row_time is None:
        return None
    if row_date is None:
        return None
    if row_time is None:
        combined = datetime.combine(row_date, time_cls(0, 0))
    else:
        combined = datetime.combine(row_date, row_time)
    return combined.replace(tzinfo=timezone.utc)


def _build_detection_item(
    row: Dict[str, Any],
    attribution_map: Dict[str, Dict[str, Optional[str]]],
    device_index: Sequence[Dict[str, Any]],
    request: Request,
    playback_filter: Optional[str] = None,
    output_format: Optional[str] = None,
) -> Tuple[DetectionItem, Optional[datetime]]:
    recorded_at_dt = _combine_datetime(row)
    recorded_at_value = recorded_at_dt.isoformat() if recorded_at_dt else None

    species_id_value = row["species_id"]
    attrib = attribution_map.get(species_id_value, {})

    device_info = _resolve_device_metadata(
        row.get("recording_path"),
        list(device_index) if device_index else [],
    )

    device_id = row.get("recording_source_id")
    device_name = row.get("recording_source_name")
    device_display_name = row.get("recording_source_display_name")

    if device_info:
        device_id = device_id or device_info.get("id")
        device_name = device_name or device_info.get("name") or device_info.get("id")
        device_display_name = (
            device_display_name
            or device_info.get("display_name")
            or device_name
            or device_id
        )

    device_display_name = device_display_name or device_name or device_id
    device_name = device_name or device_id

    recording_preview = RecordingPreview(
        wav_id=row["wav_id"],
        path=row["recording_path"],
        url=_build_recording_url(
            request,
            row["wav_id"],
            playback_filter=playback_filter,
            output_format=output_format,
        ),
        meta_url=_build_recording_meta_url(
            request,
            row["wav_id"],
            playback_filter=playback_filter,
            output_format=output_format,
        ),
        duration_seconds=row.get("recording_duration_seconds"),
    )

    species_preview = SpeciesPreview(
        id=species_id_value,
        common_name=row["species_common_name"] or row["ident_common_name"],
        scientific_name=row["species_scientific_name"] or row["ident_scientific_name"],
        genus=row["genus"],
        family=row["family"],
        image_url=attrib.get("image_url") or row["image_url"],
        image_thumbnail_url=attrib.get("thumbnail_url"),
        image_license=attrib.get("license"),
        image_attribution=attrib.get("attribution"),
        image_source_url=attrib.get("source_url"),
        summary=row["summary"],
        info_url=row["info_url"],
    )

    detection_item = DetectionItem(
        id=row["id"],
        recorded_at=recorded_at_value,
        device_id=device_id,
        device_name=device_name,
        device_display_name=device_display_name,
        confidence=row["confidence"],
        start_time=row["start_time"],
        end_time=row["end_time"],
        species=species_preview,
        recording=recording_preview,
    )
    return detection_item, recorded_at_dt


def _group_detections_into_buckets(
    rows: Sequence[Dict[str, Any]],
    attribution_map: Dict[str, Dict[str, Optional[str]]],
    device_index: Sequence[Dict[str, Any]],
    request: Request,
    bucket_minutes: int,
    playback_filter: Optional[str] = None,
    output_format: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[datetime]]:
    bucket_map: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    all_datetimes: List[datetime] = []

    items_with_time: List[Tuple[Optional[datetime], DetectionItem]] = []
    for row in rows:
        detection_item, detected_at = _build_detection_item(
            row,
            attribution_map,
            device_index,
            request,
            playback_filter=playback_filter,
            output_format=output_format,
        )
        items_with_time.append((detected_at, detection_item))
        if detected_at is not None:
            all_datetimes.append(detected_at)

    items_with_time.sort(key=lambda entry: entry[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    for detected_at, detection in items_with_time:
        if detected_at is None:
            bucket_key = "unspecified"
            bucket_start_iso = "unspecified"
            bucket_end_iso = "unspecified"
        else:
            bucket_start = _floor_to_bucket(detected_at, bucket_minutes)
            bucket_end = bucket_start + timedelta(minutes=bucket_minutes)
            bucket_key = bucket_start.isoformat()
            bucket_start_iso = bucket_start.isoformat()
            bucket_end_iso = bucket_end.isoformat()

        bucket_entry = bucket_map.get(bucket_key)
        if bucket_entry is None:
            bucket_entry = {
                "bucket_start": bucket_start_iso,
                "bucket_end": bucket_end_iso,
                "detections": [],
                "species_ids": set(),
                "datetimes": [],
            }
            bucket_map[bucket_key] = bucket_entry

        bucket_entry["detections"].append((detection, detected_at))
        bucket_entry["species_ids"].add(detection.species.id)
        if detected_at is not None:
            bucket_entry["datetimes"].append(detected_at)

    buckets: List[Dict[str, Any]] = []
    for entry in bucket_map.values():
        raw_detection_count = len(entry["detections"])
        species_groups: Dict[str, Dict[str, Any]] = {}
        for detection, detected_at in entry["detections"]:
            key = detection.species.id
            group = species_groups.get(key)
            if group is None:
                group = {
                    "count": 0,
                    "latest_dt": detected_at,
                    "latest_detection": detection,
                    "top_confidence": detection.confidence,
                }
                species_groups[key] = group

            group["count"] += 1
            if detected_at is not None:
                if group["latest_dt"] is None or detected_at > group["latest_dt"]:
                    group["latest_dt"] = detected_at
                    group["latest_detection"] = detection
            if detection.confidence is not None:
                if group["top_confidence"] is None or detection.confidence > group["top_confidence"]:
                    group["top_confidence"] = detection.confidence

        aggregated_detections: List[DetectionItem] = []
        for group in species_groups.values():
            latest_detection: DetectionItem = group["latest_detection"]
            updated_detection = latest_detection.copy(
                update={
                    "recorded_at": group["latest_dt"].isoformat() if group["latest_dt"] else latest_detection.recorded_at,
                    "confidence": group["top_confidence"],
                    "detection_count": group["count"],
                }
            )
            aggregated_detections.append(updated_detection)

        aggregated_detections.sort(
            key=lambda item: datetime.fromisoformat(item.recorded_at)
            if item.recorded_at
            else datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        buckets.append(
            {
                "bucket_start": entry["bucket_start"],
                "bucket_end": entry["bucket_end"],
                "detections": aggregated_detections,
                "total_detections": raw_detection_count,
                "unique_species": len(species_groups),
                "datetimes": entry["datetimes"],
            }
        )

    return buckets, all_datetimes


def _find_microphone(config: AppConfig, microphone_id: str) -> Optional[MicrophoneConfig]:
    for name, microphone in config.birdsong.microphones.items():
        if microphone.microphone_id == microphone_id or name == microphone_id:
            return microphone
    return None


@app.on_event("startup")
async def startup_event() -> None:
    app_config, resources = initialize_environment(
        config_data=yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")),
        base_dir=PROJECT_ROOT,
    )

    analyzer = BaseAnalyzer(
        birdnet_config=app_config.birdsong.config,
        log_path=PROJECT_ROOT / "logs" / "analyzer.log",
    )

    headers_map: Dict[str, Dict[str, str]] = {}
    user_agent_map: Dict[str, Optional[str]] = {}
    raw_headers = resources.get("data_source_headers")
    if isinstance(raw_headers, dict):
        headers_map = {str(key): dict(value) for key, value in raw_headers.items() if isinstance(value, dict)}
    raw_user_agents = resources.get("data_source_user_agents")
    if isinstance(raw_user_agents, dict):
        user_agent_map = {str(key): value for key, value in raw_user_agents.items() if value}

    alerts_config = app_config.birdsong.alerts

    storage_paths: Dict[str, Path] = resources.get("storage_paths", {}) if isinstance(resources.get("storage_paths"), dict) else {}
    temp_path = storage_paths.get("temp_path") or storage_paths.get("base_path") or (PROJECT_ROOT / "data" / "temp")
    temp_path = Path(temp_path)
    temp_path.mkdir(parents=True, exist_ok=True)
    summary_storage_path = temp_path / "alerts_summary.json"

    wikimedia_headers = headers_map.get("Wikimedia Commons", {})
    wikimedia_user_agent = user_agent_map.get("Wikimedia Commons") or wikimedia_headers.get("User-Agent")

    ebird_client: Optional[EbirdClient] = None
    third_party_sources = resources.get("third_party_sources")
    if isinstance(third_party_sources, list):
        for entry in third_party_sources:
            if isinstance(entry, dict) and entry.get("name") == "eBird":
                api_key = entry.get("api_key")
                if api_key:
                    try:
                        ebird_client = EbirdClient(
                            api_key=api_key,
                            user_agent=user_agent_map.get("eBird") or headers_map.get("eBird", {}).get("User-Agent"),
                        )
                    except ValueError as exc:
                        logger.warning("Failed to initialize eBird client: %s", exc)
                break

    images_dir = storage_paths.get("images_path") or storage_paths.get("images")
    species_enricher = SpeciesEnricher(
        wikimedia_client=WikimediaClient(user_agent=wikimedia_user_agent),
        ebird_client=ebird_client,
        images_dir=images_dir,
    )

    notifications_config = resources.get("notifications_config") or {}
    notification_service: Optional[NotificationService]
    scheduler: Optional[SummaryScheduler]
    if notifications_config:
        notification_service = NotificationService(notifications_config, summary_storage_path)
        scheduler = SummaryScheduler(notification_service, notification_service.get_summary_schedule())
        scheduler.start()
    else:
        notification_service = None
        scheduler = None

    recording_storage_config_raw = resources.get("recording_storage_config")
    recording_storage_config = (
        recording_storage_config_raw
        if isinstance(recording_storage_config_raw, RecordingStorageConfig)
        else RecordingStorageConfig()
    )
    recording_storage: Optional[S3RecordingStore] = None
    if recording_storage_config.enabled:
        try:
            recording_storage = create_s3_recording_store(recording_storage_config)
            logger.info(
                "Recording object storage enabled (bucket=%s, endpoint=%s, playback_format=%s)",
                recording_storage_config.bucket,
                recording_storage_config.endpoint_url,
                recording_storage_config.normalized_playback_format,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to initialize recording object storage: %s", exc)
            recording_storage = None

    noaa_scheduler = NoaaUpdateScheduler(app_config, resources)
    noaa_scheduler.start()

    playback_service_config_raw = resources.get("playback_service_config")
    playback_service_config = (
        playback_service_config_raw
        if isinstance(playback_service_config_raw, PlaybackServiceConfig)
        else PlaybackServiceConfig()
    )
    if playback_service_config.enabled and playback_service_config.base_url:
        logger.info(
            "Playback service delegation enabled (base_url=%s, default_filter=%s, default_format=%s)",
            playback_service_config.base_url,
            playback_service_config.normalized_filter,
            playback_service_config.normalized_format,
        )

    def _publish_alert(event: AlertEvent) -> None:
        logger.info("Alert emitted", extra={"alert": event.to_dict()})
        if notification_service:
            notification_service.handle_alert(event)

    alert_engine = AlertEngine(alerts_config, _publish_alert) if alerts_config else None

    app.state.app_config = app_config
    app.state.resources = resources
    app.state.analyzer = analyzer
    app.state.species_enricher = species_enricher
    app.state.alert_engine = alert_engine
    app.state.notification_service = notification_service
    app.state.summary_scheduler = scheduler
    app.state.noaa_scheduler = noaa_scheduler
    app.state.recording_storage = recording_storage
    app.state.recording_storage_config = recording_storage_config
    app.state.playback_service_config = playback_service_config


@app.on_event("shutdown")
async def shutdown_event() -> None:
    enricher: SpeciesEnricher | None = getattr(app.state, "species_enricher", None)
    if enricher is not None:
        enricher.close()
    scheduler: SummaryScheduler | None = getattr(app.state, "summary_scheduler", None)
    if scheduler is not None:
        await scheduler.stop()
    notification_service: NotificationService | None = getattr(app.state, "notification_service", None)
    if notification_service is not None:
        notification_service.flush_summaries()
        notification_service.close()
    noaa_scheduler: NoaaUpdateScheduler | None = getattr(app.state, "noaa_scheduler", None)
    if noaa_scheduler is not None:
        await noaa_scheduler.stop()


def _ensure_state(
    request: Request,
) -> tuple[AppConfig, Dict[str, object], BaseAnalyzer, SpeciesEnricher, Optional[AlertEngine]]:
    if not hasattr(request.app.state, "app_config") or not hasattr(
        request.app.state, "resources"
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Application not initialized",
        )

    app_config: AppConfig = request.app.state.app_config
    resources: Dict[str, object] = request.app.state.resources
    analyzer: BaseAnalyzer = request.app.state.analyzer
    species_enricher: SpeciesEnricher = request.app.state.species_enricher
    alert_engine: Optional[AlertEngine] = getattr(request.app.state, "alert_engine", None)

    return app_config, resources, analyzer, species_enricher, alert_engine


async def _process_microphone_audio_background(
    analyzer: BaseAnalyzer,
    species_enricher: SpeciesEnricher,
    alert_engine: Optional[AlertEngine],
    recording_storage: Optional[S3RecordingStore],
    recording_storage_config: RecordingStorageConfig,
    microphone: MicrophoneConfig,
    destination_path: Path,
    latitude: Optional[float],
    longitude: Optional[float],
    friendly_name: Optional[str],
) -> None:
    try:
        await asyncio.to_thread(
            _process_microphone_audio_sync,
            analyzer,
            species_enricher,
            alert_engine,
            recording_storage,
            recording_storage_config,
            microphone,
            destination_path,
            latitude,
            longitude,
            friendly_name,
        )
    except Exception:  # noqa: BLE001 - never bubble background failures to clients
        logger.exception(
            "Background ingest task crashed",
            extra={"microphone_id": microphone.microphone_id, "path": str(destination_path)},
        )


def _process_microphone_audio_sync(
    analyzer: BaseAnalyzer,
    species_enricher: SpeciesEnricher,
    alert_engine: Optional[AlertEngine],
    recording_storage: Optional[S3RecordingStore],
    recording_storage_config: RecordingStorageConfig,
    microphone: MicrophoneConfig,
    destination_path: Path,
    latitude: Optional[float],
    longitude: Optional[float],
    friendly_name: Optional[str],
) -> None:
    try:
        analysis = analyzer.analyze(
            destination_path,
            latitude=latitude,
            longitude=longitude,
            stream_id=microphone.microphone_id,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort processing
        # Get file info for debugging
        file_exists = destination_path.exists()
        file_size = destination_path.stat().st_size if file_exists else 0
        
        # Check if it's an audio format error
        error_msg = str(exc)
        if "AudioFormatError" in type(exc).__name__ or "audio read error" in error_msg.lower():
            logger.error(
                "Audio format error for microphone '%s': %s. File: %s, Size: %d bytes, Exists: %s. "
                "This may indicate a corrupted upload, unsupported format, or missing codec.",
                microphone.microphone_id,
                exc,
                destination_path,
                file_size,
                file_exists,
            )
        else:
            logger.exception(
                "Analysis failed for microphone '%s': %s. File: %s, Size: %d bytes, Exists: %s",
                microphone.microphone_id,
                exc,
                destination_path,
                file_size,
                file_exists,
            )
        return

    debug_logger.debug(
        "ears.analysis_complete",
        extra={
            "microphone_id": microphone.microphone_id,
            "detections": len(analysis.detections),
            "duration": analysis.duration_seconds,
        },
    )

    unique_species = {}
    for detection in analysis.detections:
        scientific = (detection.scientific_name or "").strip()
        if not scientific:
            continue
        key = scientific.lower()
        if key not in unique_species:
            unique_species[key] = detection

    species_id_map: Dict[str, str] = {}
    for detection in unique_species.values():
        try:
            result = species_enricher.ensure_species(
                detection.scientific_name,
                common_name=detection.common_name or detection.label,
            )
            if result.created:
                logger.info(
                    "Enriched species '%s' via GBIF/Wikimedia (species_id=%s)",
                    detection.scientific_name or detection.common_name,
                    result.species_id,
                )
            if detection.scientific_name:
                species_id_map[detection.scientific_name.strip().lower()] = result.species_id
        except SpeciesEnrichmentError as exc:
            logger.warning(
                "Species enrichment failed for '%s': %s",
                detection.scientific_name or detection.common_name or "unknown",
                exc,
            )

    if alert_engine is not None:
        for detection in analysis.detections:
            species_key = (detection.scientific_name or "").strip().lower()
            alert_engine.process_detection(
                {
                    "scientific_name": detection.scientific_name,
                    "common_name": detection.common_name,
                    "species_id": species_id_map.get(species_key),
                    "confidence": detection.confidence,
                    "start_time": detection.start_time,
                    "end_time": detection.end_time,
                    "recording_path": str(destination_path),
                    "location": microphone.location,
                }
            )
        debug_logger.debug(
            "ears.alerts_dispatched",
            extra={
                "microphone_id": microphone.microphone_id,
                "detections": len(analysis.detections),
            },
        )

    if not analysis.detections:
        try:
            destination_path.unlink(missing_ok=True)
            debug_logger.debug(
                "ears.cleanup_deleted",
                extra={"microphone_id": microphone.microphone_id, "path": str(destination_path)},
            )
        except OSError as cleanup_exc:
            debug_logger.warning(
                "ears.cleanup_failed",
                extra={
                    "microphone_id": microphone.microphone_id,
                    "path": str(destination_path),
                    "error": str(cleanup_exc),
                },
            )

    if analysis.detections:
        try:
            inserted = persist_analysis_results(
                analysis,
                analysis.detections,
                source_id=microphone.microphone_id,
                source_name=friendly_name
                or microphone.display_name
                or microphone.location
                or microphone.microphone_id,
                source_display_name=microphone.display_name or friendly_name or microphone.location,
                source_location=microphone.location,
                species_enricher=species_enricher,
                species_id_map=species_id_map,
                recording_storage=recording_storage,
                recording_storage_config=recording_storage_config,
            )
            debug_logger.info(
                "ears.persistence_complete",
                extra={
                    "microphone_id": microphone.microphone_id,
                    "inserted": inserted,
                    "path": str(destination_path),
                },
            )
        except Exception as exc:  # noqa: BLE001 - log and continue
            logger.warning(
                "Failed to persist detections for microphone '%s': %s",
                microphone.microphone_id,
                exc,
            )
            debug_logger.exception(
                "ears.persistence_error",
                extra={
                    "microphone_id": microphone.microphone_id,
                    "path": str(destination_path),
                },
            )


def _log_ingest_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception:
        logger.exception("Background ingest task failed with unhandled exception")


@app.post("/remote/upload", description="Ingest audio file from remote microphone")
async def ingest_microphone_audio(
    request: Request,
    microphone_id_form: Optional[str] = Form(None, alias="id"),
    microphone_id_alt: Optional[str] = Form(None, alias="microphone_id"),
    name: Optional[str] = Form(None),
    latitude: Optional[str] = Form(None),
    longitude: Optional[str] = Form(None),
    wav: UploadFile = File(..., description="WAV file captured by the remote microphone"),
    api_key: str = Header(..., alias=API_KEY_HEADER),
):
    app_config, resources, analyzer, species_enricher, alert_engine = _ensure_state(request)

    microphone_id = microphone_id_alt or microphone_id_form
    if not microphone_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing microphone id",
        )

    microphone = _find_microphone(app_config, microphone_id)
    if microphone is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown microphone id '{microphone_id}'",
        )

    if api_key != microphone.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    debug_logger.info(
        "ears.request",
        extra={
            "microphone_id": microphone.microphone_id,
            "friendly_name": name,
            "latitude": latitude,
            "longitude": longitude,
        },
    )

    mic_output_paths_obj = resources.get("microphone_output_paths")
    if isinstance(mic_output_paths_obj, dict):
        mic_output_paths = mic_output_paths_obj
    else:
        mic_output_paths = {}
        resources["microphone_output_paths"] = mic_output_paths

    destination_dir_obj = mic_output_paths.get(microphone.microphone_id)
    if destination_dir_obj is None:
        destination_dir = Path(microphone.output_folder)
    else:
        destination_dir = Path(destination_dir_obj)
    destination_dir.mkdir(parents=True, exist_ok=True)
    mic_output_paths[microphone.microphone_id] = destination_dir

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    extension = Path(wav.filename or "").suffix.lower()
    if extension not in {".wav", ".wave"}:
        extension = ".wav"

    destination_path = Path(destination_dir) / f"{timestamp}{extension}"

    bytes_written = 0
    try:
        with destination_path.open("wb") as destination_file:
            while True:
                chunk = await wav.read(1024 * 1024)
                if not chunk:
                    break
                destination_file.write(chunk)
                bytes_written += len(chunk)
    except OSError as exc:
        logger.error(
            "Failed to write uploaded file for microphone '%s' (%s): %s",
            microphone.microphone_id,
            destination_path,
            exc,
        )
        _delete_file_best_effort(destination_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save audio file",
        ) from exc
    finally:
        await wav.close()

    if bytes_written == 0:
        _delete_file_best_effort(destination_path)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    if not destination_path.exists():
        logger.error("File write failed - file does not exist: %s", destination_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save audio file",
        )

    actual_size = destination_path.stat().st_size
    if actual_size == 0:
        _delete_file_best_effort(destination_path)
        logger.error("File write failed - zero bytes: %s", destination_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Audio file is empty",
        )

    if actual_size != bytes_written:
        logger.warning(
            "File size mismatch - streamed: %d, written: %d, path: %s",
            bytes_written,
            actual_size,
            destination_path,
        )

    # Validate audio file format before processing
    is_valid, error_msg = _validate_audio_file(destination_path)
    if not is_valid:
        _delete_file_best_effort(destination_path)
        logger.error(
            "Invalid audio file uploaded by microphone '%s': %s. File: %s, Size: %d",
            microphone.microphone_id,
            error_msg,
            destination_path,
            actual_size
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid audio file: {error_msg}"
        )
    
    debug_logger.debug(
        "ears.file_stored",
        extra={
            "microphone_id": microphone.microphone_id,
            "path": str(destination_path),
            "bytes": bytes_written,
        },
    )

    lat_value = _parse_optional_float(latitude, "latitude") or microphone.latitude
    lon_value = _parse_optional_float(longitude, "longitude") or microphone.longitude
    recording_storage: Optional[S3RecordingStore] = getattr(request.app.state, "recording_storage", None)
    recording_storage_config: RecordingStorageConfig = getattr(
        request.app.state,
        "recording_storage_config",
        RecordingStorageConfig(),
    )

    background_task = asyncio.create_task(
        _process_microphone_audio_background(
            analyzer,
            species_enricher,
            alert_engine,
            recording_storage,
            recording_storage_config,
            microphone,
            destination_path,
            lat_value,
            lon_value,
            name,
        )
    )
    background_task.add_done_callback(_log_ingest_task_result)

    return JSONResponse({"status": "accepted"})


@app.get("/detections", response_model=DetectionFeedResponse)
def list_detections(
    request: Request,
    date: Optional[str] = Query(None, description="Filter detections by date (YYYY-MM-DD)"),
    species_id: Optional[str] = Query(None, description="Filter by species identifier"),
    min_confidence: Optional[float] = Query(
        None, ge=0.0, le=1.0, description="Filter by minimum confidence (0-1 range)"
    ),
    playback_filter: Optional[str] = Query(
        None,
        description="Optional playback filter to include in recording URLs (none or enhanced).",
    ),
    playback_format: Optional[str] = Query(
        None,
        description="Optional playback format to include in recording URLs (wav, mp3, ogg).",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> DetectionFeedResponse:
    _, resources, *_ = _ensure_state(request)
    target_date = _parse_date_param(date, "date")

    conditions = []
    if target_date is not None:
        conditions.append(idents.c.date == target_date)
    if species_id:
        conditions.append(species.c.id == species_id)
    if min_confidence is not None:
        conditions.append(idents.c.confidence >= min_confidence)

    offset = (page - 1) * page_size
    session = get_session()
    try:
        base_from_clause = (
            idents.join(species, idents.c.species_id == species.c.id)
            .join(recordings, idents.c.wav_id == recordings.c.wav_id, isouter=True)
        )

        data_stmt = (
            select(
                idents.c.id,
                idents.c.date,
                idents.c.time,
                idents.c.common_name.label("ident_common_name"),
                idents.c.sci_name.label("ident_scientific_name"),
                idents.c.confidence,
                idents.c.start_time,
                idents.c.end_time,
                idents.c.wav_id,
                species.c.id.label("species_id"),
                species.c.common_name.label("species_common_name"),
                species.c.sci_name.label("species_scientific_name"),
                species.c.genus,
                species.c.family,
                species.c.image_url,
                species.c.info_url,
                species.c.summary,
                recordings.c.path.label("recording_path"),
                recordings.c.duration_seconds.label("recording_duration_seconds"),
                recordings.c.source_id.label("recording_source_id"),
                recordings.c.source_name.label("recording_source_name"),
                recordings.c.source_display_name.label("recording_source_display_name"),
                recordings.c.source_location.label("recording_source_location"),
            )
            .select_from(base_from_clause)
        )

        if conditions:
            data_stmt = data_stmt.where(and_(*conditions))

        data_stmt = (
            data_stmt
            .order_by(idents.c.date.desc(), idents.c.time.desc().nullslast())
            .limit(page_size)
            .offset(offset)
        )
        rows = session.execute(data_stmt).mappings().all()

        total_stmt = select(func.count(idents.c.id)).select_from(
            idents.join(species, idents.c.species_id == species.c.id)
        )
        unique_species_stmt = select(func.count(func.distinct(idents.c.species_id))).select_from(
            idents.join(species, idents.c.species_id == species.c.id)
        )
        latest_detection_stmt = select(idents.c.date, idents.c.time).select_from(
            idents.join(species, idents.c.species_id == species.c.id)
        )
        earliest_detection_stmt = select(idents.c.date, idents.c.time).select_from(
            idents.join(species, idents.c.species_id == species.c.id)
        )

        if conditions:
            condition_expr = and_(*conditions)
            total_stmt = total_stmt.where(condition_expr)
            unique_species_stmt = unique_species_stmt.where(condition_expr)
            latest_detection_stmt = latest_detection_stmt.where(condition_expr)
            earliest_detection_stmt = earliest_detection_stmt.where(condition_expr)

        total_detections = int(session.execute(total_stmt).scalar_one())
        unique_species = int(session.execute(unique_species_stmt).scalar_one())
        latest_detection_row = session.execute(
            latest_detection_stmt.order_by(
                idents.c.date.desc(),
                idents.c.time.desc().nullslast(),
            ).limit(1)
        ).mappings().first()
        earliest_detection_row = session.execute(
            earliest_detection_stmt.order_by(
                idents.c.date.asc(),
                idents.c.time.asc().nullsfirst(),
            ).limit(1)
        ).mappings().first()

        species_ids = [row["species_id"] for row in rows if row.get("species_id")]
        image_attributions = _load_image_attributions(session, species_ids)
    finally:
        session.close()

    def _format_detection_time(row: Optional[Dict[str, Any]]) -> Optional[str]:
        if not row or row.get("date") is None:
            return None
        detection_time = row.get("time") or time_cls(0, 0)
        return detection_time.strftime("%H:%M:%S")

    first_detection = _format_detection_time(earliest_detection_row)
    last_detection = _format_detection_time(latest_detection_row)

    detection_models: List[DetectionItem] = []
    device_index = resources.get("device_index", []) if isinstance(resources, dict) else []

    for row in rows:
        detection_item, _ = _build_detection_item(
            row,
            image_attributions,
            device_index,
            request,
            playback_filter=playback_filter,
            output_format=playback_format,
        )
        detection_models.append(detection_item)

    summary = DetectionSummary(
        total_detections=total_detections,
        unique_species=unique_species,
        first_detection=first_detection,
        last_detection=last_detection,
        page=page,
        page_size=page_size,
    )

    result_date = target_date.isoformat() if target_date else None

    return DetectionFeedResponse(
        date=result_date,
        summary=summary,
        detections=detection_models,
    )


@app.get(
    "/detections/timeline",
    response_model=DetectionTimelineResponse,
    summary="Fetch detections grouped into fixed time buckets",
)
def timeline_detections(
    request: Request,
    before: Optional[str] = Query(
        None,
        description="Return buckets strictly before this ISO timestamp (UTC).",
    ),
    after: Optional[str] = Query(
        None,
        description="Return buckets strictly after this ISO timestamp (UTC).",
    ),
    limit: int = Query(
        24,
        ge=1,
        le=288,
        description="Maximum number of buckets to return.",
    ),
    bucket_minutes: int = Query(
        5,
        ge=1,
        le=120,
        description="Bucket size in minutes.",
    ),
    playback_filter: Optional[str] = Query(
        None,
        description="Optional playback filter to include in recording URLs (none or enhanced).",
    ),
    playback_format: Optional[str] = Query(
        None,
        description="Optional playback format to include in recording URLs (wav, mp3, ogg).",
    ),
) -> DetectionTimelineResponse:
    _, resources, *_ = _ensure_state(request)

    if before and after:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Specify only one of 'before' or 'after'.",
        )

    before_dt = _parse_iso_timestamp(before, "before") if before else None
    after_dt = _parse_iso_timestamp(after, "after") if after else None

    time_string_expr = func.coalesce(func.strftime("%H:%M:%S", idents.c.time), "00:00:00")
    timestamp_expr = func.datetime(idents.c.date, time_string_expr)

    fetch_limit = max(limit * 20, 200)

    session = get_session()
    try:
        stmt = (
            select(
                idents.c.id,
                idents.c.date,
                idents.c.time,
                idents.c.common_name.label("ident_common_name"),
                idents.c.sci_name.label("ident_scientific_name"),
                idents.c.confidence,
                idents.c.start_time,
                idents.c.end_time,
                idents.c.wav_id,
                recordings.c.path.label("recording_path"),
                recordings.c.duration_seconds.label("recording_duration_seconds"),
                recordings.c.source_id.label("recording_source_id"),
                recordings.c.source_name.label("recording_source_name"),
                recordings.c.source_display_name.label("recording_source_display_name"),
                recordings.c.source_location.label("recording_source_location"),
                species.c.id.label("species_id"),
                species.c.common_name.label("species_common_name"),
                species.c.sci_name.label("species_scientific_name"),
                species.c.genus,
                species.c.family,
                species.c.image_url,
                species.c.info_url,
                species.c.summary,
                timestamp_expr.label("detected_ts"),
            )
            .join(species, idents.c.species_id == species.c.id)
            .join(recordings, idents.c.wav_id == recordings.c.wav_id, isouter=True)
        )

        if before_dt:
            before_str = before_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            stmt = stmt.where(timestamp_expr < before_str)
        if after_dt:
            after_str = after_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            stmt = stmt.where(timestamp_expr > after_str)

        if after_dt:
            stmt = stmt.order_by(idents.c.date.asc(), idents.c.time.asc().nullsfirst())
        else:
            stmt = stmt.order_by(idents.c.date.desc(), idents.c.time.desc().nullslast())

        rows = session.execute(stmt.limit(fetch_limit)).mappings().all()

        if after_dt:
            rows = list(reversed(rows))

        species_ids = [row["species_id"] for row in rows if row.get("species_id")]
        image_attributions = _load_image_attributions(session, species_ids)
    finally:
        session.close()

    device_index = resources.get("device_index", []) if isinstance(resources, dict) else []

    buckets_raw, all_datetimes = _group_detections_into_buckets(
        rows,
        image_attributions,
        device_index,
        request,
        bucket_minutes,
        playback_filter=playback_filter,
        output_format=playback_format,
    )

    has_more = len(buckets_raw) > limit
    if has_more:
        buckets_raw = buckets_raw[:limit]

    next_cursor = None
    previous_cursor = None

    if buckets_raw:
        oldest_times: List[datetime] = []
        newest_times: List[datetime] = []
        for idx, bucket in enumerate(buckets_raw):
            bucket_times = bucket.get("datetimes") or []
            if not bucket_times:
                continue
            if idx == 0:
                newest_times.extend(bucket_times)
            oldest_times.extend(bucket_times)
        if oldest_times:
            oldest_dt = min(oldest_times)
            next_cursor = oldest_dt.isoformat()
        if newest_times:
            newest_dt = max(newest_times)
            previous_cursor = newest_dt.isoformat()

    buckets_response: List[TimelineBucket] = []
    for bucket in buckets_raw:
        bucket.pop("datetimes", None)
        detections = bucket["detections"]
        buckets_response.append(
            TimelineBucket(
                bucket_start=bucket["bucket_start"],
                bucket_end=bucket["bucket_end"],
                total_detections=bucket["total_detections"],
                unique_species=bucket["unique_species"],
                detections=detections,
            )
        )

    return DetectionTimelineResponse(
        bucket_minutes=bucket_minutes,
        has_more=has_more,
        next_cursor=next_cursor,
        previous_cursor=previous_cursor,
        buckets=buckets_response,
    )


@app.get(
    "/stats/overview",
    response_model=StatsOverviewResponse,
    summary="Dashboard overview metrics and top lists.",
)
def get_stats_overview(
    request: Request,
    start: Optional[str] = Query(
        None,
        description="ISO-8601 UTC start timestamp.",
    ),
    end: Optional[str] = Query(
        None,
        description="ISO-8601 UTC end timestamp.",
    ),
    window: Optional[str] = Query(
        None,
        description="Duration shorthand (e.g. '24h', '7d'). Ignored when both start and end are provided.",
    ),
) -> StatsOverviewResponse:
    _, resources, *_ = _ensure_state(request)
    device_index_raw = resources.get("device_index") if isinstance(resources, dict) else []
    device_index: Sequence[Dict[str, Any]]
    if isinstance(device_index_raw, list):
        device_index = [entry for entry in device_index_raw if isinstance(entry, dict)]
    else:
        device_index = []

    try:
        resolved_window = resolve_time_window(start=start, end=end, window=window)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    now_utc = datetime.now(timezone.utc)
    session = get_session()
    try:
        overview_metrics = fetch_overview_stats(
            session,
            resolved_window,
            device_index=device_index,
            top_species_limit=5,
            top_hours_limit=5,
            top_streams_limit=5,
        )
    finally:
        session.close()

    return StatsOverviewResponse(
        generated_at=_format_datetime_utc(now_utc),
        window=StatsWindow(
            start=_format_datetime_utc(resolved_window.start),
            end=_format_datetime_utc(resolved_window.end),
        ),
        detections_total=int(overview_metrics["detections_total"]),
        unique_species=int(overview_metrics["unique_species"]),
        active_devices=int(overview_metrics["active_devices"]),
        avg_confidence=float(overview_metrics["avg_confidence"]),
        top_species=overview_metrics["top_species"],
        top_hours=overview_metrics["top_hours"],
        top_streams=overview_metrics["top_streams"],
    )


@app.get(
    "/stats/data-comparison",
    response_model=DataComparisonResponse,
    summary="Compare a metric against a prior period.",
)
def get_stats_data_comparison(
    request: Request,
    metric: str = Query(
        ...,
        description="Metric identifier (detections_total, unique_species, avg_confidence, active_devices).",
    ),
    comparison: str = Query(
        ...,
        description="Comparison selector ('prior_range', 'prior_month', 'prior_year').",
    ),
    start: Optional[str] = Query(
        None,
        description="ISO-8601 UTC start timestamp for the primary window.",
    ),
    end: Optional[str] = Query(
        None,
        description="ISO-8601 UTC end timestamp for the primary window.",
    ),
    window: Optional[str] = Query(
        None,
        description="Duration shorthand applied when explicit start/end are omitted.",
    ),
    species_id: Optional[str] = Query(
        None,
        description="Optional species identifier to scope the metric.",
    ),
    device_id: Optional[str] = Query(
        None,
        description="Optional device identifier to scope the metric.",
    ),
) -> DataComparisonResponse:
    _ensure_state(request)
    try:
        resolved_window = resolve_time_window(start=start, end=end, window=window)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    session = get_session()
    try:
        comparison_payload = fetch_data_comparison(
            session,
            metric=metric,
            primary_window=resolved_window,
            selector=comparison,
            species_id=species_id,
            device_id=device_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    finally:
        session.close()

    comparison_window: TimeWindow = comparison_payload["comparison_window"]
    now_utc = datetime.now(timezone.utc)

    metrics_as_int = {"detections_total", "unique_species", "active_devices"}
    primary_value_raw = comparison_payload["primary_value"]
    comparison_value_raw = comparison_payload["comparison_value"]

    if metric in metrics_as_int:
        primary_value = int(round(primary_value_raw))
        comparison_value = int(round(comparison_value_raw))
        absolute_change = primary_value - comparison_value
        percent_change = None
        if comparison_value != 0:
            percent_change = (absolute_change / comparison_value) * 100.0
    else:
        primary_value = float(primary_value_raw)
        comparison_value = float(comparison_value_raw)
        absolute_change = float(comparison_payload["absolute_change"])
        percent_change = comparison_payload["percent_change"]
        if percent_change is not None:
            percent_change = float(percent_change)

    return DataComparisonResponse(
        generated_at=_format_datetime_utc(now_utc),
        metric=metric,
        primary_window=StatsMetricWindow(
            start=_format_datetime_utc(resolved_window.start),
            end=_format_datetime_utc(resolved_window.end),
            value=primary_value,
        ),
        comparison_window=StatsMetricWindow(
            start=_format_datetime_utc(comparison_window.start),
            end=_format_datetime_utc(comparison_window.end),
            value=comparison_value,
            selector=comparison_payload["comparison_selector"],
        ),
        absolute_change=absolute_change,
        percent_change=percent_change,
    )


@app.get(
    "/health",
    summary="Simple readiness probe.",
)
def health_check(request: Request) -> Dict[str, Any]:
    _ensure_state(request)
    now = datetime.now(timezone.utc)
    return {"status": "ok", "timestamp": now.isoformat()}


@app.get(
    "/detections/quarters",
    response_model=QuarterPresetsResponse,
    summary="List the four standard quarter-day windows for a given date.",
)
def list_quarter_presets(
    request: Request,
    date: Optional[str] = Query(
        None,
        description="Date in YYYY-MM-DD (defaults to current UTC date).",
    ),
) -> QuarterPresetsResponse:
    _ensure_state(request)
    target_date = _parse_date_param(date, "date") or datetime.now(timezone.utc).date()

    quarters = _build_quarter_windows(target_date)

    current_label: Optional[str] = None
    now_utc = datetime.now(timezone.utc)
    if target_date < now_utc.date():
        current_label = "Q4"
    elif target_date > now_utc.date():
        current_label = quarters[0].label if quarters else None
    else:
        for window in quarters:
            start_dt = datetime.fromisoformat(window.start)
            end_dt = datetime.fromisoformat(window.end)
            if start_dt <= now_utc < end_dt:
                current_label = window.label
                break
        if current_label is None and quarters:
            current_label = quarters[-1].label

    return QuarterPresetsResponse(
        date=target_date.isoformat(),
        current_label=current_label,
        quarters=quarters,
    )


@app.get(
    "/recordings/{wav_id}/meta",
    response_model=RecordingMetadataResponse,
    name="get_recording_metadata",
    summary="Return recording metadata and playback URL.",
)
def get_recording_metadata(
    request: Request,
    wav_id: str,
    playback_filter: Optional[str] = Query(
        None,
        alias="filter",
        description="Playback filter for generated URL (none or enhanced).",
    ),
    output_format: Optional[str] = Query(
        None,
        alias="format",
        description="Playback output format for generated URL (wav, mp3, ogg).",
    ),
) -> RecordingMetadataResponse:
    session = get_session()
    try:
        row = (
            session.execute(
                select(
                    recordings.c.path,
                    recordings.c.duration_seconds,
                    recordings.c.source_id,
                    recordings.c.source_name,
                    recordings.c.source_display_name,
                    recordings.c.source_location,
                ).where(recordings.c.wav_id == wav_id)
            )
            .mappings()
            .first()
        )
    finally:
        session.close()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording not found",
        )

    raw_path = row["path"]
    if not raw_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording path unavailable",
        )

    media_type = _resolve_audio_media_type(raw_path)
    resolved_path = raw_path
    source_format = _path_format(raw_path)
    if is_s3_uri(raw_path):
        recording_storage: Optional[S3RecordingStore] = getattr(
            request.app.state,
            "recording_storage",
            None,
        )
        if recording_storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Object storage is not configured",
            )
        try:
            bucket, key = parse_s3_uri(raw_path)
            object_head = recording_storage.head_object(bucket, key)
            media_type = object_head.get("ContentType") or _resolve_audio_media_type(key)
            source_format = _path_format(key)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Recording file not found in object storage",
            ) from exc
    else:
        file_path = Path(raw_path).expanduser()
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Recording file not found on disk",
            )
        resolved_path = str(file_path)
        media_type = _resolve_audio_media_type(file_path.name)
        source_format = _path_format(file_path.name)

    if output_format is not None:
        media_type = _supported_media_type_for_format(normalize_playback_format(output_format))
    elif source_format in SUPPORTED_PLAYBACK_FORMATS:
        media_type = _supported_media_type_for_format(source_format)

    playback_url = _build_recording_url(
        request,
        wav_id,
        playback_filter=playback_filter,
        output_format=output_format,
    )
    if not playback_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to build playback URL",
        )

    return RecordingMetadataResponse(
        wav_id=wav_id,
        path=resolved_path,
        url=playback_url,
        media_type=media_type,
        duration_seconds=row.get("duration_seconds"),
        source_id=row.get("source_id"),
        source_name=row.get("source_name"),
        source_display_name=row.get("source_display_name"),
        source_location=row.get("source_location"),
    )


@app.get(
    "/recordings/{wav_id}",
    name="get_recording_file",
    summary="Download a stored recording by its identifier.",
    response_model=None,
)
def get_recording_file(
    request: Request,
    wav_id: str,
    playback_filter: Optional[str] = Query(
        None,
        alias="filter",
        description="Playback filter: none (default) or enhanced.",
    ),
    output_format: Optional[str] = Query(
        None,
        alias="format",
        description="Output format: wav, mp3, or ogg.",
    ),
) -> StreamingResponse | FileResponse:
    session = get_session()
    try:
        row = (
            session.execute(
                select(recordings.c.path).where(recordings.c.wav_id == wav_id)
            )
            .mappings()
            .first()
        )
    finally:
        session.close()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording not found",
        )

    raw_path = row["path"]
    if not raw_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording path unavailable",
        )

    selected_filter = normalize_playback_filter(playback_filter or "none")
    selected_format_override = (
        normalize_playback_format(output_format) if output_format is not None else None
    )
    playback_config = _resolve_playback_service_config(request)

    if is_s3_uri(raw_path):
        recording_storage: Optional[S3RecordingStore] = getattr(
            request.app.state,
            "recording_storage",
            None,
        )
        if recording_storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Object storage is not configured",
            )

        try:
            bucket, key = parse_s3_uri(raw_path)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Stored recording path is invalid",
            ) from exc

        source_format = _path_format(key)
        selected_format = (
            selected_format_override
            or source_format
            or playback_config.normalized_format
        )
        if selected_filter == "none" and selected_format_override is None and source_format is None:
            return _stream_s3_audio(recording_storage, bucket=bucket, key=key)
        if selected_filter == "none" and source_format == selected_format:
            return _stream_s3_audio(recording_storage, bucket=bucket, key=key)

        source_path = _materialize_s3_object(recording_storage, bucket=bucket, key=key)
        return _stream_transcoded_audio(
            source_path,
            output_format=selected_format,
            playback_filter=selected_filter,
            cleanup_source=source_path,
        )

    file_path = Path(raw_path).expanduser()
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording file not found on disk",
        )

    source_format = _path_format(file_path.name)
    selected_format = (
        selected_format_override
        or source_format
        or playback_config.normalized_format
    )
    if selected_filter == "none" and selected_format_override is None and source_format is None:
        media_type = _resolve_audio_media_type(file_path.name)
        return FileResponse(file_path, media_type=media_type)
    if selected_filter == "none" and source_format == selected_format:
        media_type = _supported_media_type_for_format(selected_format)
        return FileResponse(file_path, media_type=media_type)

    return _stream_transcoded_audio(
        file_path,
        output_format=selected_format,
        playback_filter=selected_filter,
    )


@app.get(
    "/images/{image_name}",
    summary="Serve cached species imagery.",
)
def get_species_image(request: Request, image_name: str) -> FileResponse:
    _, resources, *_ = _ensure_state(request)
    storage_paths = resources.get("storage_paths") if isinstance(resources, dict) else None
    if not isinstance(storage_paths, dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    images_dir = storage_paths.get("images_path") or storage_paths.get("images")
    if not images_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    file_path = Path(images_dir) / image_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    media_type, _ = mimetypes.guess_type(file_path.name)
    return FileResponse(file_path, media_type=media_type or "image/jpeg")


@app.get("/species/{species_id}", response_model=SpeciesDetailResponse)
def get_species_detail(request: Request, species_id: str) -> SpeciesDetailResponse:
    _ensure_state(request)
    session = get_session()
    image_details_map: Dict[str, Dict[str, Optional[str]]] = {}
    try:
        species_row = session.execute(
            select(species).where(species.c.id == species_id)
        ).mappings().first()
        if species_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Species not found")

        citations = session.execute(
            select(
                data_citations.c.data_type,
                data_citations.c.content,
                data_citations.c.updated_date,
                data_sources.c.name.label("source_name"),
            )
            .join(data_sources, data_citations.c.source_id == data_sources.c.id)
            .where(data_citations.c.species_id == species_id)
        ).mappings().all()

        detection_stats = session.execute(
            select(
                func.min(idents.c.date).label("first_date"),
                func.max(idents.c.date).label("last_date"),
                func.count(idents.c.id).label("total"),
            ).where(idents.c.species_id == species_id)
        ).mappings().first()

        image_details_map = _load_image_attributions(session, [species_id])
    finally:
        session.close()

    first_seen = detection_stats["first_date"].isoformat() if detection_stats and detection_stats["first_date"] else None
    last_seen = detection_stats["last_date"].isoformat() if detection_stats and detection_stats["last_date"] else None
    total_count = int(detection_stats["total"]) if detection_stats else 0

    image_details = image_details_map.get(species_id, {}) if image_details_map else {}
    image_url = image_details.get("image_url") or species_row.get("image_url")
    image_source_url = image_details.get("source_url") or species_row.get("info_url")

    parsed_citations: List[Dict[str, Any]] = []
    for citation in citations:
        content = citation["content"]
        parsed_content: Any
        try:
            parsed_content = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            parsed_content = content
        parsed_citations.append(
            {
                "source_name": citation["source_name"],
                "data_type": citation["data_type"],
                "content": parsed_content,
                "last_updated": _format_datetime(citation["updated_date"]),
            }
        )

    taxonomy = TaxonomyDetail(
        kingdom=species_row.get("kingdom"),
        phylum=species_row.get("phylum"),
        **{"class": species_row.get("class")},
        order=species_row.get("order"),
        family=species_row.get("family"),
        genus=species_row.get("genus"),
    )

    return SpeciesDetailResponse(
        id=species_row["id"],
        common_name=species_row.get("common_name"),
        scientific_name=species_row.get("sci_name"),
        taxonomy=taxonomy,
        summary=species_row.get("summary"),
        image=SpeciesImage(
            url=image_url,
            thumbnail_url=image_details.get("thumbnail_url"),
            license=image_details.get("license"),
            attribution=image_details.get("attribution"),
            source_url=image_source_url,
        ),
        detections=SpeciesDetections(
            first_seen=first_seen,
            last_seen=last_seen,
            total_count=total_count,
        ),
        citations=[
            CitationEntry(**entry) for entry in parsed_citations
        ],
    )


@app.get("/days/{day}", response_model=DayOverviewResponse)
def get_day_overview(request: Request, day: str) -> DayOverviewResponse:
    _ensure_state(request)
    target_date = _parse_date_param(day, "day")
    if target_date is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or missing day parameter",
        )
    session = get_session()
    try:
        day_row = crud.get_day(session, target_date)
        if day_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Day not found")
    finally:
        session.close()

    forecast = DayForecast(
        high=day_row.get("forecast_high"),
        low=day_row.get("forecast_low"),
        rain_probability=day_row.get("forecast_rain"),
        issued_at=_format_datetime(day_row.get("forecast_issued_at")),
        source=day_row.get("forecast_source") or "NOAA NWS",
    )
    actual = DayActuals(
        high=day_row.get("actual_high"),
        low=day_row.get("actual_low"),
        rain_total=day_row.get("actual_rain"),
        updated_at=_format_datetime(day_row.get("actual_updated_at")),
        source=day_row.get("actual_source") or "NOAA NWS",
    )

    return DayOverviewResponse(
        date=target_date.isoformat(),
        season=day_row.get("season"),
        dawn=_format_time(day_row.get("dawn")),
        sunrise=_format_time(day_row.get("sunrise")),
        solar_noon=_format_time(day_row.get("solar_noon")),
        sunset=_format_time(day_row.get("sunset")),
        dusk=_format_time(day_row.get("dusk")),
        forecast=forecast,
        actual=actual,
    )
