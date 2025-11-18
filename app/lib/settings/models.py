from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class SettingScope(str, Enum):
    GLOBAL = "global"
    STREAM = "stream"
    MICROPHONE = "microphone"
    INTEGRATION = "integration"


@dataclass(frozen=True)
class SettingCategory:
    category_id: int
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]


@dataclass(frozen=True)
class SettingKey:
    setting_id: int
    category_id: int
    key: str
    label: Optional[str]
    description: Optional[str]
    data_type: str
    default_value: Optional[str]
    constraints: Dict[str, Any]
    editable: bool
    sensitive: bool
    created_at: datetime
    updated_at: Optional[datetime]


@dataclass(frozen=True)
class SettingValue:
    value_id: int
    setting_id: int
    scope: SettingScope
    scope_ref: Optional[str]
    value: str
    version: int
    updated_at: datetime
    updated_by: Optional[str]


@dataclass(frozen=True)
class SettingDefinition:
    category: str
    key: str
    data_type: str
    label: Optional[str] = None
    description: Optional[str] = None
    default_value: Optional[Any] = None
    constraints: Optional[Dict[str, Any]] = None
    editable: bool = True
    sensitive: bool = False
    category_description: Optional[str] = None
