from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from lib.data.tables import bootstrap_state, users

from .passwords import hash_password


@dataclass
class AdminBootstrapper:
    session: Session

    def admin_exists(self) -> bool:
        result = (
            self.session.execute(
                select(users.c.user_id).where(users.c.role == "admin")
            )
            .scalars()
            .first()
        )
        return result is not None

    def ensure_admin(self, email: str, *, password: Optional[str] = None) -> str:
        if not email:
            raise ValueError("email is required")
        normalized_email = email.strip().lower()
        if not normalized_email:
            raise ValueError("email must be non-empty")

        existing = (
            self.session.execute(
                select(users).where(users.c.email == normalized_email)
            )
            .mappings()
            .first()
        )
        password_hash = hash_password(password or _generate_temporary_password())

        if existing:
            if existing["role"] != "admin":
                self.session.execute(
                    update(users)
                    .where(users.c.user_id == existing["user_id"])
                    .values(role="admin", password_hash=password_hash, is_active=True)
                )
            else:
                self.session.execute(
                    update(users)
                    .where(users.c.user_id == existing["user_id"])
                    .values(password_hash=password_hash, is_active=True)
                )
            user_id = existing["user_id"]
        else:
            user_id = uuid4().hex
            self.session.execute(
                insert(users).values(
                    user_id=user_id,
                    email=normalized_email,
                    role="admin",
                    password_hash=password_hash,
                    is_active=True,
                )
            )

        self.session.execute(
            insert(bootstrap_state)
            .values(state_key="admin_initialized", state_value={"email": normalized_email})
            .prefix_with("OR REPLACE")
        )
        self.session.commit()
        return user_id


def _generate_temporary_password() -> str:
    return uuid4().hex
