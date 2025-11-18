from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

import redis


logger = logging.getLogger("birdsong.settings.cache")


class SettingsCacheError(RuntimeError):
    """Raised when the Redis cache cannot be reached."""


@dataclass
class SettingsCacheConfig:
    url: str
    prefix: str = "settings"
    ttl_seconds: Optional[int] = 300


class SettingsCache:
    def __init__(self, config: SettingsCacheConfig) -> None:
        if not config.url:
            raise ValueError("Redis URL is required for SettingsCache")
        self._config = config
        self._client = redis.Redis.from_url(config.url, decode_responses=True)

    @property
    def prefix(self) -> str:
        return self._config.prefix

    def build_key(self, key: str) -> str:
        return f"{self._config.prefix}:{key}"

    def get(self, key: str) -> Optional[str]:
        namespaced = self.build_key(key)
        try:
            return self._client.get(namespaced)
        except redis.RedisError as exc:  # noqa: BLE001
            logger.warning("Settings cache get failed: %s", exc)
            return None

    def set(self, key: str, value: str) -> None:
        namespaced = self.build_key(key)
        try:
            if self._config.ttl_seconds:
                self._client.setex(namespaced, self._config.ttl_seconds, value)
            else:
                self._client.set(namespaced, value)
        except redis.RedisError as exc:  # noqa: BLE001
            logger.warning("Settings cache set failed: %s", exc)

    def delete(self, key: str) -> None:
        namespaced = self.build_key(key)
        try:
            self._client.delete(namespaced)
        except redis.RedisError as exc:  # noqa: BLE001
            logger.warning("Settings cache delete failed: %s", exc)

    def clear(self) -> None:
        pattern = f"{self._config.prefix}:*"
        try:
            keys = self._client.keys(pattern)
            if keys:
                self._client.delete(*keys)
        except redis.RedisError as exc:  # noqa: BLE001
            logger.warning("Settings cache clear failed: %s", exc)


def encode_cache_payload(value: str, data_type: str) -> str:
    payload = {"value": value, "data_type": data_type}
    return json.dumps(payload, ensure_ascii=False)


def decode_cache_payload(payload: str) -> tuple[str, str]:
    if not payload:
        raise ValueError("Cache payload is empty")
    parsed = json.loads(payload)
    return str(parsed.get("value")), str(parsed.get("data_type"))
