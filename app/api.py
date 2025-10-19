from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import yaml
from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from lib.analyzer import BaseAnalyzer
from lib.config import AppConfig, MicrophoneConfig
from lib.setup import initialize_environment


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
API_KEY_HEADER = "X-API-Key"

app = FastAPI(title="BirdSong Ingest API", version="1.0.0")


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

    app.state.app_config = app_config
    app.state.resources = resources
    app.state.analyzer = analyzer


def _ensure_state(request: Request) -> tuple[AppConfig, Dict[str, object], BaseAnalyzer]:
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

    return app_config, resources, analyzer


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
    app_config, resources, analyzer = _ensure_state(request)

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
