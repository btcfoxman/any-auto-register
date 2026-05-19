from __future__ import annotations

from unittest.mock import Mock, patch

from core.base_platform import Account
from core.freebeat2api_sync import (
    Freebeat2ApiClient,
    build_freebeat2api_payload,
    sync_account_to_freebeat2api,
)


def test_build_freebeat2api_payload_from_freebeat_account():
    account = Account(
        platform="freebeat",
        email="user@example.com",
        password="",
        user_id="user_123",
        token="tok_123",
        extra={
            "proxy_url": "http://127.0.0.1:10809",
            "freebeat2api_max_concurrency": "3",
        },
    )

    payload = build_freebeat2api_payload(account, max_concurrency=1)

    assert payload["name"] == "user@example.com"
    assert payload["token"] == "tok_123"
    assert payload["email"] == "user@example.com"
    assert payload["user_id"] == "user_123"
    assert payload["proxy_url"] == "http://127.0.0.1:10809"
    assert payload["max_concurrency"] == 3
    assert payload["enabled"] is True
    assert payload["enable_auto_maintenance"] is True


def test_build_freebeat2api_payload_can_disable_remote_maintenance():
    account = Account(
        platform="freebeat",
        email="user@example.com",
        password="",
        extra={
            "access_token": "tok_123",
            "freebeat2api_enable_auto_maintenance": "false",
        },
    )

    payload = build_freebeat2api_payload(account)

    assert payload["enable_auto_maintenance"] is False


def test_freebeat2api_client_upserts_account_with_bearer_key():
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"id": 12, "name": "acct"}

    with patch("core.freebeat2api_sync.requests.post", return_value=resp) as post:
        client = Freebeat2ApiClient("http://localhost:8788/", "key")
        result = client.upsert_account({"name": "acct", "token": "tok"})

    assert result["id"] == 12
    assert post.call_args.args[0] == "http://localhost:8788/api/accounts"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer key"
    assert post.call_args.kwargs["headers"]["X-API-Key"] == "key"


def test_sync_account_to_freebeat2api_posts_and_refreshes_balance_and_sign_in():
    account = Account(
        platform="freebeat",
        email="acct@example.com",
        password="",
        user_id="user_123",
        token="tok_123",
        extra={},
    )
    created = Mock()
    created.raise_for_status = Mock()
    created.json.return_value = {"id": 9, "name": "acct@example.com"}
    balance = Mock()
    balance.raise_for_status = Mock()
    balance.json.return_value = {"data": {"totalCredits": 1000}}
    sign_in = Mock()
    sign_in.raise_for_status = Mock()
    sign_in.json.return_value = {"status": "signed"}

    with patch("core.freebeat2api_sync._get_freebeat2api_config", return_value=("http://localhost:8788", "key", 1, True)):
        with patch("core.freebeat2api_sync.requests.post", side_effect=[created, sign_in]) as post:
            with patch("core.freebeat2api_sync.requests.get", return_value=balance) as get:
                result = sync_account_to_freebeat2api(account, balance=True, sign_in=True)

    assert result["ok"] is True
    assert result["account"]["id"] == 9
    assert result["balance"]["data"]["totalCredits"] == 1000
    assert result["sign_in"]["status"] == "signed"
    body = post.call_args_list[0].kwargs["json"]
    assert body["token"] == "tok_123"
    assert body["user_id"] == "user_123"
    assert body["enable_auto_maintenance"] is True
    assert post.call_args_list[0].args[0] == "http://localhost:8788/api/accounts"
    assert get.call_args.args[0] == "http://localhost:8788/api/accounts/9/balance"
    assert post.call_args_list[1].args[0] == "http://localhost:8788/api/accounts/9/sign-in"


def test_sync_account_to_freebeat2api_uses_global_auto_maintenance_setting():
    account = Account(
        platform="freebeat",
        email="acct@example.com",
        password="",
        token="tok_123",
        extra={},
    )
    created = Mock()
    created.raise_for_status = Mock()
    created.json.return_value = {"id": 9, "name": "acct@example.com"}

    with patch("core.freebeat2api_sync._get_freebeat2api_config", return_value=("http://localhost:8788", "key", 1, False)):
        with patch("core.freebeat2api_sync.requests.post", return_value=created) as post:
            result = sync_account_to_freebeat2api(account)

    assert result["ok"] is True
    body = post.call_args.kwargs["json"]
    assert body["enable_auto_maintenance"] is False


def test_sync_account_to_freebeat2api_keeps_upsert_when_optional_refresh_fails():
    account = Account(
        platform="freebeat",
        email="acct@example.com",
        password="",
        token="tok_123",
        extra={},
    )
    created = Mock()
    created.raise_for_status = Mock()
    created.json.return_value = {"id": 9, "name": "acct@example.com"}
    logs: list[str] = []

    with patch("core.freebeat2api_sync._get_freebeat2api_config", return_value=("http://localhost:8788", "key", 1, True)):
        with patch("core.freebeat2api_sync.requests.post", return_value=created):
            with patch("core.freebeat2api_sync.requests.get", side_effect=RuntimeError("findCredits 403")):
                result = sync_account_to_freebeat2api(account, log_fn=logs.append, balance=True)

    assert result["ok"] is True
    assert result["account"]["id"] == 9
    assert result["balance"]["ok"] is False
    assert "findCredits 403" in result["balance"]["error"]
    assert any("balance refresh failed" in item for item in logs)


def test_sync_account_to_freebeat2api_skips_when_unconfigured():
    with patch("core.freebeat2api_sync._get_freebeat2api_config", return_value=("", "", 1, True)):
        account = Account(platform="freebeat", email="acct@example.com", password="", token="tok_123", extra={})

        assert sync_account_to_freebeat2api(account) is False
