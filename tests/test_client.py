"""Unit tests for elnet/client.py using synthetic fixtures and mocks."""

import json
from pathlib import Path
import pytest
import requests

from elnet.client import ELNetClient
from elnet.crypto import decrypt_payload
from elnet.schemas import (
    ErrorCategory,
    ElnetError,
    ElnetAuthError,
    ElnetHttpError,
    ElnetTimeoutError,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def synthetic_keys():
    skey = b"0123456789abcdef0123456789abcdef"
    ivkey = b"0123456789abcdef"
    return skey, ivkey


@pytest.fixture
def login_fixture():
    with open(FIXTURES_DIR / "login_success.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def homedata_fixture():
    with open(FIXTURES_DIR / "homedata_success.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_login_success(synthetic_keys, login_fixture, requests_mock):
    skey, ivkey = synthetic_keys
    client = ELNetClient(base_url="http://test-server:8091", skey=skey, ivkey=ivkey)

    def login_callback(request):
        # Verify request has NO Authorization header
        assert "Authorization" not in request.headers
        assert request.headers.get("Content-Type") == "application/json"

        body = json.loads(request.body)
        assert "InputType" in body
        decrypted = decrypt_payload(body["InputType"], skey, ivkey)
        assert decrypted["Access"] == "Live"
        assert decrypted["UserName"] == "user123"
        assert decrypted["Password"] == "pass123"

        return requests_mock.create_response(request, status_code=200, json=login_fixture)

    requests_mock.register_uri("POST", "http://test-server:8091/api/Dashboard/login", text=json.dumps(login_fixture))

    result = client.login("user123", "pass123")
    assert result["success"] is True
    assert result["flat_id"] == 1042
    assert client.is_authenticated is True
    assert client.meter_id == 1042


def test_login_failure(synthetic_keys, requests_mock):
    skey, ivkey = synthetic_keys
    client = ELNetClient(base_url="http://test-server:8091", skey=skey, ivkey=ivkey)

    failure_response = {
        "Status": "Failed",
        "Message": "Invalid Credentials",
        "Data": None,
    }
    requests_mock.post("http://test-server:8091/api/Dashboard/login", json=failure_response)

    with pytest.raises(ElnetAuthError) as exc_info:
        client.login("bad_user", "bad_pass")
    assert "Invalid Credentials" in str(exc_info.value)
    assert not client.is_authenticated


def test_homedata_success(synthetic_keys, login_fixture, homedata_fixture, requests_mock):
    skey, ivkey = synthetic_keys
    client = ELNetClient(base_url="http://test-server:8091", skey=skey, ivkey=ivkey)

    requests_mock.post("http://test-server:8091/api/Dashboard/login", json=login_fixture)
    client.login("user", "pass")

    def homedata_matcher(request):
        auth = request.headers.get("Authorization")
        if auth != f"Bearer {login_fixture['Data']['AuthToken']}":
            return False
        body = json.loads(request.body)
        decrypted = decrypt_payload(body["InputType"], skey, ivkey)
        return decrypted.get("MeterID") == 1042

    requests_mock.post(
        "http://test-server:8091/api/Dashboard/HomeData",
        additional_matcher=homedata_matcher,
        json=homedata_fixture,
    )

    data = client.get_home_data()
    assert data["DeviceId"] == 1042
    assert data["kWhRecToday"] == 12.5
    assert data["MeterBal"] == 845.5


def test_homedata_unauthorized_clears_token(synthetic_keys, login_fixture, requests_mock):
    skey, ivkey = synthetic_keys
    client = ELNetClient(base_url="http://test-server:8091", skey=skey, ivkey=ivkey)

    requests_mock.post("http://test-server:8091/api/Dashboard/login", json=login_fixture)
    client.login("user", "pass")
    assert client.is_authenticated

    requests_mock.post("http://test-server:8091/api/Dashboard/HomeData", status_code=401)

    with pytest.raises(ElnetError) as exc_info:
        client.get_home_data()

    assert exc_info.value.category == ErrorCategory.AUTHORIZATION_ERROR
    assert not client.is_authenticated
