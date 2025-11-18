from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List

import yaml
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from lib.data.tables import settings_categories, settings_keys

from .models import SettingDefinition


def load_settings_manifest(path: Path) -> List[SettingDefinition]:
    if not path.exists():
        raise FileNotFoundError(f"Settings manifest not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    categories = raw.get("categories", [])
    definitions: List[SettingDefinition] = []
    for category in categories:
        if not isinstance(category, dict):
            continue
        name = category.get("name")
        if not name:
            continue
        description = category.get("description")
        for item in category.get("settings", []):
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            data_type = item.get("data_type")
            if not key or not data_type:
                continue
            definitions.append(
                SettingDefinition(
                    category=name,
                    category_description=description,
                    key=str(key),
                    label=item.get("label"),
                    description=item.get("description"),
                    data_type=str(data_type),
                    default_value=item.get("default"),
                    constraints=item.get("constraints"),
                    editable=bool(item.get("editable", True)),
                    sensitive=bool(item.get("sensitive", False)),
                )
            )
    return definitions


def apply_settings_manifest(session: Session, definitions: Iterable[SettingDefinition]) -> None:
    category_cache: Dict[str, int] = {}

    def _ensure_category(name: str, description: str | None) -> int:
        cached = category_cache.get(name)
        if cached:
            return cached
        existing = (
            session.execute(
                select(settings_categories.c.category_id).where(settings_categories.c.name == name)
            )
            .scalars()
            .first()
        )
        if existing is not None:
            category_cache[name] = int(existing)
            if description:
                session.execute(
                    update(settings_categories)
                    .where(settings_categories.c.category_id == existing)
                    .values(description=description)
                )
            return int(existing)
        result = session.execute(
            insert(settings_categories).values(name=name, description=description)
        )
        category_id = result.inserted_primary_key[0]
        category_cache[name] = int(category_id)
        return int(category_id)

    for definition in definitions:
        category_id = _ensure_category(definition.category, definition.category_description)
        serialized_default = _serialize_default(definition.default_value)
        existing = (
            session.execute(
                select(settings_keys.c.setting_id).where(settings_keys.c.key == definition.key)
            )
            .scalars()
            .first()
        )
        payload = {
            "category_id": category_id,
            "key": definition.key,
            "label": definition.label,
            "description": definition.description,
            "data_type": definition.data_type,
            "default_value": serialized_default,
            "constraints": definition.constraints or {},
            "editable": definition.editable,
            "sensitive": definition.sensitive,
        }
        if existing is not None:
            session.execute(
                update(settings_keys)
                .where(settings_keys.c.setting_id == existing)
                .values(**payload)
            )
        else:
            session.execute(insert(settings_keys).values(**payload))


def _serialize_default(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
