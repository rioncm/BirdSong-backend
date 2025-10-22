from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
import wave

from birdnetlib.analyzer import Analyzer
from birdnetlib.main import Recording

from lib.config import BirdNetConfig


@dataclass(frozen=True)
class DetectionResult:
    """Bird detection produced by BirdNET."""

    common_name: str
    scientific_name: str
    label: str
    confidence: float
    start_time: float
    end_time: float
    is_predicted_for_location: Optional[bool]


@dataclass(frozen=True)
class AnalyzeResult:
    """Structured summary of an analyzed audio recording."""

    input_file: Path
    stream_id: Optional[str]
    timestamp: datetime
    duration_seconds: float
    frame_rate: int
    channels: int
    sample_width: int
    frame_count: int
    file_size_bytes: int
    detections: Sequence[DetectionResult]
    notes: Optional[str] = None


class BaseAnalyzer:
    """
    BirdNET-powered analyzer that extracts species detections
    from captured WAV files and records the results to a log.
    """

    def __init__(
        self,
        birdnet_config: BirdNetConfig,
        log_path: Path | str = "logs/analyzer.log",
    ) -> None:
        self.config = birdnet_config
        self.log_path = Path(log_path)
        self.logger = self._configure_logger()
        self.analyzer = self._build_birdnet_analyzer()

    def _configure_logger(self) -> logging.Logger:
        logger = logging.getLogger("birdsong.analyzer")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(self.log_path, encoding="utf-8")
            formatter = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.propagate = False
        return logger

    def _build_birdnet_analyzer(self) -> Analyzer:
        analyzer = Analyzer(
            custom_species_list_path=str(self.config.species_list_path)
            if self.config.species_list_path
            else None,
        )

        model_override = self.config.model_path
        label_override = self.config.label_path

        # Reload when a custom model or label file is provided.
        if model_override or label_override:
            if model_override:
                analyzer.model_path = str(model_override)
            if label_override:
                analyzer.label_path = str(label_override)

            analyzer.load_labels()
            analyzer.load_model()

        return analyzer

    def analyze(
        self,
        audio_file: Path | str,
        *,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        week_48: Optional[int] = None,
        stream_id: Optional[str] = None,
    ) -> AnalyzeResult:
        audio_path = Path(audio_file)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        metadata = self._read_wav_metadata(audio_path)

        recording = Recording(
            analyzer=self.analyzer,
            path=str(audio_path),
            week_48=week_48 if week_48 is not None else -1,
            sensitivity=self.config.sensitivity,
            lat=latitude,
            lon=longitude,
            min_conf=self.config.confidence_threshold,
            overlap=self.config.overlap,
            return_all_detections=self.config.return_all_detections,
        )

        recording.analyze()
        detections = self._convert_detections(recording.detections)

        sorted_detections = sorted(
            detections, key=lambda detection: detection.confidence, reverse=True
        )
        if self.config.top_n > 0:
            sorted_detections = sorted_detections[: self.config.top_n]

        result = AnalyzeResult(
            input_file=audio_path.resolve(),
            stream_id=stream_id,
            timestamp=datetime.now(timezone.utc),
            duration_seconds=metadata["duration_seconds"],
            frame_rate=int(metadata["frame_rate"]),
            channels=int(metadata["channels"]),
            sample_width=int(metadata["sample_width"]),
            frame_count=int(metadata["frame_count"]),
            file_size_bytes=int(metadata["file_size_bytes"]),
            detections=sorted_detections,
            notes=None if sorted_detections else "No detections above threshold.",
        )
        self._log_result(result)
        return result

    def _read_wav_metadata(self, audio_path: Path) -> Dict[str, float | int]:
        try:
            with wave.open(str(audio_path), "rb") as wav_reader:
                frame_rate = wav_reader.getframerate()
                channels = wav_reader.getnchannels()
                sample_width = wav_reader.getsampwidth()
                frame_count = wav_reader.getnframes()
        except wave.Error as exc:
            raise ValueError(f"Unable to read WAV metadata from {audio_path}") from exc

        duration_seconds = frame_count / frame_rate if frame_rate else 0.0
        file_size_bytes = audio_path.stat().st_size

        return {
            "frame_rate": frame_rate,
            "channels": channels,
            "sample_width": sample_width,
            "frame_count": frame_count,
            "duration_seconds": duration_seconds,
            "file_size_bytes": file_size_bytes,
        }

    def _convert_detections(
        self, detections: Iterable[Dict[str, Any]]
    ) -> List[DetectionResult]:
        converted: List[DetectionResult] = []
        for detection in detections:
            raw_location_flag = detection.get("is_predicted_for_location_and_date")
            location_flag: Optional[bool]
            if isinstance(raw_location_flag, bool):
                location_flag = raw_location_flag
            elif raw_location_flag is None or raw_location_flag == "":
                location_flag = None
            elif isinstance(raw_location_flag, str):
                lowered = raw_location_flag.strip().lower()
                if lowered in {"true", "t", "yes", "y", "1"}:
                    location_flag = True
                elif lowered in {"false", "f", "no", "n", "0"}:
                    location_flag = False
                else:
                    location_flag = None
            else:
                try:
                    location_flag = bool(int(raw_location_flag))
                except (TypeError, ValueError):
                    location_flag = None

            try:
                converted.append(
                    DetectionResult(
                        common_name=str(detection.get("common_name", "")),
                        scientific_name=str(detection.get("scientific_name", "")),
                        label=str(detection.get("label", "")),
                        confidence=float(detection.get("confidence", 0.0)),
                        start_time=float(detection.get("start_time", 0.0)),
                        end_time=float(detection.get("end_time", 0.0)),
                        is_predicted_for_location=location_flag,
                    )
                )
            except (TypeError, ValueError):
                # Skip malformed detections but continue processing.
                continue
        return converted

    def _log_result(self, result: AnalyzeResult) -> None:
        try:
            file_stat = result.input_file.stat()
            file_datetime = datetime.fromtimestamp(file_stat.st_mtime, timezone.utc).astimezone()
            capture_date = file_datetime.strftime("%Y-%m-%d")
            capture_time = file_datetime.strftime("%H:%M:%S %Z")
        except FileNotFoundError:
            capture_date = "unknown"
            capture_time = "unknown"

        summary = (
            f"id={result.stream_id or 'unknown'} | date={capture_date} | time={capture_time} | "
            f"path={result.input_file} | duration={result.duration_seconds:.2f}s | "
            f"rate={result.frame_rate}Hz | channels={result.channels} | "
            f"detections={len(result.detections)}"
        )
        self.logger.info(summary)

        if result.detections:
            for detection in result.detections:
                location_hint = (
                    "predicted"
                    if detection.is_predicted_for_location
                    else "unverified"
                    if detection.is_predicted_for_location is not None
                    else "unknown"
                )
                self.logger.info(
                    " - %s (%s) | confidence=%.2f | window=%.2f-%.2fs | %s",
                    detection.common_name or detection.label,
                    detection.scientific_name,
                    detection.confidence,
                    detection.start_time,
                    detection.end_time,
                    location_hint,
                )
        else:
            self.logger.info(" - No detections above %.2f", self.config.confidence_threshold)
