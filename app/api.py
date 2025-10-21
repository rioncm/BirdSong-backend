from __future__ import annotations

import json
import logging
from datetime import date as date_cls, datetime, time as time_cls, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from sqlalchemy import and_, func, select

import asyncio
from lib.analyzer import BaseAnalyzer
from lib.clients import WikimediaClient
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
from lib.setup import initialize_environment
from .schemas import (
    CitationEntry,
    DayActuals,
    DayForecast,
    DayOverviewResponse,
    DetectionFeedResponse,
    DetectionItem,
    DetectionSummary,
    RecordingPreview,
    SpeciesDetections,
    SpeciesDetailResponse,
    SpeciesImage,
    SpeciesPreview,
    TaxonomyDetail,
)


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
API_KEY_HEADER = "X-API-Key"

app = FastAPI(title="BirdSong Ingest API", version="1.0.0")
logger = logging.getLogger("birdsong.api")


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

    wikimedia_headers = headers_map.get("Wikimedia Commons", {})
    wikimedia_user_agent = user_agent_map.get("Wikimedia Commons") or wikimedia_headers.get("User-Agent")

    species_enricher = SpeciesEnricher(
        wikimedia_client=WikimediaClient(user_agent=wikimedia_user_agent)
    )

    alerts_config = app_config.birdsong.alerts

    storage_paths: Dict[str, Path] = resources.get("storage_paths", {}) if isinstance(resources.get("storage_paths"), dict) else {}
    temp_path = storage_paths.get("temp_path") or storage_paths.get("base_path") or (PROJECT_ROOT / "data" / "temp")
    temp_path = Path(temp_path)
    temp_path.mkdir(parents=True, exist_ok=True)
    summary_storage_path = temp_path / "alerts_summary.json"

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

    lat_value = _parse_optional_float(latitude, "latitude") or microphone.latitude
    lon_value = _parse_optional_float(longitude, "longitude") or microphone.longitude

    try:
        analysis = analyzer.analyze(
            destination_path,
            latitude=lat_value,
            longitude=lon_value,
            camera_id=microphone.microphone_id,
        )
    except Exception as exc:  # noqa: BLE001 - return clean error to client
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {exc}",
        ) from exc

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

    return {
        "microphone_id": microphone.microphone_id,
        "name": name or microphone.location,
        "stored_path": str(destination_path),
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
    _ensure_state(request)
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
                species.c.ai_summary,
                recordings.c.path.label("recording_path"),
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
    finally:
        session.close()

    total_detections = len(rows)
    unique_species = len({row["species_id"] for row in rows})

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
    for row in paged_rows:
        if row["date"] is not None and row["time"] is not None:
            recorded_at_dt = datetime.combine(row["date"], row["time"]).replace(tzinfo=timezone.utc)
            recorded_at_value = recorded_at_dt.isoformat()
        elif row["date"] is not None:
            recorded_at_value = datetime.combine(row["date"], time_cls(0, 0)).replace(
                tzinfo=timezone.utc
            ).isoformat()
        else:
            recorded_at_value = None

        detection_models.append(
            DetectionItem(
                id=row["id"],
                recorded_at=recorded_at_value,
                device_name=None,
                confidence=row["confidence"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                species=SpeciesPreview(
                    id=row["species_id"],
                    common_name=row["species_common_name"] or row["ident_common_name"],
                    scientific_name=row["species_scientific_name"] or row["ident_scientific_name"],
                    genus=row["genus"],
                    family=row["family"],
                    image_url=row["image_url"],
                    summary=row["ai_summary"],
                    info_url=row["info_url"],
                ),
                recording=RecordingPreview(
                    wav_id=row["wav_id"],
                    path=row["recording_path"],
                ),
                location_hint="unknown",
            )
        )

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


@app.get("/species/{species_id}", response_model=SpeciesDetailResponse)
def get_species_detail(request: Request, species_id: str) -> SpeciesDetailResponse:
    _ensure_state(request)
    session = get_session()
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
    finally:
        session.close()

    first_seen = detection_stats["first_date"].isoformat() if detection_stats and detection_stats["first_date"] else None
    last_seen = detection_stats["last_date"].isoformat() if detection_stats and detection_stats["last_date"] else None
    total_count = int(detection_stats["total"]) if detection_stats else 0

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
        summary=species_row.get("ai_summary"),
        image=SpeciesImage(
            url=species_row.get("image_url"),
            source_url=species_row.get("info_url"),
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
