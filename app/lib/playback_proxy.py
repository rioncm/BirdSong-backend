from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

from lib.object_storage import SUPPORTED_PLAYBACK_FORMATS


_ENV_TOKEN_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")
_FILTER_ALIASES = {
    "none": "none",
    "off": "none",
    "original": "none",
    "raw": "none",
    "enhanced": "enhanced",
    "birdsong": "enhanced",
    "noise_reduction": "enhanced",
    "noise-reduction": "enhanced",
    "denoise": "enhanced",
}


@dataclass(frozen=True)
class PlaybackServiceConfig:
    enabled: bool = False
    base_url: Optional[str] = None
    default_filter: str = "none"
    default_format: str = "mp3"

    @property
    def normalized_filter(self) -> str:
        return normalize_playback_filter(self.default_filter)

    @property
    def normalized_format(self) -> str:
        return normalize_playback_format(self.default_format)


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

    token_match = _ENV_TOKEN_PATTERN.fullmatch(stripped)
    if token_match:
        return os.getenv(token_match.group(1))
    if stripped.startswith("$") and len(stripped) > 1 and " " not in stripped:
        return os.getenv(stripped[1:])
    return value


def _env_or_value(env_keys: list[str], value: Any) -> Any:
    for env_key in env_keys:
        env_value = os.getenv(env_key)
        if env_value is not None and env_value != "":
            return env_value
    return _resolve_env_token(value)


def normalize_playback_filter(value: Optional[str]) -> str:
    normalized = (value or "none").strip().lower()
    return _FILTER_ALIASES.get(normalized, "none")


def normalize_playback_format(value: Optional[str]) -> str:
    normalized = (value or "mp3").strip().lower()
    if normalized in SUPPORTED_PLAYBACK_FORMATS:
        return normalized
    return "mp3"


def build_playback_service_config(playback_section: Dict[str, Any]) -> PlaybackServiceConfig:
    service_section = (
        playback_section.get("service")
        if isinstance(playback_section.get("service"), dict)
        else {}
    )
    transcode_section = (
        playback_section.get("transcode")
        if isinstance(playback_section.get("transcode"), dict)
        else {}
    )

    base_url = _clean_str(
        _env_or_value(
            ["BIRDSONG_PLAYBACK_SERVICE_BASE_URL"],
            service_section.get("base_url"),
        )
    )
    enabled_raw = _env_or_value(
        ["BIRDSONG_PLAYBACK_SERVICE_ENABLED"],
        service_section.get("enabled", bool(base_url)),
    )
    default_filter = (
        _clean_str(
            _env_or_value(
                ["BIRDSONG_PLAYBACK_DEFAULT_FILTER"],
                transcode_section.get("default_filter"),
            )
        )
        or "none"
    )
    default_format = (
        _clean_str(
            _env_or_value(
                ["BIRDSONG_PLAYBACK_DEFAULT_FORMAT"],
                transcode_section.get("default_format"),
            )
        )
        or "mp3"
    )

    if base_url:
        base_url = base_url.rstrip("/")

    return PlaybackServiceConfig(
        enabled=_parse_bool(enabled_raw, default=bool(base_url)),
        base_url=base_url,
        default_filter=normalize_playback_filter(default_filter),
        default_format=normalize_playback_format(default_format),
    )


def build_playback_service_url(
    config: PlaybackServiceConfig,
    wav_id: str,
    *,
    playback_filter: Optional[str] = None,
    output_format: Optional[str] = None,
) -> Optional[str]:
    if not wav_id or not config.enabled or not config.base_url:
        return None

    encoded_wav_id = quote(wav_id, safe="")
    base_url = config.base_url.rstrip("/")
    if base_url.endswith("/playback"):
        url = f"{base_url}/recordings/{encoded_wav_id}"
    else:
        url = f"{base_url}/playback/recordings/{encoded_wav_id}"

    selected_filter = normalize_playback_filter(playback_filter or config.default_filter)
    selected_format = normalize_playback_format(output_format or config.default_format)

    query: Dict[str, str] = {"format": selected_format}
    if selected_filter != "none":
        query["filter"] = selected_filter
    if query:
        url = f"{url}?{urlencode(query)}"
    return url
