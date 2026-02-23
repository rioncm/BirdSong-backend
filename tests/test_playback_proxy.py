from __future__ import annotations

from app.lib.playback_proxy import (
    build_playback_service_config,
    build_playback_service_url,
    normalize_playback_filter,
    normalize_playback_format,
)


def test_build_playback_service_config_respects_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("BIRDSONG_PLAYBACK_SERVICE_ENABLED", "true")
    monkeypatch.setenv("BIRDSONG_PLAYBACK_SERVICE_BASE_URL", "https://playback.api.birdsong.diy")
    monkeypatch.setenv("BIRDSONG_PLAYBACK_DEFAULT_FILTER", "birdsong")
    monkeypatch.setenv("BIRDSONG_PLAYBACK_DEFAULT_FORMAT", "ogg")

    config = build_playback_service_config({"service": {}, "transcode": {}})
    assert config.enabled is True
    assert config.base_url == "https://playback.api.birdsong.diy"
    assert config.normalized_filter == "enhanced"
    assert config.normalized_format == "ogg"


def test_build_playback_service_url_uses_defaults_without_filter_query() -> None:
    config = build_playback_service_config(
        {
            "service": {"enabled": True, "base_url": "https://playback.api.birdsong.diy/"},
            "transcode": {"default_filter": "none", "default_format": "mp3"},
        }
    )

    url = build_playback_service_url(config, "20251023_162835")
    assert (
        url
        == "https://playback.api.birdsong.diy/playback/recordings/20251023_162835?format=mp3"
    )


def test_build_playback_service_url_with_explicit_filter() -> None:
    config = build_playback_service_config(
        {
            "service": {"enabled": True, "base_url": "https://playback.api.birdsong.diy"},
            "transcode": {"default_filter": "none", "default_format": "mp3"},
        }
    )

    url = build_playback_service_url(
        config,
        "20251023_162835",
        playback_filter="denoise",
        output_format="wav",
    )
    assert (
        url
        == "https://playback.api.birdsong.diy/playback/recordings/20251023_162835?format=wav&filter=enhanced"
    )


def test_build_playback_service_url_respects_base_url_with_playback_suffix() -> None:
    config = build_playback_service_config(
        {
            "service": {"enabled": True, "base_url": "https://api.birdsong.diy/playback"},
            "transcode": {"default_filter": "none", "default_format": "mp3"},
        }
    )

    url = build_playback_service_url(config, "20251023_162835")
    assert (
        url
        == "https://api.birdsong.diy/playback/recordings/20251023_162835?format=mp3"
    )


def test_normalizers_fall_back_to_safe_defaults() -> None:
    assert normalize_playback_filter("unexpected") == "none"
    assert normalize_playback_filter("noise_reduction") == "enhanced"
    assert normalize_playback_format("unexpected") == "mp3"
