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
