from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


@dataclass
class DatabaseConfig:
    engine: str
    name: str
    path: Path

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DatabaseConfig":
        return cls(
            engine=data.get("type", "sqlite"),
            name=data["name"],
            path=Path(data["path"]),
        )


@dataclass
class BirdNetConfig:
    model_path: Optional[Path]
    label_path: Optional[Path]
    species_list_path: Optional[Path]
    sample_rate: int
    chunk_size: int
    overlap: float
    confidence_threshold: float
    top_n: int
    sensitivity: float
    return_all_detections: bool

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BirdNetConfig":
        def to_optional_path(value: Any) -> Optional[Path]:
            if value in (None, "", "null"):
                return None
            return Path(str(value))

        return cls(
            model_path=to_optional_path(data.get("model_path")),
            label_path=to_optional_path(data.get("label_path")),
            species_list_path=to_optional_path(data.get("species_list_path")),
            sample_rate=int(data["sample_rate"]),
            chunk_size=int(data["chunk_size"]),
            overlap=float(data["overlap"]),
            confidence_threshold=float(data["confidence_threshold"]),
            top_n=int(data["top_n"]),
            sensitivity=float(data.get("sensitivity", 1.0)),
            return_all_detections=bool(data.get("return_all_detections", False)),
        )


@dataclass
class CameraConfig:
    camera_id: str
    rtsp_url: str
    record_time: int
    output_folder: str
    location: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        *,
        camera_name: Optional[str] = None,
    ) -> "CameraConfig":
        def to_optional_float(value: Any) -> Optional[float]:
            if value in (None, "", "null"):
                return None
            return float(value)

        camera_id_raw = (
            data.get("camera_id")
            or data.get("id")
            or camera_name
            or data.get("output_folder")
        )
        if not camera_id_raw:
            raise ValueError("Camera configuration missing 'camera_id'")

        return cls(
            camera_id=str(camera_id_raw),
            rtsp_url=data["rtsp_url"],
            record_time=int(data["record_time"]),
            output_folder=data["output_folder"],
            location=data["location"],
            latitude=to_optional_float(data.get("latitude")),
            longitude=to_optional_float(data.get("longitude")),
        )


@dataclass
class MicrophoneConfig:
    microphone_id: str
    output_folder: str
    location: str
    api_key: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        *,
        microphone_name: Optional[str] = None,
    ) -> "MicrophoneConfig":
        def to_optional_float(value: Any) -> Optional[float]:
            if value in (None, "", "null"):
                return None
            return float(value)

        microphone_id_raw = (
            data.get("microphone_id")
            or data.get("id")
            or microphone_name
            or data.get("output_folder")
        )
        if not microphone_id_raw:
            raise ValueError("Microphone configuration missing 'id'")

        api_key = data.get("api_key")
        if not api_key:
            raise ValueError(
                f"Microphone '{microphone_id_raw}' configuration missing 'api_key'"
            )

        return cls(
            microphone_id=str(microphone_id_raw),
            output_folder=data["output_folder"],
            location=data["location"],
            api_key=str(api_key),
            latitude=to_optional_float(data.get("latitude")),
            longitude=to_optional_float(data.get("longitude")),
        )


@dataclass
class BirdsongConfig:
    database: DatabaseConfig
    config: BirdNetConfig
    cameras: Dict[str, CameraConfig] = field(default_factory=dict)
    microphones: Dict[str, MicrophoneConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Dict]) -> "BirdsongConfig":
        cameras_raw = data.get("cameras", {})
        cameras = {
            name: CameraConfig.from_dict(camera_conf, camera_name=name)
            for name, camera_conf in cameras_raw.items()
        }
        microphones_raw = data.get("microphones", {})
        microphones = {
            name: MicrophoneConfig.from_dict(mic_conf, microphone_name=name)
            for name, mic_conf in microphones_raw.items()
        }
        return cls(
            database=DatabaseConfig.from_dict(data["database"]),
            config=BirdNetConfig.from_dict(data["config"]),
            cameras=cameras,
            microphones=microphones,
        )


@dataclass
class AppConfig:
    birdsong: BirdsongConfig

    @classmethod
    def from_dict(cls, data: Dict[str, Dict]) -> "AppConfig":
        return cls(birdsong=BirdsongConfig.from_dict(data["birdsong"]))


def app_config(file_path: str) -> AppConfig:
    with open(file_path, "r", encoding="utf-8") as file:
        config_dict = yaml.safe_load(file)
    return AppConfig.from_dict(config_dict)


# Backwards-compatible alias.
load_config = app_config
