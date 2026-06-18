from __future__ import annotations

from unittest.mock import Mock, patch

from core.base_platform import Account
from core.imgs2api_sync import (
    build_imgs2api_payload,
    sync_account_to_imgs2api,
)


def test_build_imgs2api_payload_rewrites_proxy_host_for_downstream():
    account = Account(
        platform="imgs_weryai",
        email="acct@example.com",
        password="",
        token="tok_123",
        extra={
            "proxy_url": "socks5://xray:20015",
            "team_id": "team_123",
        },
    )

    payload = build_imgs2api_payload(
        account,
        extra_overrides={"imgs2api_proxy_host_override": "192.168.3.5"},
    )

    assert payload["proxy_url"] == "socks5://192.168.3.5:20015"


def test_build_imgs2api_payload_keeps_explicit_imgs2api_proxy_url():
    account = Account(
        platform="imgs_weryai",
        email="acct@example.com",
        password="",
        token="tok_123",
        extra={
            "imgs2api_proxy_url": "socks5://proxy-for-imgs2api:21015",
            "proxy_url": "socks5://xray:20015",
        },
    )

    payload = build_imgs2api_payload(
        account,
        extra_overrides={"imgs2api_proxy_host_override": "192.168.3.5"},
    )

    assert payload["proxy_url"] == "socks5://proxy-for-imgs2api:21015"


def test_sync_account_to_imgs2api_uses_global_proxy_host_override():
    account = Account(
        platform="imgs_weryai",
        email="acct@example.com",
        password="",
        token="tok_123",
        extra={"proxy_url": "socks5://user:pass@xray:20015"},
    )
    created = Mock()
    created.raise_for_status = Mock()
    created.json.return_value = {"id": 9, "name": "acct@example.com"}

    with patch("core.imgs2api_sync._get_imgs2api_config", return_value=("http://localhost:8790", "sk-key", 1, True)):
        with patch("core.imgs2api_sync._get_imgs2api_proxy_host_override", return_value="192.168.3.5"):
            with patch("core.imgs2api_sync.requests.post", return_value=created) as post:
                result = sync_account_to_imgs2api(account)

    assert result["ok"] is True
    body = post.call_args.kwargs["json"]
    assert body["proxy_url"] == "socks5://user:pass@192.168.3.5:20015"

