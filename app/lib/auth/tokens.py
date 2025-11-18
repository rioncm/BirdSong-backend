from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt


def create_access_token(
    subject: str,
    *,
    secret_key: str,
    expires_in: timedelta = timedelta(hours=1),
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    if not subject:
        raise ValueError("subject is required for access token")
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, secret_key, algorithm="HS256")


def decode_access_token(token: str, *, secret_key: str) -> Dict[str, Any]:
    return jwt.decode(token, secret_key, algorithms=["HS256"])
