from __future__ import annotations

import json
import os
import mimetypes
import logging
from collections import OrderedDict
from datetime import date as date_cls, datetime, time as time_cls, timezone, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
from fastapi.responses import FileResponse, JSONResponse
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
from lib.schemas import (
    CitationEntry,
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
    SpeciesDetections,
    SpeciesDetailResponse,
    SpeciesImage,
    SpeciesPreview,
    TaxonomyDetail,
)


PROJECT_ROOT = Path(__file__).resolve().parent
_config_override = os.getenv("BIRDSONG_CONFIG")
CONFIG_PATH = Path(_config_override) if _config_override else PROJECT_ROOT / "config.yaml"
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
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().isoformat()


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


def _build_recording_url(request: Request, wav_id: Optional[str]) -> Optional[str]:
    if not wav_id:
        return None
    try:
        return str(request.url_for("get_recording_file", wav_id=wav_id))
    except NoMatchFound:
        return None


def _combine_datetime(row: Dict[str, Any]) -> Optional[datetime]:
    row_date = row.get("date")
    row_time = row.get("time")
    if row_date is None and row_time is None:
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
        url=_build_recording_url(request, row["wav_id"]),
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


@app.post("/ears")
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

    file_bytes = await wav.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty"
        )

    destination_path.write_bytes(file_bytes)
    await wav.close()
    debug_logger.debug(
        "ears.file_stored",
        extra={
            "microphone_id": microphone.microphone_id,
            "path": str(destination_path),
            "bytes": len(file_bytes),
        },
    )

    lat_value = _parse_optional_float(latitude, "latitude") or microphone.latitude
    lon_value = _parse_optional_float(longitude, "longitude") or microphone.longitude

    try:
        analysis = analyzer.analyze(
            destination_path,
            latitude=lat_value,
            longitude=lon_value,
            stream_id=microphone.microphone_id,
        )
    except Exception as exc:  # noqa: BLE001 - return clean error to client
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {exc}",
        ) from exc
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

    detection_payload = [
        {
            "common_name": detection.common_name,
            "scientific_name": detection.scientific_name,
            "label": detection.label,
            "confidence": detection.confidence,
            "start_time": detection.start_time,
            "end_time": detection.end_time,
            "location_hint": (
                "predicted"
                if detection.is_predicted_for_location
                else "unverified"
                if detection.is_predicted_for_location is not None
                else "unknown"
            ),
        }
        for detection in analysis.detections
    ]

    stored_path: Optional[str] = str(destination_path)
    if not analysis.detections:
        try:
            destination_path.unlink(missing_ok=True)
            stored_path = None
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
                source_name=name or microphone.display_name or microphone.location or microphone.microphone_id,
                source_display_name=microphone.display_name or name or microphone.location,
                source_location=microphone.location,
                species_enricher=species_enricher,
                species_id_map=species_id_map,
            )
            debug_logger.info(
                "ears.persistence_complete",
                extra={
                    "microphone_id": microphone.microphone_id,
                    "inserted": inserted,
                    "path": str(destination_path),
                },
            )
        except Exception as exc:  # noqa: BLE001 - log and continue response
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

    return {
        "microphone_id": microphone.microphone_id,
        "name": name or microphone.location,
        "stored_path": stored_path,
        "analyzed_at": analysis.timestamp.astimezone().isoformat(),
        "duration_seconds": analysis.duration_seconds,
        "sample_rate": analysis.frame_rate,
        "channels": analysis.channels,
        "detections": detection_payload,
    }


@app.get("/detections", response_model=DetectionFeedResponse)
def list_detections(
    request: Request,
    date: Optional[str] = Query(None, description="Filter detections by date (YYYY-MM-DD)"),
    species_id: Optional[str] = Query(None, description="Filter by species identifier"),
    min_confidence: Optional[float] = Query(
        None, ge=0.0, le=1.0, description="Filter by minimum confidence (0-1 range)"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> DetectionFeedResponse:
    _, resources, *_ = _ensure_state(request)
    target_date = _parse_date_param(date, "date")

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
                species.c.id.label("species_id"),
                species.c.common_name.label("species_common_name"),
                species.c.sci_name.label("species_scientific_name"),
                species.c.genus,
                species.c.family,
                species.c.image_url,
                species.c.info_url,
                species.c.summary,
                recordings.c.path.label("recording_path"),
                recordings.c.source_id.label("recording_source_id"),
                recordings.c.source_name.label("recording_source_name"),
                recordings.c.source_display_name.label("recording_source_display_name"),
                recordings.c.source_location.label("recording_source_location"),
            )
            .join(species, idents.c.species_id == species.c.id)
            .join(recordings, idents.c.wav_id == recordings.c.wav_id, isouter=True)
        )

        conditions = []
        if target_date is not None:
            conditions.append(idents.c.date == target_date)
        if species_id:
            conditions.append(species.c.id == species_id)
        if min_confidence is not None:
            conditions.append(idents.c.confidence >= min_confidence)

        if conditions:
            stmt = stmt.where(and_(*conditions))

        stmt = stmt.order_by(idents.c.date.desc(), idents.c.time.desc().nullslast())

        rows = session.execute(stmt).mappings().all()

        species_ids = [
            row["species_id"]
            for row in rows
            if row.get("species_id")
        ]
        image_attributions = _load_image_attributions(session, species_ids)
    finally:
        session.close()

    total_detections = len(rows)
    unique_species = len({row["species_id"] for row in rows if row.get("species_id")})

    detection_times: List[datetime] = []
    for row in rows:
        if row["date"] is not None:
            if row["time"] is not None:
                composite = datetime.combine(row["date"], row["time"])
            else:
                composite = datetime.combine(row["date"], time_cls(0, 0))
            detection_times.append(composite)

    first_detection = (
        detection_times[-1].strftime("%H:%M:%S") if detection_times else None
    )
    last_detection = (
        detection_times[0].strftime("%H:%M:%S") if detection_times else None
    )

    offset = (page - 1) * page_size
    paged_rows = rows[offset : offset + page_size]

    detection_models: List[DetectionItem] = []
    device_index = resources.get("device_index", []) if isinstance(resources, dict) else []

    for row in paged_rows:
        detection_item, _ = _build_detection_item(
            row,
            image_attributions,
            device_index,
            request,
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
    "/recordings/{wav_id}",
    name="get_recording_file",
    summary="Download a stored recording by its identifier.",
)
def get_recording_file(wav_id: str) -> FileResponse:
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

    file_path = Path(raw_path).expanduser()
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording file not found on disk",
        )

    media_type, _ = mimetypes.guess_type(file_path.name)
    return FileResponse(file_path, media_type=media_type or "audio/wav")


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
        class_=species_row.get("class"),
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
