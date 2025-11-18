from __future__ import annotations

import base64
import os
from hashlib import pbkdf2_hmac
from typing import Tuple


_ALGORITHM = "sha256"
_ITERATIONS = 390000
_SALT_BYTES = 16


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return pbkdf2_hmac(_ALGORITHM, password.encode("utf-8"), salt, iterations, dklen=32)


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must be non-empty")
    salt = os.urandom(_SALT_BYTES)
    derived = _derive(password, salt, _ITERATIONS)
    return "pbkdf2_{algo}${iters}${salt}${digest}".format(
        algo=_ALGORITHM,
        iters=_ITERATIONS,
        salt=base64.b64encode(salt).decode("utf-8"),
        digest=base64.b64encode(derived).decode("utf-8"),
    )


def verify_password(password: str, hashed: str) -> bool:
    try:
        algorithm, iterations, salt, digest = _parse_hash(hashed)
    except ValueError:
        return False
    derived = pbkdf2_hmac(
        algorithm,
        password.encode("utf-8"),
        base64.b64decode(salt),
        iterations,
        dklen=32,
    )
    return base64.b64encode(derived).decode("utf-8") == digest


def _parse_hash(encoded: str) -> Tuple[str, int, str, str]:
    if not encoded:
        raise ValueError("Invalid password hash")
    parts = encoded.split("$")
    if len(parts) != 4 or not parts[0].startswith("pbkdf2_"):
        raise ValueError("Invalid password hash")
    algorithm = parts[0].replace("pbkdf2_", "")
    iterations = int(parts[1])
    salt = parts[2]
    digest = parts[3]
    return algorithm, iterations, salt, digest
