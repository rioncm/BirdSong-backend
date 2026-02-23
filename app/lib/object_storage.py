from __future__ import annotations

import logging
import mimetypes
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse


logger = logging.getLogger("birdsong.storage")

SUPPORTED_PLAYBACK_FORMATS: Dict[str, str] = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
}

_ENV_TOKEN_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


@dataclass(frozen=True)
class RecordingStorageConfig:
    enabled: bool = False
    endpoint_url: Optional[str] = None
    region_name: str = "us-east-1"
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    secure: bool = True
    bucket: Optional[str] = None
    prefix: str = "recordings"
    playback_format: str = "mp3"
    keep_wav_copy: bool = True
    delete_local_after_upload: bool = True

    @property
    def normalized_playback_format(self) -> str:
        value = (self.playback_format or "wav").strip().lower()
        if value not in SUPPORTED_PLAYBACK_FORMATS:
            return "wav"
        return value


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off"}:
            return False
    return default


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_env_token(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    # Support "${ENV_KEY}" placeholders in config files.
    token_match = _ENV_TOKEN_PATTERN.fullmatch(stripped)
    if token_match:
        return os.getenv(token_match.group(1))
    # Support "$ENV_KEY" shorthand.
    if stripped.startswith("$") and len(stripped) > 1 and " " not in stripped:
        return os.getenv(stripped[1:])
    return value


def _env_or_value(env_keys: list[str], value: Any) -> Any:
    for env_key in env_keys:
        env_value = os.getenv(env_key)
        if env_value is not None and env_value != "":
            return env_value
    return _resolve_env_token(value)


def build_recording_storage_config(storage_section: Dict[str, Any]) -> RecordingStorageConfig:
    s3_section_raw = storage_section.get("s3") if isinstance(storage_section.get("s3"), dict) else {}
    recordings_section_raw = (
        storage_section.get("recordings") if isinstance(storage_section.get("recordings"), dict) else {}
    )

    enabled_raw = _env_or_value(
        ["BIRDSONG_S3_ENABLED", "MINIO_ENABLED"],
        s3_section_raw.get("enabled", False),
    )
    endpoint_url = _clean_str(
        _env_or_value(
            ["BIRDSONG_S3_ENDPOINT_URL", "MINIO_ENDPOINT_URL", "AWS_ENDPOINT_URL"],
            s3_section_raw.get("endpoint_url"),
        )
    )
    access_key = _clean_str(
        _env_or_value(
            ["BIRDSONG_S3_ACCESS_KEY", "MINIO_ACCESS_KEY", "AWS_ACCESS_KEY_ID"],
            s3_section_raw.get("access_key"),
        )
    )
    secret_key = _clean_str(
        _env_or_value(
            ["BIRDSONG_S3_SECRET_KEY", "MINIO_SECRET_KEY", "AWS_SECRET_ACCESS_KEY"],
            s3_section_raw.get("secret_key"),
        )
    )
    bucket = _clean_str(
        _env_or_value(
            ["BIRDSONG_S3_BUCKET", "MINIO_BUCKET", "AWS_S3_BUCKET"],
            s3_section_raw.get("bucket"),
        )
    )
    region_name = _clean_str(
        _env_or_value(
            ["BIRDSONG_S3_REGION", "AWS_REGION", "AWS_DEFAULT_REGION"],
            s3_section_raw.get("region"),
        )
    ) or "us-east-1"
    secure_raw = _env_or_value(
        ["BIRDSONG_S3_SECURE", "MINIO_SECURE"],
        s3_section_raw.get("secure", True),
    )
    prefix = _clean_str(
        _env_or_value(
            ["BIRDSONG_S3_PREFIX", "MINIO_PREFIX"],
            s3_section_raw.get("prefix"),
        )
    ) or "recordings"

    playback_format = (
        _clean_str(
            _env_or_value(
                ["BIRDSONG_PLAYBACK_FORMAT", "BIRDSONG_RECORDING_PLAYBACK_FORMAT"],
                recordings_section_raw.get("playback_format"),
            )
        )
        or "mp3"
    )
    keep_wav_copy_raw = _env_or_value(
        ["BIRDSONG_KEEP_WAV_COPY", "BIRDSONG_RECORDING_KEEP_WAV_COPY"],
        recordings_section_raw.get("keep_wav_copy", True),
    )
    delete_local_after_upload_raw = _env_or_value(
        ["BIRDSONG_DELETE_LOCAL_AFTER_UPLOAD", "BIRDSONG_RECORDING_DELETE_LOCAL_AFTER_UPLOAD"],
        recordings_section_raw.get("delete_local_after_upload", True),
    )

    enabled = _parse_bool(enabled_raw, default=False)
    secure = _parse_bool(secure_raw, default=True)

    # Endpoint scheme should control secure when explicitly provided.
    if endpoint_url:
        if endpoint_url.lower().startswith("http://"):
            secure = False
        if endpoint_url.lower().startswith("https://"):
            secure = True

    return RecordingStorageConfig(
        enabled=enabled,
        endpoint_url=endpoint_url,
        region_name=region_name,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        bucket=bucket,
        prefix=prefix,
        playback_format=playback_format,
        keep_wav_copy=_parse_bool(keep_wav_copy_raw, default=True),
        delete_local_after_upload=_parse_bool(delete_local_after_upload_raw, default=True),
    )


def is_s3_uri(value: str) -> bool:
    return value.strip().lower().startswith("s3://")


def parse_s3_uri(uri: str) -> Tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme.lower() != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {uri}")
    key = parsed.path.lstrip("/")
    if not key:
        raise ValueError(f"S3 URI has no object key: {uri}")
    return parsed.netloc, key


def build_s3_uri(bucket: str, key: str) -> str:
    cleaned_key = key.lstrip("/")
    return f"s3://{bucket}/{cleaned_key}"


def guess_media_type(path_or_key: str) -> str:
    media_type, _ = mimetypes.guess_type(path_or_key)
    return media_type or "application/octet-stream"


def build_object_key(prefix: str, *, category: str, wav_id: str, source_id: Optional[str], extension: str) -> str:
    segments = [prefix.strip("/")]
    if category:
        segments.append(category.strip("/"))
    if source_id:
        segments.append(str(source_id).strip("/"))
    segments.append(f"{wav_id}.{extension}")
    return "/".join(segment for segment in segments if segment)


def transcode_audio_for_playback(input_path: Path, *, output_format: str) -> Path:
    fmt = output_format.strip().lower()
    if fmt not in SUPPORTED_PLAYBACK_FORMATS:
        raise ValueError(f"Unsupported playback format '{output_format}'")

    if fmt == "wav":
        return input_path

    temp_file = tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False)
    temp_file.close()
    output_path = Path(temp_file.name)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "48000",
    ]

    if fmt == "mp3":
        cmd.extend(["-codec:a", "libmp3lame", "-q:a", "3"])
    elif fmt == "ogg":
        cmd.extend(["-codec:a", "libvorbis", "-qscale:a", "5"])

    cmd.append(str(output_path))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove failed transcode artifact %s", output_path, exc_info=True)
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"ffmpeg transcode failed ({result.returncode}): {stderr}")

    return output_path


