from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

import yaml
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from lib.config_path import resolve_config_path
from lib.data.db import get_session
from lib.data.tables import recordings
from lib.object_storage import (
    RecordingStorageConfig,
    S3RecordingStore,
    SUPPORTED_PLAYBACK_FORMATS,
    build_object_key,
    create_s3_recording_store,
    guess_media_type,
    is_s3_uri,
    transcode_audio_for_playback,
)
from lib.setup import initialize_environment


PROJECT_ROOT = Path(__file__).resolve().parent
logger = logging.getLogger("birdsong.backfill.recordings")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate existing recording paths from local filesystem to object storage."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=resolve_config_path(PROJECT_ROOT),
        help="Path to birdsong config (default: env override, /etc mount, or backend/app/config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without uploading or writing DB updates.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optionally limit number of recordings processed.",
    )
    parser.add_argument(
        "--delete-local",
        action="store_true",
        help="Delete local source files after a successful upload/update.",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Skip missing local files without marking as failures.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Number of rows fetched from SQLite per read batch (default: %(default)s).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=6,
        help="Max DB update retries when SQLite is locked (default: %(default)s).",
    )
    parser.add_argument(
        "--retry-delay-ms",
        type=int,
        default=250,
        help="Initial retry delay in milliseconds for DB lock retries (default: %(default)s).",
    )
    return parser.parse_args()


