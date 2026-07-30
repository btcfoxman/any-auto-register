from __future__ import annotations

from core.base_platform import Account, RegisterConfig
from core.db import AccountModel
from core.platform_accounts import build_platform_extra
from core.registry import get, load_all
from infrastructure.platform_runtime import (
    PERSISTED_ACTION_DATA_KEYS,
    STATEFUL_ACTION_IDS,
    _build_account_overview,
)
from infrastructure.config_repository import ConfigRepository
from platforms.higg.browser_context import (
    HiggBitBrowserSession,
    HiggBrowserSession,
    HiggChromeBrowserSession,
    _ProfileLeases,
    parse_profile_ids,
    parse_proxy_ports,
)
from platforms.higg.core import (
    HiggAuthState,
    HiggClient,
    HiggRiskBlocked,
    cookie_header_from_any,
)
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
        "higg_browser_enabled",
        "higg_browser_required",
        "higg_browser_mode",
        "higg_browser_fallback_mode",
        "higg_chrome_proxy_ports",
        "higg_bitbrowser_api_url",
        "higg_bitbrowser_profile_ids",
        "higg_bitbrowser_close_after_use",
        "higg_bitbrowser_clear_site_data",
    } <= ConfigRepository().get_allowed_keys()


def test_higg_downstream_concurrency_config_is_always_one(monkeypatch):
    saved = {}
    monkeypatch.setattr(
        "infrastructure.config_repository.config_store.get_all",
        lambda: {"higg2api_max_concurrency": "5"},
    )
    monkeypatch.setattr(
        "infrastructure.config_repository.config_store.set_many",
        lambda values: saved.update(values),
    )
    repository = ConfigRepository()

    assert repository.get_flat()["higg2api_max_concurrency"] == "1"
    repository.update_flat({"higg2api_max_concurrency": "9"})
    assert saved["higg2api_max_concurrency"] == "1"


def test_higg_runtime_restores_persisted_legacy_extra():
    model = AccountModel(
        platform="higg",
        email="higg@example.com",
        password="secret",
    )
    graph = {
        "overview": {
            "lifecycle_status": "registered",
            "legacy_extra": {
                "proxy_url": "socks5://127.0.0.1:20001",
                "free_generations": 3,
                "browser_mode": "native_chrome",
            },
        },
        "credentials": [
            {
                "scope": "platform",
                "key": "browser_mode",
                "value": "credential-value",
            }
        ],
    }

    extra = build_platform_extra(model, graph)

    assert extra["proxy_url"] == "socks5://127.0.0.1:20001"
    assert extra["free_generations"] == 3
    assert extra["browser_mode"] == "credential-value"
    assert extra["account_overview"] == graph["overview"]


def test_higg_profile_ids_accept_commas_newlines_and_remove_duplicates():
    assert parse_profile_ids("profile-a, profile-b\nprofile-a") == [
        "profile-a",
        "profile-b",
    ]


def test_higg_browser_factory_selects_native_chrome_before_bitbrowser():
    chrome = HiggBrowserSession(
        browser_mode="native_chrome",
        chrome_proxy_ports="20001,20002",
    )
    bitbrowser = HiggBrowserSession(browser_mode="bitbrowser")

    assert isinstance(chrome, HiggChromeBrowserSession)
    assert isinstance(bitbrowser, HiggBitBrowserSession)
    assert parse_proxy_ports("20001, 20002\n20001") == [20001, 20002]


