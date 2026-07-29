from __future__ import annotations

from core.base_platform import Account, RegisterConfig
from core.registry import get, load_all
from infrastructure.platform_runtime import (
    PERSISTED_ACTION_DATA_KEYS,
    STATEFUL_ACTION_IDS,
    _build_account_overview,
)
from infrastructure.config_repository import ConfigRepository
from platforms.higg.core import HiggAuthState, cookie_header_from_any
from platforms.higg.plugin import HiggPlatform
from platforms.higg.protocol_mailbox import HiggProtocolMailboxWorker


def test_higg_platform_is_registered_and_exposes_stateful_actions():
    load_all()

    assert get("higg") is HiggPlatform
    platform = HiggPlatform(config=RegisterConfig(executor_type="protocol"))
    action_ids = {item["id"] for item in platform.get_platform_actions()}

    assert {
        "query_state",
        "refresh_session",
        "keepalive_sync",
        "sync_higg2api",
        "stop_keepalive",
        "resume_keepalive",
    } <= action_ids
    assert "sync_higg2api" in STATEFUL_ACTION_IDS
    assert {"clerk_jwt", "session_id", "datadome", "folder_id"} <= PERSISTED_ACTION_DATA_KEYS
    assert {
        "higg2api_url",
        "higg2api_api_key",
        "higg2api_max_concurrency",
        "higg2api_enable_auto_maintenance",
    } <= ConfigRepository().get_allowed_keys()


def test_cookie_header_accepts_browser_cookie_records():
    value = cookie_header_from_any(
        [
            {"name": "__client", "value": "client_123", "domain": ".higgsfield.ai"},
            {"name": "datadome", "value": "dd_123", "domain": ".higgsfield.ai"},
        ]
    )

    assert value == "__client=client_123; datadome=dd_123"


def test_higg_protocol_registration_completes_clerk_chain(monkeypatch):
    calls: list[object] = []

    class FakeClient:
        user_agent = "test-agent"
        sec_ch_ua = "test-sec-ch"
        sec_ch_ua_platform = '"Windows"'

        def __init__(self, **kwargs):
            calls.append(("init", kwargs["proxy"]))

        def create_signup(self, email, password, captcha):
            calls.append(("signup", email, password, captcha))
            return {"response": {"id": "signup_123"}}

        def prepare_email_verification(self, signup_id):
            calls.append(("prepare", signup_id))

        def attempt_email_verification(self, signup_id, code):
            calls.append(("verify", signup_id, code))
            return {
                "response": {
                    "status": "complete",
                    "created_user_id": "user_123",
                }
            }

        def touch_session(self):
            calls.append("touch")

        def auth_state(self, **kwargs):
            return HiggAuthState(
                email=kwargs["email"],
                user_id=kwargs["user_id"],
                session_id="sess_123",
                token="jwt_123",
                workspace_id="ws_123",
                cookie_header="__client=client_123; datadome=dd_123",
                datadome="dd_123",
            )

        def fetch_account_state(self):
            return {
                "generation_ready": True,
                "credits_balance": 60,
                "free_generations": 1,
                "chips": ["Credits 60", "Free gens 1"],
            }

    monkeypatch.setattr("platforms.higg.protocol_mailbox.HiggClient", FakeClient)
    worker = HiggProtocolMailboxWorker(
        proxy="http://proxy.example:8080",
        turnstile_solver=lambda url, site_key, proxy: "turnstile_123",
    )

    result = worker.run(
        email="higg@example.com",
        password="Password123!",
        otp_callback=lambda: "123456",
    )

    assert ("signup", "higg@example.com", "Password123!", "turnstile_123") in calls
    assert ("verify", "signup_123", "123456") in calls
    assert result["session_id"] == "sess_123"
    assert result["clerk_jwt"] == "jwt_123"
    assert result["workspace_id"] == "ws_123"
    assert result["credits_balance"] == 60
    assert result["account_overview"]["generation_ready"] is True


def test_higg_overview_preserves_balance_and_risk_state():
    overview = _build_account_overview(
        "higg",
        {
            "valid": True,
            "generation_ready": False,
            "workspace_id": "ws_123",
            "credits_balance": 42,
            "free_generations": 2,
            "check_warning": "DataDome required",
        },
    )

    assert overview["workspace_id"] == "ws_123"
    assert overview["credits_balance"] == 42
    assert overview["free_generations"] == 2
    assert overview["check_warning"] == "DataDome required"
    assert "风控状态待更新" in overview["chips"]


def test_higg_query_state_does_not_require_downstream(monkeypatch):
    platform = HiggPlatform(config=RegisterConfig(executor_type="protocol"))
    account = Account(
        platform="higg",
        email="higg@example.com",
        password="",
        token="jwt_old",
    )
    monkeypatch.setattr(
        platform,
        "_load_state",
        lambda _: {
            "valid": True,
            "generation_ready": True,
            "clerk_jwt": "jwt_new",
            "session_id": "sess_123",
            "credits_balance": 12,
            "free_generations": 0,
        },
    )

    result = platform.execute_action("query_state", account, {})

    assert result["ok"] is True
    assert result["data"]["clerk_jwt"] == "jwt_new"
    assert result["data"]["credits_balance"] == 12