def _load_environment(config_path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    with config_path.open("r", encoding="utf-8") as config_file:
        config_data = yaml.safe_load(config_file)
    return initialize_environment(config_data, base_dir=PROJECT_ROOT)


def _resolve_storage(resources: Dict[str, Any]) -> Tuple[RecordingStorageConfig, S3RecordingStore]:
    config_raw = resources.get("recording_storage_config")
    config = config_raw if isinstance(config_raw, RecordingStorageConfig) else RecordingStorageConfig()
    if not config.enabled:
        raise RuntimeError("Object storage is not enabled. Set BIRDSONG_S3_ENABLED=true.")

    storage = create_s3_recording_store(config)
    if storage is None:
        raise RuntimeError("Failed to initialize object storage client.")
    return config, storage


def _is_sqlite_locked_error(exc: BaseException) -> bool:
    return "database is locked" in str(exc).lower()


def _iter_recording_rows(*, batch_size: int, limit: Optional[int]) -> Iterator[Dict[str, Any]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")

    yielded = 0
    last_wav_id: Optional[str] = None

    while True:
        session = get_session()
        try:
            stmt = select(
                recordings.c.wav_id,
                recordings.c.path,
                recordings.c.source_id,
            ).order_by(recordings.c.wav_id.asc())

            if last_wav_id is not None:
                stmt = stmt.where(recordings.c.wav_id > last_wav_id)

            if limit is not None:
                remaining = limit - yielded
                if remaining <= 0:
                    return
                stmt = stmt.limit(min(batch_size, remaining))
            else:
                stmt = stmt.limit(batch_size)

            rows = session.execute(stmt).mappings().all()
        finally:
            session.close()

        if not rows:
            return

        for row in rows:
            yielded += 1
            yield dict(row)
        last_wav_id = str(rows[-1]["wav_id"])


def _update_recording_path_with_retry(
    *,
    wav_id: str,
    playback_uri: str,
    max_retries: int,
    retry_delay_ms: int,
) -> None:
    attempt = 0
    while True:
        session = get_session()
        try:
            session.execute(
                update(recordings)
                .where(recordings.c.wav_id == wav_id)
                .values(path=playback_uri)
            )
            session.commit()
            return
        except OperationalError as exc:
            session.rollback()
            if not _is_sqlite_locked_error(exc) or attempt >= max_retries:
                raise

            sleep_seconds = (retry_delay_ms / 1000.0) * (2 ** attempt)
            logger.warning(
                "[retry:%d/%d] sqlite locked while updating %s; sleeping %.2fs",
                attempt + 1,
                max_retries,
                wav_id,
                sleep_seconds,
            )
            time.sleep(sleep_seconds)
            attempt += 1
        finally:
            session.close()


def _upload_recording(
    *,
    row: Dict[str, Any],
    storage: S3RecordingStore,
    storage_config: RecordingStorageConfig,
    delete_local: bool,
) -> str:
    wav_id = str(row["wav_id"])
    source_id = str(row.get("source_id") or "unknown-source")
    local_path = Path(str(row["path"])).expanduser()

    if not local_path.exists() or not local_path.is_file():
        raise FileNotFoundError(f"Local recording not found: {local_path}")

    playback_format = storage_config.normalized_playback_format
    transcoded_path: Optional[Path] = None
    try:
        playback_local_path = local_path
        if playback_format != "wav":
            playback_local_path = transcode_audio_for_playback(local_path, output_format=playback_format)
            transcoded_path = playback_local_path

        playback_key = build_object_key(
            storage_config.prefix,
            category="playback",
            wav_id=wav_id,
            source_id=source_id,
            extension=playback_format,
        )
        playback_content_type = SUPPORTED_PLAYBACK_FORMATS.get(playback_format) or guess_media_type(playback_key)
        playback_uri = storage.upload_file(
            playback_local_path,
            playback_key,
            content_type=playback_content_type,
        )

        if storage_config.keep_wav_copy and playback_format != "wav" and local_path.suffix.lower() == ".wav":
            raw_key = build_object_key(
                storage_config.prefix,
                category="raw",
                wav_id=wav_id,
                source_id=source_id,
                extension="wav",
            )
            storage.upload_file(
                local_path,
                raw_key,
                content_type=SUPPORTED_PLAYBACK_FORMATS["wav"],
            )

        if delete_local:
            try:
                local_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to delete local file %s", local_path, exc_info=True)

        return playback_uri
    finally:
        if transcoded_path is not None and transcoded_path.exists():
            try:
                transcoded_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to cleanup temp file %s", transcoded_path, exc_info=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args()

    _, resources = _load_environment(args.config)
    storage_config, storage = _resolve_storage(resources)

    logger.info(
        "Starting recording migration (dry_run=%s, playback_format=%s, bucket=%s)",
        args.dry_run,
        storage_config.normalized_playback_format,
        storage_config.bucket,
    )

    total = 0
    migrated = 0
    already_s3 = 0
    missing = 0
    failed = 0

    for row in _iter_recording_rows(batch_size=args.batch_size, limit=args.limit):
        total += 1

        wav_id = row.get("wav_id")
        path_value = row.get("path")
        if not wav_id or not path_value:
            logger.warning("Skipping invalid recording row: %s", dict(row))
            failed += 1
            continue

        path_text = str(path_value)
        if is_s3_uri(path_text):
            already_s3 += 1
            logger.info("[skip:s3] %s -> %s", wav_id, path_text)
            continue

        local_path = Path(path_text).expanduser()
        if not local_path.exists() or not local_path.is_file():
            missing += 1
            level = logger.info if args.skip_missing else logger.warning
            level("[skip:missing] %s -> %s", wav_id, local_path)
            if not args.skip_missing:
                failed += 1
            continue

        source_id = row.get("source_id") or "unknown-source"
        preview_key = build_object_key(
            storage_config.prefix,
            category="playback",
            wav_id=str(wav_id),
            source_id=str(source_id),
            extension=storage_config.normalized_playback_format,
        )
        preview_uri = f"s3://{storage_config.bucket}/{preview_key}"

        if args.dry_run:
            logger.info("[dry-run] %s: %s -> %s", wav_id, local_path, preview_uri)
            migrated += 1
            continue

        try:
            playback_uri = _upload_recording(
                row=dict(row),
                storage=storage,
                storage_config=storage_config,
                delete_local=args.delete_local,
            )
            _update_recording_path_with_retry(
                wav_id=str(wav_id),
                playback_uri=playback_uri,
                max_retries=max(0, args.max_retries),
                retry_delay_ms=max(1, args.retry_delay_ms),
            )
            migrated += 1
            logger.info("[migrated] %s: %s -> %s", wav_id, local_path, playback_uri)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.error("[failed] %s: %s", wav_id, exc)

    logger.info(
        "Migration complete. total=%d migrated=%d already_s3=%d missing=%d failed=%d",
        total,
        migrated,
        already_s3,
        missing,
        failed,
    )


if __name__ == "__main__":
    main()
