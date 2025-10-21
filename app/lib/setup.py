from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sqlalchemy import insert, select, update, inspect, text
from sqlalchemy.engine import Engine

from lib.config import AppConfig
from lib.data.db import initialize_database
from lib.data.tables import data_sources


_ALLOWED_SOURCE_TYPES = {"image", "taxa", "copy", "ai", "weather"}
_SOURCE_TYPE_ALIASES = {
    "ai model": "ai",
    "ai_model": "ai",
    "model": "ai",
    "gbif": "taxa",
    "taxonomy": "taxa",
    "taxon": "taxa",
    "species": "taxa",
    "xenocanto": "taxa",
    "macaulay": "image",
    "macaulay library": "image",
    "media": "image",
    "photo": "image",
    "photos": "image",
    "image": "image",
    "wikimedia": "image",
    "wikipedia": "copy",
    "weather": "weather",
    "copy": "copy",
}


def _to_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "t", "yes", "y", "1"}:
            return True
        if normalized in {"false", "f", "no", "n", "0"}:
            return False
    return bool(value)


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    stringified = str(value).strip()
    return stringified or None


def _normalize_source_type(raw_value: Any) -> str:
    normalized = _clean_str(raw_value)
    if not normalized:
        return "copy"

    lowered = normalized.lower()
    if lowered in _ALLOWED_SOURCE_TYPES:
        return lowered
    return _SOURCE_TYPE_ALIASES.get(lowered, "copy")


def _parse_headers(raw_headers: Any) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if isinstance(raw_headers, dict):
        for key, value in raw_headers.items():
            key_clean = _clean_str(key)
            value_clean = _clean_str(value)
            if key_clean and value_clean:
                headers[key_clean] = value_clean.strip('"')
    elif isinstance(raw_headers, (list, tuple)):
        for item in raw_headers:
            if not isinstance(item, str):
                continue
            line = item.strip().strip('"')
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key_clean = _clean_str(key)
            value_clean = _clean_str(value)
            if key_clean and value_clean:
                headers[key_clean] = value_clean.strip('"')
    elif isinstance(raw_headers, str):
        line = raw_headers.strip().strip('"')
        if ":" in line:
            key, value = line.split(":", 1)
            key_clean = _clean_str(key)
            value_clean = _clean_str(value)
            if key_clean and value_clean:
                headers[key_clean] = value_clean
    return headers


def _normalize_data_source_configs(raw_entries: Any) -> List[Dict[str, Any]]:
    if not raw_entries:
        return []

    normalized_entries: List[Dict[str, Any]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        if not _to_bool(entry.get("active", True), default=True):
            continue

        name = _clean_str(entry.get("name"))
        if not name:
            continue

        api_key = _clean_str(entry.get("api_key"))
        source_type_raw = (
            entry.get("source_type")
            or entry.get("type")
            or entry.get("data_type")
        )
        headers = _parse_headers(entry.get("headers"))
        user_agent = _clean_str(entry.get("user_agent"))
        if not user_agent and headers.get("User-Agent"):
            user_agent = headers.get("User-Agent")
        normalized_entries.append(
            {
                "name": name,
                "title": _clean_str(entry.get("title")),
                "source_type": _normalize_source_type(source_type_raw),
                "reference_url": _clean_str(entry.get("reference_url") or entry.get("url")),
                "api_url": _clean_str(entry.get("api_url") or entry.get("endpoint")),
                "key_required": _to_bool(
                    entry.get("key_required"),
                    default=bool(api_key),
                ),
                "api_key": api_key,
                "cite": _to_bool(entry.get("cite"), default=True),
                "headers": headers,
                "user_agent": user_agent,
            }
        )

    return normalized_entries


def _ensure_data_sources_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    if "data_sources" not in inspector.get_table_names():
        return

    to_apply: List[str] = []
    with engine.connect() as connection:
        result = connection.execute(text("PRAGMA table_info(data_sources)"))
        columns = {row._mapping["name"] for row in result}
        if "active" not in columns:
            to_apply.append("ALTER TABLE data_sources ADD COLUMN active BOOLEAN NOT NULL DEFAULT 1")
        if "headers" not in columns:
            to_apply.append("ALTER TABLE data_sources ADD COLUMN headers TEXT DEFAULT '{}'")  # JSON-compatible

    if not to_apply:
        return

    with engine.begin() as connection:
        for statement in to_apply:
            connection.execute(text(statement))


def _sync_data_sources(engine: Engine, entries: List[Dict[str, Any]]) -> None:
    if not entries:
        return

    _ensure_data_sources_schema(engine)

    with engine.begin() as connection:
        existing_rows = connection.execute(
            select(data_sources.c.name, data_sources.c.id)
        ).all()
        existing_ids = {row.name: row.id for row in existing_rows}

        for record in entries:
            payload = {
                "name": record["name"],
                "title": record.get("title"),
                "source_type": record.get("source_type"),
                "reference_url": record.get("reference_url"),
                "api_url": record.get("api_url"),
                "key_required": record.get("key_required"),
                "api_key": record.get("api_key"),
                "cite": record.get("cite"),
            }

            existing_id = existing_ids.get(record["name"])
            if existing_id is not None:
                payload["date_updated"] = datetime.utcnow()
                connection.execute(
                    update(data_sources)
                    .where(data_sources.c.id == existing_id)
                    .values(**payload)
                )
            else:
                connection.execute(insert(data_sources).values(**payload))


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

    data_source_entries = _normalize_data_source_configs(
        birdsong_section.get("data_sources")
    )
    data_source_headers = {
        entry["name"]: dict(entry.get("headers", {}))
        for entry in data_source_entries
        if entry.get("headers")
    }
    data_source_user_agents = {
        entry["name"]: entry.get("user_agent")
        for entry in data_source_entries
        if entry.get("user_agent")
    }
    alerts_config = birdsong_section.get("alerts") or {}
    storage_section = birdsong_section.get("storage") or {}
    storage_paths = {}
    for key, value in storage_section.items():
        if value is None:
            continue
        resolved = _resolve_path(value, base_dir_path)
        storage_paths[key] = resolved
        if key.endswith("path") or key.endswith("_path"):
            resolved.mkdir(parents=True, exist_ok=True)
    notifications_config = config_data.get("notifications") or {}

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
        "alerts": alerts_config,
        }
    }

    app_config = AppConfig.from_dict(normalized_config)

    engine = initialize_database(app_config.birdsong.database)
    _sync_data_sources(engine, data_source_entries)

    resources = {
        "database_dir": database_dir_path,
        "database_file": database_dir_path / database_name,
        "model_path": model_path,
        "label_path": label_path,
        "species_list_path": species_list_path,
        "camera_output_paths": camera_output_paths,
        "camera_coordinates": camera_coordinates,
        "microphone_output_paths": microphone_output_paths,
        "third_party_sources": data_source_entries,
        "data_source_headers": data_source_headers,
        "data_source_user_agents": data_source_user_agents,
        "alerts_config": alerts_config,
        "storage_paths": storage_paths,
        "notifications_config": notifications_config,
    }

    return app_config, resources
