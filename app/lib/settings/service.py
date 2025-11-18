from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from lib.data.tables import settings_keys

from lib.data import crud
from .cache import SettingsCache, decode_cache_payload, encode_cache_payload
from .models import SettingKey, SettingScope
from .repository import SettingsRepository


logger = logging.getLogger("birdsong.settings.service")


class SettingsServiceError(RuntimeError):
    pass


class SettingsService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        cache: SettingsCache | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._cache = cache

    @contextmanager
    def _session_scope(self) -> Session:
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    def list_settings(self) -> List[Dict[str, Any]]:
        with self._session_scope() as session:
            repository = SettingsRepository(session)
            keys = repository.list_keys()
            values = repository.list_values(setting_ids=[key.setting_id for key in keys])

        value_map: Dict[tuple[int, str, Optional[str]], str] = {}
        for value in values:
            value_map[(value.setting_id, value.scope.value, value.scope_ref)] = value.value

        results = []
        for key in keys:
            serialized = value_map.get((key.setting_id, SettingScope.GLOBAL.value, None)) or key.default_value
            python_value = self._deserialize(serialized, key.data_type) if serialized is not None else None
            results.append(
                {
                    "key": key,
                    "value": python_value,
                    "scope": SettingScope.GLOBAL,
                    "scope_ref": None,
                    "serialized": serialized,
                }
        )
        return results

    def list_definitions(self) -> Dict[str, List[SettingKey]]:
        with self._session_scope() as session:
            repository = SettingsRepository(session)
            return repository.list_definitions_by_category()

    def describe(
        self,
        key: str,
        *,
        scope: SettingScope = SettingScope.GLOBAL,
        scope_ref: Optional[str] = None,
        fallback: Any = None,
    ) -> tuple[Optional[SettingKey], Any]:
        cache_key = self._cache_key(key, scope, scope_ref)
        if self._cache:
            payload = self._cache.get(cache_key)
            if payload:
                try:
                    cached_value, data_type = decode_cache_payload(payload)
                    setting_key = self._get_definition(key)
                    if setting_key is None:
                        return None, fallback
                    return setting_key, self._deserialize(cached_value, data_type)
                except Exception:  # noqa: BLE001
                    logger.debug("Settings cache decode failed", exc_info=True)

        with self._session_scope() as session:
            repository = SettingsRepository(session)
            setting_key = repository.get_setting_key(key)
            if setting_key is None:
                return None, fallback
            value = repository.get_value(setting_id=setting_key.setting_id, scope=scope, scope_ref=scope_ref)
            if value is None:
                serialized = setting_key.default_value
            else:
                serialized = value.value

        if serialized is None:
            return setting_key, fallback

        if self._cache:
            self._cache.set(cache_key, encode_cache_payload(serialized, setting_key.data_type))

        return setting_key, self._deserialize(serialized, setting_key.data_type)

    def set(
        self,
        key: str,
        value: Any,
        *,
        scope: SettingScope = SettingScope.GLOBAL,
        scope_ref: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> Any:
        with self._session_scope() as session:
            repository = SettingsRepository(session)
            setting_key = repository.get_setting_key(key)
            if setting_key is None:
                raise SettingsServiceError(f"Unknown setting '{key}'")
            serialized = self._serialize(value, setting_key.data_type)
            existing = repository.get_value(setting_id=setting_key.setting_id, scope=scope, scope_ref=scope_ref)
            repository.upsert_value(
                setting_id=setting_key.setting_id,
                value=serialized,
                scope=scope,
                scope_ref=scope_ref,
                updated_by=actor,
            )
            repository.record_audit(
                setting_id=setting_key.setting_id,
                scope=scope,
                scope_ref=scope_ref,
                previous_value=existing.value if existing else None,
                new_value=serialized,
                actor=actor,
                event="update",
            )
            session.commit()

        if self._cache:
            cache_key = self._cache_key(key, scope, scope_ref)
            self._cache.set(cache_key, encode_cache_payload(serialized, setting_key.data_type))
        return self._deserialize(serialized, setting_key.data_type)

    def delete(
        self,
        key: str,
        *,
        scope: SettingScope = SettingScope.GLOBAL,
        scope_ref: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> None:
        with self._session_scope() as session:
            repository = SettingsRepository(session)
            setting_key = repository.get_setting_key(key)
            if setting_key is None:
                raise SettingsServiceError(f"Unknown setting '{key}'")
            existing = repository.get_value(setting_id=setting_key.setting_id, scope=scope, scope_ref=scope_ref)
            if existing is None:
                return
            repository.delete_value(setting_id=setting_key.setting_id, scope=scope, scope_ref=scope_ref)
            repository.record_audit(
                setting_id=setting_key.setting_id,
                scope=scope,
                scope_ref=scope_ref,
                previous_value=existing.value,
                new_value=None,
                actor=actor,
                event="delete",
            )
            session.commit()

        if self._cache:
            cache_key = self._cache_key(key, scope, scope_ref)
            self._cache.delete(cache_key)

    def _get_definition(self, key: str) -> Optional[SettingKey]:
        with self._session_scope() as session:
            repository = SettingsRepository(session)
            return repository.get_setting_key(key)

    def get_definition(self, key: str) -> Optional[SettingKey]:
        return self._get_definition(key)

    def clear_cache(self) -> None:
        if self._cache:
            self._cache.clear()

    def get_bootstrap_state(self) -> Dict[str, Any]:
        with self._session_scope() as session:
            record = crud.get_bootstrap_state(session, "admin_initialized")
            if record is None:
                return {}
            return record.get("state_value") or {}

    def upsert_data_source_credentials(self, name: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._session_scope() as session:
            record = crud.get_data_source(session, name)
            if record is None:
                return None
            crud.upsert_data_source_credentials(
                session,
                source_name=name,
                api_key=payload.get("api_key"),
                headers=payload.get("headers"),
                expires_at=None,
            )
            session.commit()
            return record

    def _cache_key(self, key: str, scope: SettingScope, scope_ref: Optional[str]) -> str:
        suffix = scope_ref if scope_ref else "_"
        return f"{scope.value}:{suffix}:{key}"

    def _serialize(self, value: Any, data_type: str) -> str:
        if value is None:
            raise SettingsServiceError("Cannot serialize None for setting")
        if data_type in {"string", "secret"}:
            return str(value)
        if data_type == "int":
            return str(int(value))
        if data_type == "float":
            return str(float(value))
        if data_type == "bool":
            return "true" if bool(value) else "false"
        if data_type == "json":
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _deserialize(self, serialized: str, data_type: str) -> Any:
        if serialized is None:
            return None
        if data_type in {"string", "secret"}:
            return serialized
        if data_type == "int":
            return int(serialized)
        if data_type == "float":
            return float(serialized)
        if data_type == "bool":
            lowered = serialized.strip().lower()
            return lowered in {"1", "true", "yes", "on"}
        if data_type == "json":
            return json.loads(serialized)
        return serialized
