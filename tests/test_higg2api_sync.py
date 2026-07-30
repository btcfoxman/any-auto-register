from __future__ import annotations

import base64
import json
from unittest.mock import Mock, patch

from core.base_platform import Account
from core.higg2api_sync import (
    Higg2ApiClient,
    build_higg2api_payload,
    sync_account_to_higg2api,
)


def _jwt(**claims) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return f"header.{encoded}.signature"


def test_build_higg2api_payload_preserves_generation_context():
    token = _jwt(sid="sess_123", sub="user_123", workspace_id="ws_123")
    account = Account(
        platform="higg",
        email="higg@example.com",
        password="",
        token=token,
        extra={
            "cookie_header": "__client=client_123; datadome=dd_123",
            "datadome": "dd_123",
            "proxy_url": "http://proxy.example:8080",
            "folder_id": "folder_123",
            "credits_balance": 86,
            "free_generations": 2,
            "higg2api_max_concurrency": "3",
        },
    )

    payload = build_higg2api_payload(account)

    assert payload["name"] == "higg@example.com"
    assert payload["session_id"] == "sess_123"
    assert payload["user_id"] == "user_123"
    assert payload["workspace_id"] == "ws_123"
    assert payload["folder_id"] == "folder_123"
    assert payload["clerk_jwt"] == token
    assert payload["cookies"] == "__client=client_123; datadome=dd_123"
    assert payload["datadome"] == "dd_123"
    assert payload["proxy_url"] == "http://proxy.example:8080"
    assert payload["last_balance"] == 86
    assert payload["free_generations"] == 2
    assert payload["max_concurrency"] == 1
    assert payload["enable_auto_maintenance"] is True


def test_build_higg2api_payload_disables_remote_maintenance():
    account = Account(
        platform="higg",
        email="higg@example.com",
        password="",
        token=_jwt(sid="sess_123"),
        extra={"higg_keepalive_disabled": True},
    )

    payload = build_higg2api_payload(account)

    assert payload["enable_auto_maintenance"] is False


def test_higg2api_client_uses_bearer_and_api_key_headers():
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = {"id": 12}

    with patch("core.higg2api_sync.requests.post", return_value=response) as post:
        result = Higg2ApiClient("http://localhost:8790/", "sk-key").upsert_account(
            {"name": "higg@example.com"}
        )

    assert result["id"] == 12
    assert post.call_args.args[0] == "http://localhost:8790/api/accounts"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-key"
    assert post.call_args.kwargs["headers"]["X-API-Key"] == "sk-key"


def test_sync_higg2api_upserts_checks_and_masks_secrets():
    account = Account(
        platform="higg",
        email="higg@example.com",
        password="",
        token=_jwt(sid="sess_123"),
        extra={"cookie_header": "__client=client_123"},
    )
    created = Mock()
    created.raise_for_status = Mock()
    created.json.return_value = {"id": 7}
    checked = Mock()
    checked.raise_for_status = Mock()
    checked.json.return_value = {"status": "active"}
    balanced = Mock()
    balanced.raise_for_status = Mock()
    balanced.json.return_value = {"last_balance": 40}

    with patch(
        "core.higg2api_sync._get_higg2api_config",
        return_value=("http://localhost:8790", "sk-key", 1, True),
    ):
        with patch("core.higg2api_sync.requests.post", side_effect=[created, checked]):
            with patch("core.higg2api_sync.requests.get", return_value=balanced):
                result = sync_account_to_higg2api(account, check=True, balance=True)

    assert result["ok"] is True
    assert result["account"]["id"] == 7
    assert result["check"]["status"] == "active"
    assert result["balance"]["last_balance"] == 40
    assert result["payload"]["token"] == "***"
    assert result["payload"]["clerk_jwt"] == "***"
    assert result["payload"]["cookies"] == "***"


def test_sync_higg2api_skips_when_unconfigured():
    account = Account(
        platform="higg",
        email="higg@example.com",
        password="",
        token=_jwt(sid="sess_123"),
    )

    with patch(
        "core.higg2api_sync._get_higg2api_config",
        return_value=("", "", 1, True),
    ):
        assert sync_account_to_higg2api(account) is False


def test_registration_auto_sync_pushes_higg_account(monkeypatch):
    import application.tasks as tasks
    import core.higg2api_sync as sync_module

    calls: list[str] = []
    logs: list[tuple[str, str]] = []

    class Logger:
        def log(self, message, level="info"):
            logs.append((message, level))

    monkeypatch.setattr(sync_module, "is_higg2api_configured", lambda: True)
    monkeypatch.setattr(
        sync_module,
        "sync_account_to_higg2api",
        lambda account, **_: calls.append(account.email) or {"ok": True},
    )
    account = Account(
        platform="higg",
        email="higg@example.com",
        password="",
        token=_jwt(sid="sess_123"),
    )

    tasks._auto_sync_higg2api(Logger(), account)

    assert calls == ["higg@example.com"]
    assert any("Higgsfield account synced" in message for message, _ in logs)
