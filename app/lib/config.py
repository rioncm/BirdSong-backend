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
class StreamConfig:
    stream_id: str
    kind: str
    url: str
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
        stream_name: Optional[str] = None,
    ) -> "StreamConfig":
        def to_optional_float(value: Any) -> Optional[float]:
            if value in (None, "", "null"):
                return None
            return float(value)

        stream_id_raw = data.get("stream_id") or data.get("id") or stream_name or data.get("output_folder")
        if not stream_id_raw:
            raise ValueError("Stream configuration missing 'stream_id'")

        kind = (data.get("kind") or "rtsp").strip().lower()
        url = data.get("url")
        if not url:
            raise ValueError(f"Stream '{stream_id_raw}' missing required 'url'")

        record_time = int(data.get("record_time", 0))
        if record_time <= 0:
            raise ValueError(f"Stream '{stream_id_raw}' must set a positive 'record_time'")

        location = data.get("location") or stream_id_raw
        output_folder = data.get("output_folder") or stream_id_raw

        return cls(
            stream_id=str(stream_id_raw),
            kind=kind,
            url=str(url),
            record_time=record_time,
            output_folder=str(output_folder),
            location=str(location),
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
    streams: Dict[str, StreamConfig] = field(default_factory=dict)
    microphones: Dict[str, MicrophoneConfig] = field(default_factory=dict)
    alerts: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Dict]) -> "BirdsongConfig":
        streams_raw = data.get("streams", {})
        streams = {
            name: StreamConfig.from_dict(stream_conf, stream_name=name)
            for name, stream_conf in streams_raw.items()
        }
        microphones_raw = data.get("microphones", {})
        microphones = {
            name: MicrophoneConfig.from_dict(mic_conf, microphone_name=name)
            for name, mic_conf in microphones_raw.items()
        }
        return cls(
            database=DatabaseConfig.from_dict(data["database"]),
            config=BirdNetConfig.from_dict(data["config"]),
            streams=streams,
            microphones=microphones,
            alerts=data.get("alerts", {}),
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
