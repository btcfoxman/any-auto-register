from __future__ import annotations

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
