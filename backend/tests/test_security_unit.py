import uuid

import pytest

from app.core.security import (
    TokenError,
    create_access_token,
    create_stream_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("s3cret-password!")
    assert h != "s3cret-password!"
    assert verify_password("s3cret-password!", h)
    assert not verify_password("wrong", h)


def test_verify_password_handles_garbage_hash():
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_access_token_roundtrip():
    uid = uuid.uuid4()
    payload = decode_token(create_access_token(uid), expected_purpose="access")
    assert payload["sub"] == str(uid)


def test_token_purpose_enforced():
    uid, rid = uuid.uuid4(), uuid.uuid4()
    stream = create_stream_token(uid, rid)
    with pytest.raises(TokenError):
        decode_token(stream, expected_purpose="access")
    payload = decode_token(stream, expected_purpose="stream")
    assert payload["run_id"] == str(rid)


def test_tampered_token_rejected():
    token = create_access_token(uuid.uuid4())
    with pytest.raises(TokenError):
        decode_token(token[:-2] + "xx", expected_purpose="access")
