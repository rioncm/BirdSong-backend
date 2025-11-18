from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from lib.data.tables import (
    settings_audit,
    settings_categories,
    settings_keys,
    settings_values,
)

from .models import SettingCategory, SettingKey, SettingScope, SettingValue


class SettingsRepository:
    """
    Thin data-access layer for settings categories, keys, and values.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_categories(self) -> List[SettingCategory]:
        rows = (
            self._session.execute(
                select(settings_categories).order_by(settings_categories.c.name.asc())
            )
            .mappings()
            .all()
        )
        return [self._row_to_category(row) for row in rows]

    def get_setting_key(self, key: str) -> Optional[SettingKey]:
        row = (
            self._session.execute(
                select(settings_keys).where(settings_keys.c.key == key)
            )
            .mappings()
            .first()
        )
        return self._row_to_key(row) if row else None

    def upsert_value(
        self,
        *,
        setting_id: int,
        value: str,
        scope: SettingScope = SettingScope.GLOBAL,
        scope_ref: Optional[str] = None,
        updated_by: Optional[str] = None,
    ) -> None:
        existing = (
            self._session.execute(
                select(settings_values.c.value_id, settings_values.c.version).where(
                    settings_values.c.setting_id == setting_id,
                    settings_values.c.scope == scope.value,
                    settings_values.c.scope_ref == scope_ref,
                )
            )
            .mappings()
            .first()
        )
        payload = {
            "setting_id": setting_id,
            "scope": scope.value,
            "scope_ref": scope_ref,
            "value": value,
            "updated_at": datetime.utcnow(),
            "updated_by": updated_by,
        }
        if existing is not None:
            current_version = existing["version"] or 1
            self._session.execute(
                update(settings_values)
                .where(settings_values.c.value_id == existing["value_id"])
                .values(**payload, version=current_version + 1)
            )
        else:
            payload["version"] = 1
            self._session.execute(insert(settings_values).values(**payload))

    def get_value(
        self,
        *,
        setting_id: int,
        scope: SettingScope = SettingScope.GLOBAL,
        scope_ref: Optional[str] = None,
    ) -> Optional[SettingValue]:
        row = (
            self._session.execute(
                select(settings_values).where(
                    settings_values.c.setting_id == setting_id,
                    settings_values.c.scope == scope.value,
                    settings_values.c.scope_ref == scope_ref,
                )
            )
            .mappings()
            .first()
        )
        return self._row_to_value(row) if row else None

    def list_values(self, *, setting_ids: Iterable[int]) -> List[SettingValue]:
        rows = (
            self._session.execute(
                select(settings_values).where(settings_values.c.setting_id.in_(list(setting_ids)))
            )
            .mappings()
            .all()
        )
        return [self._row_to_value(row) for row in rows]

    def list_keys(self) -> List[SettingKey]:
        rows = (
            self._session.execute(
                select(settings_keys).order_by(settings_keys.c.key.asc())
            )
            .mappings()
            .all()
        )
        return [self._row_to_key(row) for row in rows]

    def list_definitions_by_category(self) -> Dict[str, List[SettingKey]]:
        rows = (
            self._session.execute(
                select(settings_categories.c.name, settings_keys)
                .join(settings_keys, settings_keys.c.category_id == settings_categories.c.category_id)
                .order_by(settings_categories.c.name.asc(), settings_keys.c.key.asc())
            )
            .mappings()
            .all()
        )
        buckets: Dict[str, List[SettingKey]] = {}
        for row in rows:
            category_name = row["name"]
            key = self._row_to_key(row)
            buckets.setdefault(category_name, []).append(key)
        return buckets

    def delete_value(
        self,
        *,
        setting_id: int,
        scope: SettingScope,
        scope_ref: Optional[str] = None,
    ) -> None:
        self._session.execute(
            settings_values.delete().where(
                settings_values.c.setting_id == setting_id,
                settings_values.c.scope == scope.value,
                settings_values.c.scope_ref == scope_ref,
            )
        )

    def record_audit(
        self,
        *,
        setting_id: int,
        scope: SettingScope,
        scope_ref: Optional[str],
        previous_value: Optional[str],
        new_value: Optional[str],
        actor: Optional[str],
        event: str,
    ) -> None:
        self._session.execute(
            settings_audit.insert().values(
                setting_id=setting_id,
                scope=scope.value,
                scope_ref=scope_ref,
                previous_value=previous_value,
                new_value=new_value,
                actor=actor,
                event=event,
            )
        )

    @staticmethod
    def _row_to_category(row) -> SettingCategory:
        data = dict(row)
        return SettingCategory(
            category_id=data["category_id"],
            name=data["name"],
            description=data.get("description"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    @staticmethod
    def _row_to_key(row) -> SettingKey:
        data = dict(row)
        return SettingKey(
            setting_id=data["setting_id"],
            category_id=data["category_id"],
            key=data["key"],
            label=data.get("label"),
            description=data.get("description"),
            data_type=data.get("data_type"),
            default_value=data.get("default_value"),
            constraints=data.get("constraints") or {},
            editable=bool(data.get("editable", True)),
            sensitive=bool(data.get("sensitive", False)),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    @staticmethod
    def _row_to_value(row) -> SettingValue:
        data = dict(row)
        return SettingValue(
            value_id=data["value_id"],
            setting_id=data["setting_id"],
            scope=SettingScope(data.get("scope", "global")),
            scope_ref=data.get("scope_ref"),
            value=data.get("value"),
            version=data.get("version", 1),
            updated_at=data.get("updated_at"),
            updated_by=data.get("updated_by"),
        )
