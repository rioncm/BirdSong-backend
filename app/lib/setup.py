from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from lib.config import AppConfig


def _resolve_path(raw_path: str | Path, base_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def initialize_environment(
    config_data: Dict[str, Any],
    base_dir: str | Path = ".",
) -> Tuple[AppConfig, Dict[str, Any]]:
    if not isinstance(config_data, dict):
        raise TypeError("config_data must be a dictionary")

    birdsong_section = config_data.get("birdsong")
    if not isinstance(birdsong_section, dict):
        raise ValueError("config_data missing 'birdsong' section")

    base_dir_path = Path(base_dir).expanduser().resolve()

    database_section = dict(birdsong_section.get("database") or {})
    if not database_section:
        raise ValueError("birdsong configuration missing database section")

    database_engine = (
        database_section.get("type")
        or database_section.get("engine")
        or database_section.get('type"')
        or "sqlite"
    )
    database_name = str(database_section.get("name", "birdsong.db"))
    database_dir_raw = database_section.get("path", "data")
    database_dir_path = _resolve_path(database_dir_raw, base_dir_path)
    database_dir_path.mkdir(parents=True, exist_ok=True)

    normalized_database = {
        "type": database_engine,
        "name": database_name,
        "path": str(database_dir_path),
    }

    birdnet_section = dict(birdsong_section.get("config") or {})
    if not birdnet_section:
        raise ValueError("birdsong configuration missing model config section")

    model_path = None
    model_path_raw = birdnet_section.get("model_path")
    if model_path_raw:
        model_path = _resolve_path(model_path_raw, base_dir_path)
        model_path.parent.mkdir(parents=True, exist_ok=True)

    label_path = None
    label_path_raw = birdnet_section.get("label_path")
    if label_path_raw:
        label_path = _resolve_path(label_path_raw, base_dir_path)
        label_path.parent.mkdir(parents=True, exist_ok=True)

    species_list_path = None
    species_list_raw = birdnet_section.get("species_list_path")
    if species_list_raw:
        species_list_path = _resolve_path(species_list_raw, base_dir_path)
        species_list_path.parent.mkdir(parents=True, exist_ok=True)

    normalized_birdnet = {
        "model_path": str(model_path) if model_path else None,
        "label_path": str(label_path) if label_path else None,
        "species_list_path": str(species_list_path) if species_list_path else None,
        "sample_rate": int(birdnet_section.get("sample_rate", 48000)),
        "chunk_size": int(birdnet_section.get("chunk_size", 3)),
        "overlap": float(birdnet_section.get("overlap", 0.5)),
        "confidence_threshold": float(
            birdnet_section.get("confidence_threshold", 0.3)
        ),
        "top_n": int(birdnet_section.get("top_n", 5)),
        "sensitivity": float(birdnet_section.get("sensitivity", 1.0)),
        "return_all_detections": bool(
            birdnet_section.get("return_all_detections", False)
        ),
    }

    cameras_section = dict(birdsong_section.get("cameras") or {})
    camera_base_raw = cameras_section.pop("base_path", None)
    default_latitude = cameras_section.pop("default_latitude", None)
    default_longitude = cameras_section.pop("default_longitude", None)

    camera_base_path = None
    if camera_base_raw:
        camera_base_path = _resolve_path(camera_base_raw, base_dir_path)
        camera_base_path.mkdir(parents=True, exist_ok=True)

    camera_configs = {}
    camera_output_paths: Dict[str, Path] = {}
    camera_coordinates: Dict[str, Tuple[Any, Any]] = {}

    for camera_name, camera_details in cameras_section.items():
        if not isinstance(camera_details, dict):
            continue

        rtsp_url = camera_details.get("rtsp_url")
        if not rtsp_url:
            raise ValueError(f"Camera '{camera_name}' missing rtsp_url")

        record_time = int(camera_details.get("record_time", 0))
        output_folder = camera_details.get("output_folder") or camera_name
        location_label = (
            camera_details.get("location_name")
            or camera_details.get("location")
            or camera_name
        )
        camera_id = camera_details.get("camera_id") or camera_name

        if camera_base_path is not None:
            camera_output_path = _resolve_path(output_folder, camera_base_path)
        else:
            camera_output_path = _resolve_path(output_folder, base_dir_path)
        camera_output_path.mkdir(parents=True, exist_ok=True)

        camera_configs[camera_name] = {
            "camera_id": camera_id,
            "rtsp_url": rtsp_url,
            "record_time": record_time,
            "output_folder": str(camera_output_path),
            "location": location_label,
        }
        camera_output_paths[camera_name] = camera_output_path

        latitude = camera_details.get("latitude", camera_details.get("cam_latitude"))
        if latitude is None:
            latitude = default_latitude
        longitude = camera_details.get("longitude", camera_details.get("cam_longitude"))
        if longitude is None:
            longitude = default_longitude
        if latitude is not None and longitude is not None:
            camera_coordinates[camera_name] = (latitude, longitude)
        camera_configs[camera_name]["latitude"] = latitude
        camera_configs[camera_name]["longitude"] = longitude

    microphones_section = dict(birdsong_section.get("microphones") or {})
    mic_base_raw = microphones_section.pop("base_path", None)
    mic_default_latitude = microphones_section.pop("default_latitude", None)
    mic_default_longitude = microphones_section.pop("default_longitude", None)

    mic_base_path = None
    if mic_base_raw:
        mic_base_path = _resolve_path(mic_base_raw, base_dir_path)
        mic_base_path.mkdir(parents=True, exist_ok=True)

    microphone_configs = {}
    microphone_output_paths: Dict[str, Path] = {}

    for mic_name, mic_details in microphones_section.items():
        if not isinstance(mic_details, dict):
            continue

        microphone_id = (
            mic_details.get("microphone_id")
            or mic_details.get("id")
            or mic_name
        )
        if not microphone_id:
            raise ValueError(f"Microphone '{mic_name}' missing identifier")

        api_key = mic_details.get("api_key")
        if not api_key:
            raise ValueError(f"Microphone '{microphone_id}' missing api_key")

        output_folder = mic_details.get("output_folder") or mic_name
        location_label = (
            mic_details.get("location_name")
            or mic_details.get("location")
            or mic_name
        )

        if mic_base_path is not None:
            mic_output_path = _resolve_path(output_folder, mic_base_path)
        else:
            mic_output_path = _resolve_path(output_folder, base_dir_path)
        mic_output_path.mkdir(parents=True, exist_ok=True)

        latitude = mic_details.get("latitude")
        if latitude is None:
            latitude = mic_default_latitude
        longitude = mic_details.get("longitude")
        if longitude is None:
            longitude = mic_default_longitude

        microphone_configs[mic_name] = {
            "microphone_id": microphone_id,
            "output_folder": str(mic_output_path),
            "location": location_label,
            "api_key": api_key,
            "latitude": latitude,
            "longitude": longitude,
        }
        microphone_output_paths[str(microphone_id)] = mic_output_path

    normalized_config = {
        "birdsong": {
            "database": normalized_database,
            "config": normalized_birdnet,
            "cameras": camera_configs,
            "microphones": microphone_configs,
        }
    }

    app_config = AppConfig.from_dict(normalized_config)

    resources = {
        "database_dir": database_dir_path,
        "database_file": database_dir_path / database_name,
        "model_path": model_path,
        "label_path": label_path,
        "species_list_path": species_list_path,
        "camera_output_paths": camera_output_paths,
        "camera_coordinates": camera_coordinates,
        "microphone_output_paths": microphone_output_paths,
    }

    return app_config, resources
