from __future__ import annotations

from unittest.mock import Mock, patch

from core.base_platform import Account
from core.lingya2api_sync import (
    Lingya2ApiClient,
    build_lingya2api_payload,
    sync_account_to_lingya2api,
)


def test_build_lingya2api_payload_from_lingya_account():
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        extra={
            "cookies": "v_vusession=session; v_vurefresh=refresh; v_vuserid=vuid; vdevice_guid=device",
            "lingya2api_max_concurrency": "3",
        },
    )

    payload = build_lingya2api_payload(account, max_concurrency=1)

    assert payload["name"] == "+8613800138000"
    assert payload["max_concurrency"] == 3
    assert "v_vusession=session" in payload["cookie"]
    assert "vdevice_guid=device" in payload["cookie"]
    assert payload["enabled"] is True


def test_lingya2api_client_upserts_account():
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"id": 12, "name": "acct"}

    with patch("core.lingya2api_sync.requests.post", return_value=resp) as post:
        client = Lingya2ApiClient("http://localhost:8000/", "key")
        result = client.upsert_account({"name": "acct", "cookie": "a=b"})

    assert result["id"] == 12
    assert post.call_args.args[0] == "http://localhost:8000/api/accounts"
    assert post.call_args.kwargs["headers"]["x-api-key"] == "key"


def test_sync_account_to_lingya2api_posts_and_heartbeats():
    account = Account(
        platform="lingya_qq",
        email="acct",
        password="",
        extra={
            "cookies": "v_vusession=session; v_vurefresh=refresh; v_vuserid=vuid; vdevice_guid=device",
        },
    )
    created = Mock()
    created.raise_for_status = Mock()
    created.json.return_value = {"id": 9, "name": "acct"}
    heartbeat = Mock()
    heartbeat.raise_for_status = Mock()
    heartbeat.json.return_value = {"timestamp": "1", "token_ok": True}

    with patch("core.lingya2api_sync._get_lingya2api_config", return_value=("http://localhost:8000", "key", 1)):
        with patch("core.lingya2api_sync.requests.post", side_effect=[created, heartbeat]) as post:
            result = sync_account_to_lingya2api(account, heartbeat=True)

    assert result["ok"] is True
    assert result["account"]["id"] == 9
    assert result["heartbeat"]["token_ok"] is True
    assert post.call_args_list[0].args[0] == "http://localhost:8000/api/accounts"
    assert post.call_args_list[1].args[0] == "http://localhost:8000/api/accounts/9/heartbeat"


def test_sync_account_to_lingya2api_skips_when_unconfigured():
    with patch("core.lingya2api_sync._get_lingya2api_config", return_value=("", "", 1)):
        account = Account(platform="lingya_qq", email="acct", password="", extra={})

        assert sync_account_to_lingya2api(account) is False
