from __future__ import annotations

import json

from core.base_platform import Account
from platforms.imgs_weryai import core as weryai_core


class FakeCookies:
    jar = []

    def set(self, *args, **kwargs):
        return None


class FakeSession:
    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.calls.append({"args": args, **kwargs})
        self.cookies = FakeCookies()


class FakeResponse:
    text = "{}"

    def raise_for_status(self):
        return None

    def json(self):
        return {}


class FakeRetrySession:
    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.impersonate = kwargs["impersonate"]
        self.cookies = FakeCookies()
        self.calls.append({"event": "session", "impersonate": self.impersonate})

    def request(self, method, url, **kwargs):
        self.calls.append({"event": "request", "impersonate": self.impersonate, "method": method, "url": url})
        if self.impersonate == "chrome134":
            raise RuntimeError("Impersonating chrome134 is not supported")
        return FakeResponse()


class FakeSignResponse:
    text = '{"status":200,"data":{"reward":0.3,"next_day":3}}'

    def raise_for_status(self):
        return None

    def json(self):
        return {"status": 200, "data": {"reward": 0.3, "next_day": 3}}


class FakeSignSession:
    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.cookies = FakeCookies()
        self.calls.append({"event": "session", "args": args, **kwargs})

    def request(self, method, url, **kwargs):
        self.calls.append({"event": "request", "method": method, "url": url, **kwargs})
        return FakeSignResponse()


class FakeAlreadySignedResponse:
    text = '{"status":409,"message":"already signed today"}'

    def raise_for_status(self):
        return None

    def json(self):
        return {"status": 409, "message": "already signed today"}


class FakeAlreadySignedSession(FakeSignSession):
    def request(self, method, url, **kwargs):
        self.calls.append({"event": "request", "method": method, "url": url, **kwargs})
        return FakeAlreadySignedResponse()


def test_weryai_client_uses_supported_default_impersonate(monkeypatch):
    FakeSession.calls = []
    monkeypatch.setattr(weryai_core, "supported_weryai_impersonates", lambda: {"chrome"})
    monkeypatch.setattr(weryai_core, "Session", FakeSession)

    client = weryai_core.WeryAIClient(log_fn=lambda message: None)

    assert FakeSession.calls[0]["impersonate"] == "chrome"
    assert client.auth_state()["weryai_impersonate"] == "chrome"


def test_weryai_client_falls_back_from_unsupported_impersonate(monkeypatch):
    FakeSession.calls = []
    logs: list[str] = []
    monkeypatch.setattr(weryai_core, "supported_weryai_impersonates", lambda: {"chrome"})
    monkeypatch.setattr(weryai_core, "Session", FakeSession)

    client = weryai_core.WeryAIClient(log_fn=logs.append, impersonate="chrome134")

    assert FakeSession.calls[0]["impersonate"] == "chrome"
    assert client.impersonate == "chrome"
    assert logs == ["WeryAI impersonate chrome134 is not supported; using chrome"]


def test_weryai_account_context_normalizes_legacy_impersonate(monkeypatch):
    monkeypatch.setattr(weryai_core, "supported_weryai_impersonates", lambda: {"chrome", "chrome136"})
    account = Account(
        platform="imgs_weryai",
        email="user@example.com",
        password="secret",
        extra={"account_overview": {"weryai_impersonate": "chrome134"}},
    )

    context = weryai_core.extract_weryai_account_context(account)

    assert context["impersonate"] == "chrome"


def test_weryai_request_retries_with_fallback_impersonate(monkeypatch):
    FakeRetrySession.calls = []
    logs: list[str] = []
    monkeypatch.setattr(weryai_core, "supported_weryai_impersonates", lambda: set())
    monkeypatch.setattr(weryai_core, "Session", FakeRetrySession)

    client = weryai_core.WeryAIClient(log_fn=logs.append, impersonate="chrome134")
    data = client._request_json("POST", "/api/v1/email/ticket", body={}, label="email ticket")

    assert data == {}
    assert [item["impersonate"] for item in FakeRetrySession.calls if item["event"] == "request"] == ["chrome134", "chrome"]
    assert client.impersonate == "chrome"
    assert logs == ["WeryAI retry email ticket with impersonate chrome"]


def test_weryai_daily_sign_in_uses_auth_params_and_normalizes_result(monkeypatch):
    FakeSignSession.calls = []
    monkeypatch.setattr(weryai_core, "supported_weryai_impersonates", lambda: {"chrome"})
    monkeypatch.setattr(weryai_core, "Session", FakeSignSession)

    client = weryai_core.WeryAIClient(
        access_token="Bearer tok_123",
        df_id="df_123",
        client_ip="43.153.141.41",
        log_fn=lambda message: None,
    )

    result = client.daily_sign_in(team_id="team_123", product_id="327805", day=2)

    request = next(item for item in FakeSignSession.calls if item["event"] == "request")
    assert request["method"] == "POST"
    assert request["url"].endswith("/api/v1/auth/sign/everydaySign")
    assert request["params"]["app_key"] == "20006012"
    assert request["params"]["ver_code"] == "1.9.0"
    assert request["params"]["lang"] == "en"
    assert request["params"]["client_ip"] == "43.153.141.41"
    assert request["params"]["df_id"] == "df_123"
    assert request["params"]["teamId"] == "team_123"
    assert request["params"]["productId"] == "327805"
    assert request["headers"]["authorization"] == "Bearer tok_123"
    assert json.loads(request["data"]) == {"day": 2, "team_id": "team_123", "product_id": 327805}
    assert result["status"] == "signed"
    assert result["signed"] is True
    assert result["day"] == 2
    assert result["next_day"] == 3
    assert result["reward_amount"] == 0.3


def test_weryai_daily_sign_in_treats_already_signed_as_successful_state(monkeypatch):
    FakeAlreadySignedSession.calls = []
    monkeypatch.setattr(weryai_core, "supported_weryai_impersonates", lambda: {"chrome"})
    monkeypatch.setattr(weryai_core, "Session", FakeAlreadySignedSession)

    client = weryai_core.WeryAIClient(access_token="Bearer tok_123", log_fn=lambda message: None)
    result = client.daily_sign_in(team_id="team_123", product_id="327805", day=2)

    assert result["status"] == "already_signed"
    assert result["signed"] is False
    assert result["already_signed"] is True
