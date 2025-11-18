from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from lib.data.db import get_session
from lib.data.tables import users

security = HTTPBearer(auto_error=False)


def get_current_admin_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")
    token = credentials.credentials
    # TODO: replace with real JWT validation when auth service lands
    if token != "dev-admin-token":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return {"role": "admin", "email": "admin@example.com"}
