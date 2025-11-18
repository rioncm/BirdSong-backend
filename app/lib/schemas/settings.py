from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SettingValueResponse(BaseModel):
    key: str
    label: Optional[str]
    description: Optional[str]
    data_type: str
    value: Any
    scope: str = Field(default="global")
    scope_ref: Optional[str]
    editable: bool = True
    sensitive: bool = False


class UpdateSettingRequest(BaseModel):
    value: Any
    scope: str = Field(default="global")
    scope_ref: Optional[str]


class CacheClearResponse(BaseModel):
    status: str = "ok"
    message: str


class SettingDefinitionResponse(BaseModel):
    category: str
    settings: List[SettingValueResponse]


class BootstrapStateResponse(BaseModel):
    state: Dict[str, Any]


class CredentialRequest(BaseModel):
    api_key: Optional[str]
    headers: Optional[Dict[str, Any]]
    expires_at: Optional[str]


class DataSourceResponse(BaseModel):
    name: str
    title: Optional[str]
    active: Optional[bool]
