import time

import pytest
from fastapi import HTTPException

import server


def test_parse_txt_content_maps_basic_unit_fields():
    text = """
Назва: Test Unit
UAVs: Orlan-10; Lancet
Lat: 47.12
Lng: 36.34
"""

    units = server.parse_txt_content(text)

    assert len(units) == 1
    assert units[0]["name"] == "Test Unit"
    assert units[0]["uavs"] == ["Orlan-10", "Lancet"]
    assert units[0]["lat"] == 47.12
    assert units[0]["lng"] == 36.34


def test_validate_token_accepts_unexpired_bearer_token():
    token = "test-token"
    server.manager.allowed_tokens[token] = time.time() + 60

    assert server.validate_token(f"Bearer {token}") is True

    server.manager.allowed_tokens.pop(token, None)


def test_validate_token_rejects_expired_bearer_token():
    token = "expired-token"
    server.manager.allowed_tokens[token] = time.time() - 1

    assert server.validate_token(f"Bearer {token}") is False
    assert token not in server.manager.allowed_tokens


def test_admin_password_is_required(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    with pytest.raises(HTTPException) as exc:
        server.get_admin_password()

    assert exc.value.status_code == 503
