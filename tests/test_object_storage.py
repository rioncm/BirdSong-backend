from __future__ import annotations

from app.lib.object_storage import (
    build_object_key,
    build_recording_storage_config,
    build_s3_uri,
    parse_s3_uri,
)


def test_build_and_parse_s3_uri_round_trip() -> None:
    uri = build_s3_uri("birdsong-recordings", "birdsong/playback/north/abc123.mp3")
    assert uri == "s3://birdsong-recordings/birdsong/playback/north/abc123.mp3"

    bucket, key = parse_s3_uri(uri)
    assert bucket == "birdsong-recordings"
    assert key == "birdsong/playback/north/abc123.mp3"


def test_build_object_key_includes_segments() -> None:
    key = build_object_key(
        "birdsong",
        category="playback",
        wav_id="abc123",
        source_id="north-side",
        extension="mp3",
    )
    assert key == "birdsong/playback/north-side/abc123.mp3"


def test_storage_config_respects_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("BIRDSONG_S3_ENABLED", "true")
    monkeypatch.setenv("BIRDSONG_S3_ENDPOINT_URL", "http://127.0.0.1:9000")
    monkeypatch.setenv("BIRDSONG_S3_BUCKET", "birdsong-recordings")
    monkeypatch.setenv("BIRDSONG_PLAYBACK_FORMAT", "ogg")

    config = build_recording_storage_config({"s3": {}, "recordings": {}})
    assert config.enabled is True
    assert config.endpoint_url == "http://127.0.0.1:9000"
    assert config.bucket == "birdsong-recordings"
    assert config.normalized_playback_format == "ogg"
    assert config.secure is False