class S3RecordingStore:
    def __init__(self, config: RecordingStorageConfig):
        self.config = config
        if not config.enabled:
            raise ValueError("S3RecordingStore cannot be initialized when storage is disabled")
        if not config.bucket:
            raise ValueError("S3 storage enabled but no bucket configured")

        try:
            import boto3  # type: ignore
            from botocore.config import Config as BotoConfig  # type: ignore
            from botocore.exceptions import ClientError  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "boto3 is required for S3 recording storage. Install boto3 in backend requirements."
            ) from exc

        self._client_error = ClientError
        self.client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key,
            region_name=config.region_name,
            use_ssl=config.secure,
            config=BotoConfig(signature_version="s3v4"),
        )
        self._ensure_bucket_exists(config.bucket)

    def _ensure_bucket_exists(self, bucket: str) -> None:
        try:
            self.client.head_bucket(Bucket=bucket)
            return
        except self._client_error as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code not in {"404", "NoSuchBucket", "400"}:
                raise

        create_payload: Dict[str, Any] = {"Bucket": bucket}
        if self.config.region_name and self.config.region_name != "us-east-1":
            create_payload["CreateBucketConfiguration"] = {
                "LocationConstraint": self.config.region_name
            }
        self.client.create_bucket(**create_payload)

    def upload_file(self, local_path: Path, key: str, *, content_type: Optional[str]) -> str:
        extra_args: Dict[str, str] = {}
        if content_type:
            extra_args["ContentType"] = content_type

        self.client.upload_file(
            Filename=str(local_path),
            Bucket=self.config.bucket,
            Key=key,
            ExtraArgs=extra_args or None,
        )
        return build_s3_uri(self.config.bucket, key)

    def head_object(self, bucket: str, key: str) -> Dict[str, Any]:
        return self.client.head_object(Bucket=bucket, Key=key)

    def get_object(self, bucket: str, key: str) -> Dict[str, Any]:
        return self.client.get_object(Bucket=bucket, Key=key)


def create_s3_recording_store(config: RecordingStorageConfig) -> Optional[S3RecordingStore]:
    if not config.enabled:
        return None
    return S3RecordingStore(config)
