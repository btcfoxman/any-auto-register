from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

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
    assert payload["vuserid"] == "vuid"
    assert payload["vdevice_guid"] == "device"
    assert payload["main_login"] == "wx"
    assert payload["enabled"] is True
    assert payload["enable_auto_maintenance"] is False


def test_build_lingya2api_payload_can_explicitly_enable_remote_auto_maintenance():
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        extra={
            "cookies": "v_vusession=session; v_vurefresh=refresh; v_vuserid=vuid; vdevice_guid=device",
            "lingya2api_enable_auto_maintenance": "true",
        },
    )

    payload = build_lingya2api_payload(account)

    assert payload["enable_auto_maintenance"] is True


def test_build_lingya2api_payload_rebuilds_cookie_from_split_fields():
    account = Account(
        platform="lingya_qq",
        email="acct",
        password="",
        user_id="vuid",
        token="session",
        extra={
            "vurefresh": "refresh",
            "vdevice_guid": "device",
        },
    )

    payload = build_lingya2api_payload(account)

    assert payload["vuserid"] == "vuid"
    assert payload["vdevice_guid"] == "device"
    assert "vdevice_guid=device" in payload["cookie"]
    assert "v_vuserid=vuid" in payload["cookie"]
    assert "v_vusession=session" in payload["cookie"]
    assert "v_vurefresh=refresh" in payload["cookie"]


def test_build_lingya2api_payload_requires_vdevice_guid():
    account = Account(
        platform="lingya_qq",
        email="acct",
        password="",
        extra={"cookies": "v_vusession=session; v_vuserid=vuid"},
    )

    with pytest.raises(ValueError, match="missing vdevice_guid"):
        build_lingya2api_payload(account)


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
    body = post.call_args_list[0].kwargs["json"]
    assert body["vdevice_guid"] == "device"
    assert body["vuserid"] == "vuid"
    assert body["enable_auto_maintenance"] is False
    assert "vdevice_guid=device" in body["cookie"]
    assert post.call_args_list[0].args[0] == "http://localhost:8000/api/accounts"
    assert post.call_args_list[1].args[0] == "http://localhost:8000/api/accounts/9/heartbeat"


def test_sync_account_to_lingya2api_skips_when_unconfigured():
    with patch("core.lingya2api_sync._get_lingya2api_config", return_value=("", "", 1)):
        account = Account(platform="lingya_qq", email="acct", password="", extra={})

        assert sync_account_to_lingya2api(account) is False
