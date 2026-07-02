from __future__ import annotations

from core.base_platform import Account, RegisterConfig
from platforms.imgs_weryai import plugin as weryai_plugin
from platforms.imgs_weryai.plugin import ImgsWeryaiPlatform


def test_imgs_weryai_daily_sign_in_refreshes_balance_and_syncs_imgs2api(monkeypatch):
    sync_calls: list[dict] = []
    sign_calls: list[dict] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.access_token = kwargs.get("access_token", "")
            self.proxy = kwargs.get("proxy", "")

        def daily_sign_in(self, **kwargs):
            sign_calls.append({"proxy": self.proxy, **kwargs})
            return {
                "status": "signed",
                "signed": True,
                "already_signed": False,
                "day": 2,
                "next_day": 3,
                "reward_amount": 0.3,
            }

        def fetch_account_state(self, **kwargs):
            assert kwargs["access_token"] == "tok_123"
            assert kwargs["team_id"] == "team_123"
            assert kwargs["product_id"] == "327805"
            return {
                "access_token": "tok_123",
                "authorization": "tok_123",
                "team_id": "team_123",
                "teamId": "team_123",
                "product_id": "327805",
                "productId": "327805",
                "credits_balance": 11,
                "remaining_credits": 11,
                "balance": 11,
                "summary": {
                    "valid": True,
                    "email": "acct@example.com",
                    "team_id": "team_123",
                    "teamId": "team_123",
                    "product_id": "327805",
                    "productId": "327805",
                    "credits_balance": 11,
                    "remaining_credits": 11,
                    "balance": 11,
                    "account_overview": {
                        "valid": True,
                        "email": "acct@example.com",
                        "team_id": "team_123",
                        "teamId": "team_123",
                        "product_id": "327805",
                        "productId": "327805",
                        "remaining_credits": 11,
                    },
                },
            }

    def fake_load_state(self, account, *, force_refresh=False):
        assert force_refresh is False
        return {
            "access_token": "tok_123",
            "authorization": "tok_123",
            "team_id": "team_123",
            "product_id": "327805",
            "summary": {
                "valid": True,
                "remaining_credits": 10.7,
                "account_overview": {
                    "team_id": "team_123",
                    "product_id": "327805",
                    "remaining_credits": 10.7,
                    "imgs_weryai_daily_sign_in_next_day": 2,
                },
            },
        }

    def fake_sync(account, *, log_fn=None, heartbeat=False, balance=False, check=False, **kwargs):
        sync_calls.append(
            {
                "account": account,
                "heartbeat": heartbeat,
                "balance": balance,
                "check": check,
            }
        )
        return {"ok": True, "account": {"id": 9}}

    monkeypatch.setattr(weryai_plugin, "WeryAIClient", FakeClient)
    monkeypatch.setattr(ImgsWeryaiPlatform, "_load_state", fake_load_state)
    monkeypatch.setattr(weryai_plugin, "sync_account_to_imgs2api", fake_sync)

    platform = ImgsWeryaiPlatform(RegisterConfig(executor_type="protocol"))
    account = Account(
        platform="imgs_weryai",
        email="acct@example.com",
        password="",
        token="tok_123",
        extra={
            "access_token": "tok_123",
            "team_id": "team_123",
            "product_id": "327805",
            "proxy_url": "socks5://xray:20015",
        },
    )

    result = platform.execute_action("daily_sign_in", account, {})

    assert result["ok"] is True
    assert result["data"]["daily_sign_in_status"] == "signed"
    assert result["data"]["remaining_credits"] == 11
    assert result["data"]["imgs2api_synced"] is True
    assert sign_calls == [
        {
            "proxy": "socks5://xray:20015",
            "team_id": "team_123",
            "product_id": "327805",
            "day": 2,
        }
    ]
    assert len(sync_calls) == 1
    assert sync_calls[0]["balance"] is True
    assert sync_calls[0]["heartbeat"] is False
    assert sync_calls[0]["check"] is False
    assert sync_calls[0]["account"].extra["remaining_credits"] == 11
    assert sync_calls[0]["account"].extra["daily_sign_in"]["status"] == "signed"