def test_higg_profile_leases_balance_concurrent_workers_without_duplicates():
    leases = _ProfileLeases()
    profiles = [
        {
            "id": f"profile-{index}",
            "proxyType": "socks5",
            "host": "127.0.0.1",
            "port": 20001 + index,
        }
        for index in range(4)
    ]

    selected = [
        leases.acquire(profiles, timeout=0.1)["id"]
        for _ in range(len(profiles))
    ]

    assert len(set(selected)) == 4
    for profile_id in selected:
        leases.release(profile_id)


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

    def solve_turnstile(url, site_key, proxy):
        raise AssertionError("browser Turnstile token should be preferred")

    class FakeBrowser:
        def __init__(self, **kwargs):
            calls.append(("browser_init", kwargs["proxy"]))
            self.proxy_url = "socks5://127.0.0.1:20014"

        def start(self):
            calls.append("browser_start")

        def create_signup(self, *, email, password, captcha_token=""):
            calls.append(("browser_signup", email, password, captcha_token))
            return {"response": {"id": "signup_123"}}

        def complete_email_verification(self, code):
            calls.append(("browser_verify", code))
            return {
                "cookies": [
                    {"name": "__client", "value": "client_123", "domain": ".higgsfield.ai"},
                    {"name": "__session", "value": "jwt_browser", "domain": ".higgsfield.ai"},
                    {"name": "datadome", "value": "dd_browser", "domain": ".higgsfield.ai"},
                ],
                "datadome": "dd_browser",
                "clerk_jwt": "jwt_browser",
                "user_agent": "browser-agent",
                "sec_ch_ua": '"Chromium";v="140"',
                "sec_ch_ua_platform": '"Windows"',
                "browser_profile_id": "profile_123",
                "browser_webdriver": False,
                "proxy_url": self.proxy_url,
                "risk_probe": {"blocked": False},
            }

        def close(self):
            calls.append("browser_close")

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs["proxy"]))
            self.token = kwargs["token"]
            self.user_agent = kwargs["user_agent"]
            self.sec_ch_ua = kwargs["sec_ch_ua"]
            self.sec_ch_ua_platform = kwargs["sec_ch_ua_platform"]

        def create_signup(self, email, password, captcha):
            raise AssertionError("direct Clerk registration must not run")

        def prepare_email_verification(self, signup_id):
            raise AssertionError("direct Clerk verification must not run")

        def attempt_email_verification(self, signup_id, code):
            raise AssertionError("direct Clerk verification must not run")

        def touch_session(self):
            raise AssertionError("direct Clerk session touch must not run")

        def auth_state(self, **kwargs):
            return HiggAuthState(
                email=kwargs["email"],
                user_id=str(kwargs.get("user_id") or "user_123"),
                session_id="sess_123",
                token=self.token,
                workspace_id="ws_123",
                cookie_header="__client=client_123; datadome=dd_browser",
                datadome="dd_browser",
            )

        def fetch_account_state(self):
            return {
                "generation_ready": True,
                "credits_balance": 60,
                "free_generations": 1,
                "chips": ["Credits 60", "Free gens 1"],
            }

        def ensure_upload_agreements(self):
            calls.append("confirm_upload_agreement")
            return {"character_sheets_consent": True}

        def ensure_seedance_onboarding(self):
            calls.append("initialize_seedance")
            return {"project": {"access": "owner"}}

    monkeypatch.setattr("platforms.higg.protocol_mailbox.HiggClient", FakeClient)
    worker = HiggProtocolMailboxWorker(
        proxy="http://proxy.example:8080",
        turnstile_solver=solve_turnstile,
        browser_session_factory=FakeBrowser,
    )

    result = worker.run(
        email="higg@example.com",
        password="Password123!",
        otp_callback=lambda: "123456",
    )

    assert ("browser_init", "http://proxy.example:8080") in calls
    assert (
        "browser_signup",
        "higg@example.com",
        "Password123!",
        "",
    ) in calls
    assert ("browser_verify", "123456") in calls
    assert ("init", "socks5://127.0.0.1:20014") in calls
    assert result["session_id"] == "sess_123"
    assert result["clerk_jwt"] == "jwt_browser"
    assert result["datadome"] == "dd_browser"
    assert result["proxy_url"] == "socks5://127.0.0.1:20014"
    assert result["browser_profile_id"] == "profile_123"
    assert result["browser_webdriver"] is False
    assert result["workspace_id"] == "ws_123"
    assert result["credits_balance"] == 60
    assert result["account_overview"]["generation_ready"] is True
    assert "initialize_seedance" in calls
    assert "confirm_upload_agreement" in calls
    assert calls[-1] == "browser_close"


def test_higg_client_applies_browser_fingerprint_and_datadome():
    client = HiggClient(
        token="header.payload.signature",
        session_id="sess_123",
        cookies="__client=old",
    )

    client.apply_browser_context(
        {
            "cookies": [
                {"name": "__client", "value": "new", "domain": ".higgsfield.ai"},
                {"name": "datadome", "value": "dd_new", "domain": ".higgsfield.ai"},
            ],
            "datadome": "dd_new",
            "user_agent": "Browser UA",
            "sec_ch_ua": '"Chromium";v="140"',
            "sec_ch_ua_platform": '"Windows"',
        }
    )

    assert client.datadome == "dd_new"
    assert client.user_agent == "Browser UA"
    assert client.session.headers["User-Agent"] == "Browser UA"
    assert "datadome=dd_new" in client.cookie_header()


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


def test_higg_query_state_rebuilds_browser_context_after_datadome_block(monkeypatch):
    events: list[object] = []

    class FakeClient:
        proxy = "http://proxy.example:8080"
        token = "jwt_old"

        def __init__(self):
            self.calls = 0

        def fetch_account_state(self):
            self.calls += 1
            if self.calls == 1:
                raise HiggRiskBlocked("blocked")
            return {
                "valid": True,
                "generation_ready": True,
                "cookies": "__client=new; datadome=dd_new",
                "datadome": "dd_new",
                "credits_balance": 18,
            }

        def browser_cookies(self):
            return [{"name": "__client", "value": "old", "domain": "clerk.higgsfield.ai"}]

        def apply_browser_context(self, context):
            events.append(("apply", context["datadome"]))

    class FakeBrowser:
        def __init__(self, **kwargs):
            events.append(("browser", kwargs["proxy"]))

        def start(self):
            events.append("start")

        def bootstrap_authenticated(self, *, cookies, token):
            events.append(("bootstrap", token))
            return {
                "cookies": [{"name": "datadome", "value": "dd_new", "domain": ".higgsfield.ai"}],
                "datadome": "dd_new",
                "browser_profile_id": "profile_refresh",
                "browser_webdriver": False,
                "proxy_url": "socks5://127.0.0.1:20014",
                "risk_probe": {"blocked": False},
            }

        def close(self):
            events.append("close")

    platform = HiggPlatform(config=RegisterConfig(executor_type="protocol"))
    client = FakeClient()
    monkeypatch.setattr(platform, "_client", lambda _account: client)
    monkeypatch.setattr("platforms.higg.browser_context.HiggBrowserSession", FakeBrowser)
    account = Account(
        platform="higg",
        email="higg@example.com",
        password="",
        token="jwt_old",
        extra={"higg_browser_enabled": True, "proxy_url": client.proxy},
    )

    state = platform._load_state(account)

    assert state["risk_context_refreshed"] is True
    assert state["credits_balance"] == 18
    assert state["browser_profile_id"] == "profile_refresh"
    assert state["proxy_url"] == "socks5://127.0.0.1:20014"
    assert ("browser", client.proxy) in events
    assert ("apply", "dd_new") in events
    assert events[-1] == "close"
