from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from core.base_platform import Account
from core.quickframe2api_sync import (
    QUICKFRAME2API_DEFAULT_API_KEY,
    QuickFrame2ApiClient,
    QuickFrame2ApiAuthError,
    build_quickframe2api_payload,
    _get_quickframe2api_config,
    sync_account_to_quickframe2api,
)


def test_build_quickframe2api_payload_from_quickframe_account():
    account = Account(
        platform="quickframe",
        email="new@example.com",
        password="",
        user_id="108334",
        token="tok_123",
        extra={
            "cookie_header": "qf_session=sess_123; auth0=auth",
            "workspace_id": "47784",
            "free_exports_remaining": 1,
            "proxy_url": "http://proxy.example:8080",
            "quickframe2api_max_concurrency": "3",
        },
    )

    payload = build_quickframe2api_payload(account, max_concurrency=1)

    assert payload["name"] == "new@example.com"
    assert payload["token"] == "tok_123"
    assert payload["access_token"] == "tok_123"
    assert payload["email"] == "new@example.com"
    assert payload["user_id"] == "108334"
    assert payload["workspace_id"] == "47784"
    assert payload["cookies"] == "qf_session=sess_123; auth0=auth"
    assert payload["proxy_url"] == "http://proxy.example:8080"
    assert payload["max_concurrency"] == 3
    assert payload["enable_auto_maintenance"] is True


def test_build_quickframe2api_payload_disables_remote_maintenance_when_keepalive_stopped():
    account = Account(
        platform="quickframe",
        email="new@example.com",
        password="",
        extra={
            "access_token": "tok_123",
            "account_overview": {
                "workspace_id": "47784",
                "quickframe_keepalive_disabled": True,
                "quickframe2api_enable_auto_maintenance": True,
            },
        },
    )

    payload = build_quickframe2api_payload(account, auto_maintenance_default=True)

    assert payload["workspace_id"] == "47784"
    assert payload["enable_auto_maintenance"] is False


def test_quickframe2api_client_upserts_account_with_api_key_headers():
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"id": 12, "name": "new@example.com"}

    with patch("core.quickframe2api_sync.requests.post", return_value=resp) as post:
        client = QuickFrame2ApiClient("http://localhost:8789/", "sk-key")
        result = client.upsert_account({"name": "new@example.com", "token": "tok_123"})

    assert result["id"] == 12
    assert post.call_args.args[0] == "http://localhost:8789/api/accounts"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-key"
    assert post.call_args.kwargs["headers"]["X-API-Key"] == "sk-key"
    assert post.call_args.kwargs["headers"]["X-Admin-Token"] == "sk-key"


def test_quickframe2api_client_reports_auth_error_on_401():
    resp = Mock()
    resp.status_code = 401

    with patch("core.quickframe2api_sync.requests.post", return_value=resp):
        client = QuickFrame2ApiClient("http://localhost:8789/", "bad-key")
        with pytest.raises(QuickFrame2ApiAuthError, match="quickframe2api_api_key"):
            client.upsert_account({"name": "new@example.com", "token": "tok_123"})


def test_quickframe2api_config_uses_default_key_when_url_configured(monkeypatch):
    import core.config_store as config_module

    values = {
        "quickframe2api_url": "http://localhost:8789",
        "quickframe2api_api_key": "",
        "quickframe2api_max_concurrency": "2",
        "quickframe2api_enable_auto_maintenance": "true",
    }
    monkeypatch.setattr(config_module.config_store, "get", lambda key, default="": values.get(key, default))

    base_url, api_key, max_concurrency, auto_maintenance = _get_quickframe2api_config()

    assert base_url == "http://localhost:8789"
    assert api_key == QUICKFRAME2API_DEFAULT_API_KEY
    assert max_concurrency == 2
    assert auto_maintenance is True


def test_sync_account_to_quickframe2api_posts_heartbeat_and_check():
    account = Account(
        platform="quickframe",
        email="new@example.com",
        password="",
        user_id="108334",
        token="tok_123",
        extra={
            "cookie_header": "qf_session=sess_123",
            "workspace_id": "47784",
            "free_exports_remaining": 1,
        },
    )
    created = Mock()
    created.raise_for_status = Mock()
    created.json.return_value = {"id": 9, "name": "new@example.com"}
    heartbeat = Mock()
    heartbeat.raise_for_status = Mock()
    heartbeat.json.return_value = {"ok": True, "token_ok": True}
    check = Mock()
    check.raise_for_status = Mock()
    check.json.return_value = {"ok": True, "valid": True}

    with patch("core.quickframe2api_sync._get_quickframe2api_config", return_value=("http://localhost:8789", "sk-key", 2, True)):
        with patch("core.quickframe2api_sync.requests.post", side_effect=[created, heartbeat, check]) as post:
            result = sync_account_to_quickframe2api(account, heartbeat=True, check=True)

    assert result["ok"] is True
    assert result["account"]["id"] == 9
    assert result["heartbeat"]["token_ok"] is True
    assert result["check"]["valid"] is True
    body = post.call_args_list[0].kwargs["json"]
    assert body["token"] == "tok_123"
    assert body["workspace_id"] == "47784"
    assert body["free_exports_remaining"] == 1
    assert body["max_concurrency"] == 2
    assert post.call_args_list[0].args[0] == "http://localhost:8789/api/accounts"
    assert post.call_args_list[1].args[0] == "http://localhost:8789/api/accounts/9/heartbeat"
    assert post.call_args_list[2].args[0] == "http://localhost:8789/api/accounts/9/check"
    assert result["payload"]["token"] == "***"
    assert result["payload"]["cookies"] == "***"


def test_sync_account_to_quickframe2api_skips_when_unconfigured():
    account = Account(platform="quickframe", email="new@example.com", password="", token="tok_123")

    with patch("core.quickframe2api_sync._get_quickframe2api_config", return_value=("", "", 1, True)):
        assert sync_account_to_quickframe2api(account) is False


def test_registration_auto_sync_pushes_quickframe_account_to_quickframe2api(monkeypatch):
    import application.tasks as tasks
    import core.quickframe2api_sync as sync_module

    calls: list[tuple[bool, bool, str]] = []
    logs: list[tuple[str, str]] = []

    class Logger:
        def log(self, message, level="info"):
            logs.append((message, level))

    def fake_sync(account, *, log_fn=None, heartbeat=False, check=False, **kwargs):
        calls.append((heartbeat, check, account.token))
        return {"ok": True, "account": {"id": 9}}

    monkeypatch.setattr(sync_module, "sync_account_to_quickframe2api", fake_sync)
    monkeypatch.setattr(sync_module, "is_quickframe2api_configured", lambda: True)

    account = Account(
        platform="quickframe",
        email="new@example.com",
        password="",
        user_id="108334",
        token="tok_new",
        extra={"access_token": "tok_new", "workspace_id": "47784"},
    )

    tasks._auto_sync_quickframe2api(Logger(), account)

    assert calls == [(True, True, "tok_new")]
    assert any("QuickFrame account synced" in message for message, _ in logs)
