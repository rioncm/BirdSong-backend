from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from lib.auth.dependencies import get_current_admin_user
from lib.settings import SettingsService
from lib.settings.models import SettingScope
from lib.schemas.settings import (
    BootstrapStateResponse,
    CacheClearResponse,
    CredentialRequest,
    DataSourceResponse,
    SettingDefinitionResponse,
    SettingValueResponse,
    UpdateSettingRequest,
)


def get_settings_service(request: Request) -> SettingsService:
    resources = getattr(request.app.state, "resources", {})
    service = resources.get("settings_service") if isinstance(resources, dict) else None
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Settings service unavailable")
    return service


def _parse_scope(raw: str) -> SettingScope:
    try:
        return SettingScope(raw)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid scope '{raw}'") from exc


def _to_response(setting_key, value, scope: SettingScope, scope_ref: Optional[str]) -> SettingValueResponse:
    return SettingValueResponse(
        key=setting_key.key,
        label=setting_key.label,
        description=setting_key.description,
        data_type=setting_key.data_type,
        value=value,
        scope=scope.value,
        scope_ref=scope_ref,
        editable=setting_key.editable,
        sensitive=setting_key.sensitive,
    )


router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])


@router.get("", response_model=List[SettingValueResponse])
def list_settings(
    service: SettingsService = Depends(get_settings_service),
    _admin=Depends(get_current_admin_user),
):
    entries = service.list_settings()
    responses: List[SettingValueResponse] = []
    for entry in entries:
        setting_key = entry["key"]
        value = entry["value"]
        scope = entry["scope"]
        scope_ref = entry["scope_ref"]
        responses.append(_to_response(setting_key, value, scope, scope_ref))
    return responses


@router.get("/definitions", response_model=List[SettingDefinitionResponse])
def list_definitions(
    service: SettingsService = Depends(get_settings_service),
    _admin=Depends(get_current_admin_user),
):
    definitions = service.list_definitions()
    payload: List[SettingDefinitionResponse] = []
    for category, entries in definitions.items():
        payload.append(
            SettingDefinitionResponse(
                category=category,
                settings=[
                    SettingValueResponse(
                        key=entry.key,
                        label=entry.label,
                        description=entry.description,
                        data_type=entry.data_type,
                        value=None,
                        scope="global",
                        scope_ref=None,
                        editable=entry.editable,
                        sensitive=entry.sensitive,
                    )
                    for entry in entries
                ],
            )
        )
    return payload


@router.get("/{key}", response_model=SettingValueResponse)
def get_setting(
    key: str,
    scope: str = "global",
    scope_ref: Optional[str] = None,
    service: SettingsService = Depends(get_settings_service),
    _admin=Depends(get_current_admin_user),
):
    resolved_scope = _parse_scope(scope)
    setting_key, value = service.describe(key, scope=resolved_scope, scope_ref=scope_ref)
    if setting_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Setting '{key}' not found")
    return _to_response(setting_key, value, resolved_scope, scope_ref)


@router.put("/{key}", response_model=SettingValueResponse)
def update_setting(
    key: str,
    payload: UpdateSettingRequest,
    service: SettingsService = Depends(get_settings_service),
    admin=Depends(get_current_admin_user),
):
    resolved_scope = _parse_scope(payload.scope)
    value = service.set(
        key,
        payload.value,
        scope=resolved_scope,
        scope_ref=payload.scope_ref,
        actor=admin.get("email"),
    )
    definition = service.get_definition(key)
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Setting '{key}' not found")
    return _to_response(definition, value, resolved_scope, payload.scope_ref)


@router.delete("/{key}/scopes/{scope}/{scope_ref}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scoped_setting(
    key: str,
    scope: str,
    scope_ref: str,
    service: SettingsService = Depends(get_settings_service),
    admin=Depends(get_current_admin_user),
):
    resolved_scope = _parse_scope(scope)
    service.delete(key, scope=resolved_scope, scope_ref=scope_ref, actor=admin.get("email"))
    return None


@router.post("/cache/clear", response_model=CacheClearResponse)
def clear_settings_cache(
    service: SettingsService = Depends(get_settings_service),
    _admin=Depends(get_current_admin_user),
):
    service.clear_cache()
    return CacheClearResponse(message="Settings cache cleared")


@router.get("/bootstrap/state", response_model=BootstrapStateResponse)
def get_bootstrap_state(
    service: SettingsService = Depends(get_settings_service),
    _admin=Depends(get_current_admin_user),
):
    state = service.get_bootstrap_state()
    return BootstrapStateResponse(state=state)


@router.post("/data-sources/{name}/credentials", response_model=DataSourceResponse)
def upsert_credentials(
    name: str,
    payload: CredentialRequest,
    service: SettingsService = Depends(get_settings_service),
    _admin=Depends(get_current_admin_user),
):
    record = service.upsert_data_source_credentials(name, payload)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Data source '{name}' not found")
    return record
