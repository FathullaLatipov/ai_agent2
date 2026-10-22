"""Password hashing and JWT handling.

Access tokens carry only the user id; org membership and role are resolved from
the database on every request (see core/deps.py). Stream tokens are short-lived,
single-purpose tokens for the SSE endpoint (EventSource cannot send headers).
"""

import datetime as dt
import uuid

import bcrypt
import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def _encode(claims: dict, expires_delta: dt.timedelta) -> str:
    settings = get_settings()
    now = dt.datetime.now(dt.UTC)
    payload = {**claims, "iat": now, "exp": now + expires_delta, "jti": uuid.uuid4().hex}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(user_id: uuid.UUID) -> str:
    settings = get_settings()
    return _encode(
        {"sub": str(user_id), "purpose": "access"},
        dt.timedelta(minutes=settings.access_token_minutes),
    )


def create_stream_token(user_id: uuid.UUID, run_id: uuid.UUID) -> str:
    settings = get_settings()
    return _encode(
        {"sub": str(user_id), "purpose": "stream", "run_id": str(run_id)},
        dt.timedelta(seconds=settings.stream_token_seconds),
    )


def create_project_stream_token(user_id: uuid.UUID, project_id: uuid.UUID) -> str:
    settings = get_settings()
    return _encode(
        {"sub": str(user_id), "purpose": "project-stream", "project_id": str(project_id)},
        dt.timedelta(seconds=settings.stream_token_seconds),
    )


class TokenError(Exception):
    pass


def decode_token(token: str, expected_purpose: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError("invalid or expired token") from exc
    if payload.get("purpose") != expected_purpose:
        raise TokenError("wrong token purpose")
    return payload
