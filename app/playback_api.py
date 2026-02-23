from __future__ import annotations

import logging
import mimetypes
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select

from lib.config_path import resolve_config_path
from lib.data.db import get_session
from lib.data.tables import recordings
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
    normalize_playback_filter,
    normalize_playback_format,
)
from lib.setup import initialize_environment


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = resolve_config_path(PROJECT_ROOT)

PLAYBACK_FILTERS: Dict[str, Optional[str]] = {
    "none": None,
    "enhanced": (
        "highpass=f=140,"
        "lowpass=f=9800,"
        "afftdn=nf=-24,"
        "acompressor=threshold=-20dB:ratio=2.2:attack=8:release=120,"
        "volume=1.5,"
        "alimiter=limit=0.95"
    ),
}

app = FastAPI(title="BirdSong Playback API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("birdsong.playback")


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


def _cleanup_temp(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to delete temporary playback file %s", path, exc_info=True)


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
    media_type = response.get("ContentType") or guess_media_type(key)
    content_length = response.get("ContentLength")
    headers: Dict[str, str] = {
        "Cache-Control": "public, max-age=86400",
    }
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
) -> list[str]:
    cmd = [
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

    headers = {
        "Cache-Control": "public, max-age=3600",
    }
    media_type = _supported_media_type_for_format(output_format)
    return StreamingResponse(_iter_chunks(), media_type=media_type, headers=headers)


@app.on_event("startup")
async def startup_event() -> None:
    _, resources = initialize_environment(
        config_data=yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")),
        base_dir=PROJECT_ROOT,
    )

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
                "Playback service object storage enabled (bucket=%s, endpoint=%s)",
                recording_storage_config.bucket,
                recording_storage_config.endpoint_url,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Playback service failed to initialize object storage")
            recording_storage = None

    playback_service_config_raw = resources.get("playback_service_config")
    playback_service_config = (
        playback_service_config_raw
        if isinstance(playback_service_config_raw, PlaybackServiceConfig)
        else PlaybackServiceConfig()
    )

    app.state.recording_storage = recording_storage
    app.state.recording_storage_config = recording_storage_config
    app.state.playback_service_config = playback_service_config


@app.get("/playback/health", summary="Simple readiness probe for playback service.")
def health_check() -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {"status": "ok", "timestamp": now.isoformat()}


@app.get(
    "/playback/recordings/{wav_id}",
    summary="Stream recording playback with optional live transcoding and enhancement filter.",
    response_model=None,
)
def get_playback_recording(
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

    playback_config: PlaybackServiceConfig = getattr(
        app.state,
        "playback_service_config",
        PlaybackServiceConfig(),
    )
    selected_filter = normalize_playback_filter(
        playback_filter if playback_filter is not None else playback_config.default_filter
    )
    selected_format = normalize_playback_format(
        output_format if output_format is not None else playback_config.default_format
    )

    if selected_filter not in PLAYBACK_FILTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported filter '{selected_filter}'",
        )

    if is_s3_uri(raw_path):
        recording_storage: Optional[S3RecordingStore] = getattr(
            app.state,
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
    if selected_filter == "none" and source_format == selected_format:
        media_type = _supported_media_type_for_format(selected_format)
        headers = {"Cache-Control": "public, max-age=86400"}
        return FileResponse(file_path, media_type=media_type, headers=headers)

    return _stream_transcoded_audio(
        file_path,
        output_format=selected_format,
        playback_filter=selected_filter,
    )
