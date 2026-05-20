from __future__ import annotations

from sqlmodel import Session

from core.account_graph import load_account_graphs
from core.base_platform import Account, RegisterConfig
from core.db import AccountModel, engine
from core.platform_accounts import build_platform_account
from domain.actions import ActionExecutionCommand
from infrastructure.platform_runtime import PlatformRuntime, STATEFUL_ACTION_IDS
from platforms.freebeat.core import FreebeatClient, _extract_login_payload
from platforms.freebeat.plugin import FreebeatPlatform
from platforms.freebeat.protocol_mailbox import FreebeatProtocolMailboxWorker


def test_freebeat_next_action_login_parser_extracts_token():
    payload = (
        '0:["$","$L1",null,{}]\n'
        '1:{"code":0,"msg":"","data":{"token":"tok_123","accessToken":"tok_123",'
        '"deviceToken":"dev_123","userId":"user_123","newUser":true,"expireTime":1781635058486}}\n'
    )

    parsed = _extract_login_payload(payload)

    assert parsed["code"] == 0
    assert parsed["data"]["token"] == "tok_123"
    assert parsed["data"]["deviceToken"] == "dev_123"


def test_freebeat_authenticated_api_sends_current_frontend_token_headers():
    calls: list[dict] = []

    class Response:
        status_code = 200
        text = '{"code":0,"data":{"totalCredits":100}}'

        def json(self):
            return {"code": 0, "data": {"totalCredits": 100}}

    client = FreebeatClient(log_fn=lambda message: None, cookie_header="authToken=tok_123; fb_session=sess_123")

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return Response()

    client.s.request = fake_request

    result = client.find_credits("tok_123")

    headers = calls[0]["headers"]
    assert result["data"]["totalCredits"] == 100
    assert headers["fb-language"] == "en"
    assert headers["x-platform-type"] == "web"
    assert headers["sec-ch-ua-mobile"] == "?0"
    assert headers["Authorization"] == "tok_123"
    assert headers["token"] == "tok_123"
    assert headers["udt"] == "tok_123"
    assert headers["cookie"] == "authToken=tok_123; fb_session=sess_123"


def test_freebeat_api_retries_once_after_vercel_403():
    calls: list[dict] = []

    class Response403:
        status_code = 403
        text = '{"error":{"code":"403","message":"Forbidden"}}'

        def json(self):
            return {"error": {"code": "403", "message": "Forbidden"}}

    class Response200:
        status_code = 200
        text = '{"code":0,"data":true}'

        def json(self):
            return {"code": 0, "data": True}

    client = FreebeatClient(log_fn=lambda message: None)

    def fake_request(method, url, **kwargs):
        calls.append({"kind": "request", "method": method, "url": url, **kwargs})
        return Response403() if len([item for item in calls if item["kind"] == "request"]) == 1 else Response200()

    def fake_get(url, **kwargs):
        calls.append({"kind": "warmup", "url": url, **kwargs})
        return Response200()

    client.s.request = fake_request
    client.s.get = fake_get

    result = client.send_email_verify_code("user@example.com")

    assert result["data"] is True
    assert [item["kind"] for item in calls] == ["request", "warmup", "request"]
    assert calls[0]["headers"]["x-platform-type"] == "web"
    assert "x-platform-type" not in {key.lower(): value for key, value in calls[1]["headers"].items()}
    assert "origin" not in {key.lower(): value for key, value in calls[0]["headers"].items()}


def test_freebeat_send_code_already_sent_response_continues():
    class Response409:
        status_code = 200
        text = '{"code":409,"msg":"A login code has already been sent to this email. Please try again in 1 minute."}'

        def json(self):
            return {
                "code": 409,
                "msg": "A login code has already been sent to this email. Please try again in 1 minute.",
            }

    client = FreebeatClient(log_fn=lambda message: None)
    client.s.request = lambda *args, **kwargs: Response409()

    result = client.send_email_verify_code("user@example.com")

    assert result["code"] == 409


def test_freebeat_protocol_mailbox_worker_claims_rewards(monkeypatch):
    calls: list[tuple[str, object]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def send_email_verify_code(self, email, *, verify_source):
            calls.append(("send", email))
            return {"code": 0, "data": True}

        def verify_email_code(self, email, code, *, next_action=None, next_router_state_tree=None):
            calls.append(("login", code))
            return {
                "code": 0,
                "data": {
                    "token": "tok_worker",
                    "accessToken": "tok_worker",
                    "deviceToken": "dev_worker",
                    "userId": "user_worker",
                    "newUser": True,
                    "expireTime": 1781635058486,
                },
            }

        def fetch_account_state(self, token):
            calls.append(("state", token))
            return {
                "token": token,
                "credits": {"free": "500", "boost": "300", "event": "200", "membership": "0", "totalCredits": 1000},
                "signin_status": {"signedToday": True, "canSignIn": False, "serverUtcDate": "2026-05-18"},
                "last_keepalive_at": "2026-05-18T00:00:00Z",
            }

        def claim_questionnaire(self, token):
            calls.append(("questionnaire", token))
            return {"status": "claimed", "credits_granted": 300}

        def daily_sign_in(self, token):
            calls.append(("signin", token))
            return {"status": "signed", "reward_amount": 200}

    monkeypatch.setattr("platforms.freebeat.protocol_mailbox.FreebeatClient", FakeClient)

    worker = FreebeatProtocolMailboxWorker(log_fn=lambda message: None)
    result = worker.run(email="user@example.com", otp_callback=lambda: "123456")

    assert result["success"] is True
    assert result["token"] == "tok_worker"
    assert result["device_token"] == "dev_worker"
    assert result["account_overview"]["total_credits"] == 1000
    assert ("questionnaire", "tok_worker") in calls
    assert ("signin", "tok_worker") in calls


def test_freebeat_protocol_mailbox_worker_saves_token_when_state_refresh_times_out(monkeypatch):
    calls: list[tuple[str, object]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def send_email_verify_code(self, email, *, verify_source):
            calls.append(("send", email))
            return {"code": 0, "data": True}

        def verify_email_code(self, email, code, *, next_action=None, next_router_state_tree=None):
            calls.append(("login", code))
            return {
                "code": 0,
                "data": {
                    "token": "tok_partial",
                    "accessToken": "tok_partial",
                    "deviceToken": "dev_partial",
                    "userId": "user_partial",
                    "newUser": True,
                    "expireTime": 1781635058486,
                },
            }

        def fetch_account_state(self, token):
            calls.append(("state", token))
            raise TimeoutError("credits timeout")

        def claim_questionnaire(self, token):
            calls.append(("questionnaire", token))
            raise AssertionError("questionnaire should be skipped after state timeout")

        def daily_sign_in(self, token):
            calls.append(("signin", token))
            raise AssertionError("daily sign-in should be skipped after state timeout")

        def auth_state(self):
            return {"cookies": "authToken=tok_partial", "cookie_header": "authToken=tok_partial"}

    monkeypatch.setattr("platforms.freebeat.protocol_mailbox.FreebeatClient", FakeClient)

    logs: list[str] = []
    worker = FreebeatProtocolMailboxWorker(log_fn=logs.append)
    result = worker.run(email="user@example.com", otp_callback=lambda: "123456")

    assert result["success"] is True
    assert result["token"] == "tok_partial"
    assert result["device_token"] == "dev_partial"
    assert result["cookies"] == "authToken=tok_partial"
    assert result["account_overview"]["account_state_partial"] is True
    assert "credits timeout" in result["account_overview"]["account_state_error"]
    assert ("questionnaire", "tok_partial") not in calls
    assert ("signin", "tok_partial") not in calls
    assert any("先保存账号" in message for message in logs)


def test_freebeat_refresh_session_action_persists_token_and_overview(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def verify_email_code(self, email, code, *, next_action=None, next_router_state_tree=None):
            assert email == "user@example.com"
            assert code == "654321"
            return {
                "code": 0,
                "data": {
                    "token": "tok_new",
                    "accessToken": "tok_new",
                    "deviceToken": "dev_new",
                    "userId": "user_new",
                    "expireTime": 1781635058486,
                },
            }

        def fetch_account_state(self, token):
            assert token == "tok_new"
            return {
                "token": token,
                "credits": {"free": "500", "boost": "300", "event": "100", "membership": "0", "totalCredits": 900},
                "signin_status": {"signedToday": True, "canSignIn": False, "serverUtcDate": "2026-05-18"},
                "last_keepalive_at": "2026-05-18T00:00:00Z",
            }

    import platforms.freebeat.plugin as freebeat_plugin

    monkeypatch.setattr(freebeat_plugin, "FreebeatClient", FakeClient)

    with Session(engine) as session:
        model = AccountModel(platform="freebeat", email="user@example.com", password="")
        session.add(model)
        session.commit()
        session.refresh(model)
        account_id = int(model.id or 0)

    result = PlatformRuntime().execute_action(
        ActionExecutionCommand(
            platform="freebeat",
            account_id=account_id,
            action_id="refresh_session",
            params={"code": "654321"},
        )
    )

    assert result.ok is True
    assert result.data["access_token"] == "tok_new"
    assert result.data["session_refreshed"] is True

    with Session(engine) as session:
        graph = load_account_graphs(session, [account_id])[account_id]
        credentials = {item["key"]: item["value"] for item in graph["credentials"]}
        account = build_platform_account(session, session.get(AccountModel, account_id))

    assert credentials["access_token"] == "tok_new"
    assert credentials["device_token"] == "dev_new"
    assert graph["overview"]["total_credits"] == 900
    assert graph["overview"]["session_refreshed"] is True
    assert account.token == "tok_new"


def test_freebeat_relogin_email_code_refreshes_and_syncs(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def verify_email_code(self, email, code, *, next_action=None, next_router_state_tree=None):
            assert email == "user@example.com"
            assert code == "112233"
            return {
                "code": 0,
                "data": {
                    "token": "tok_relogin",
                    "accessToken": "tok_relogin",
                    "deviceToken": "dev_relogin",
                    "userId": "user_relogin",
                    "expireTime": 1781635058486,
                },
            }

        def fetch_account_state(self, token):
            assert token == "tok_relogin"
            return {
                "token": token,
                "credits": {"free": "800", "boost": "200", "event": "100", "membership": "0", "totalCredits": 1100},
                "signin_status": {"signedToday": False, "canSignIn": True, "serverUtcDate": "2026-05-20"},
                "last_keepalive_at": "2026-05-20T00:00:00Z",
            }

        def auth_state(self):
            return {
                "cookies": "authToken=tok_relogin; fb_session=sess_relogin",
                "cookie_header": "authToken=tok_relogin; fb_session=sess_relogin",
            }

    sync_calls: list[tuple[bool, bool, str]] = []

    def fake_sync(account, *, log_fn=None, heartbeat=False, balance=False, **kwargs):
        sync_calls.append((heartbeat, balance, account.token))
        return {"ok": True, "account": {"id": 11}}

    import platforms.freebeat.plugin as freebeat_plugin

    monkeypatch.setattr(freebeat_plugin, "FreebeatClient", FakeClient)
    monkeypatch.setattr(freebeat_plugin, "sync_account_to_freebeat2api", fake_sync)

    with Session(engine) as session:
        model = AccountModel(platform="freebeat", email="user@example.com", password="")
        session.add(model)
        session.commit()
        session.refresh(model)
        account_id = int(model.id or 0)

    result = PlatformRuntime().execute_action(
        ActionExecutionCommand(
            platform="freebeat",
            account_id=account_id,
            action_id="relogin_email_code",
            params={"code": "112233"},
        )
    )

    assert result.ok is True
    assert result.data["access_token"] == "tok_relogin"
    assert result.data["cookies"] == "***"
    assert result.data["freebeat2api_synced"] is True
    assert sync_calls == [(True, True, "tok_relogin")]

    with Session(engine) as session:
        graph = load_account_graphs(session, [account_id])[account_id]
        credentials = {item["key"]: item["value"] for item in graph["credentials"]}

    assert credentials["access_token"] == "tok_relogin"
    assert credentials["device_token"] == "dev_relogin"
    assert credentials["cookies"] == "authToken=tok_relogin; fb_session=sess_relogin"
    assert graph["overview"]["total_credits"] == 1100
    assert graph["overview"]["freebeat2api_synced"] is True


def test_freebeat_relogin_saves_new_token_when_state_refresh_times_out(monkeypatch):
    sync_calls: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def verify_email_code(self, email, code, *, next_action=None, next_router_state_tree=None):
            return {
                "code": 0,
                "data": {
                    "token": "tok_partial",
                    "accessToken": "tok_partial",
                    "deviceToken": "dev_partial",
                    "userId": "user_partial",
                    "expireTime": 1781635058486,
                },
            }

        def fetch_account_state(self, token):
            raise TimeoutError("credits timeout")

        def auth_state(self):
            return {"cookies": "authToken=tok_partial", "cookie_header": "authToken=tok_partial"}

    def fake_sync(account, *, log_fn=None, heartbeat=False, balance=False, **kwargs):
        sync_calls.append(account.token)
        return {"ok": True}

    monkeypatch.setattr("platforms.freebeat.plugin.FreebeatClient", FakeClient)
    monkeypatch.setattr("platforms.freebeat.plugin.sync_account_to_freebeat2api", fake_sync)

    platform = FreebeatPlatform(RegisterConfig(executor_type="protocol"))
    account = Account(platform="freebeat", email="user@example.com", password="", token="tok_old")

    result = platform.execute_action("relogin_email_code", account, {"code": "112233"})

    assert result["ok"] is True
    assert result["data"]["access_token"] == "tok_partial"
    assert result["data"]["device_token"] == "dev_partial"
    assert result["data"]["account_state_partial"] is True
    assert "credits timeout" in result["data"]["account_state_error"]
    assert sync_calls == ["tok_partial"]


def test_freebeat_relogin_uses_saved_account_proxy(monkeypatch):
    proxies: list[str | None] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            proxies.append(kwargs.get("proxy"))

        def verify_email_code(self, email, code, *, next_action=None, next_router_state_tree=None):
            return {
                "code": 0,
                "data": {
                    "token": "tok_proxy",
                    "accessToken": "tok_proxy",
                    "deviceToken": "dev_proxy",
                    "userId": "user_proxy",
                    "expireTime": 1781635058486,
                },
            }

        def fetch_account_state(self, token):
            return {
                "token": token,
                "credits": {"free": "500", "boost": "0", "event": "0", "membership": "0", "totalCredits": 500},
                "signin_status": {"signedToday": False, "canSignIn": True},
                "last_keepalive_at": "2026-05-20T00:00:00Z",
            }

        def auth_state(self):
            return {"cookies": "authToken=tok_proxy", "cookie_header": "authToken=tok_proxy"}

    monkeypatch.setattr("platforms.freebeat.plugin.FreebeatClient", FakeClient)
    monkeypatch.setattr("platforms.freebeat.plugin.sync_account_to_freebeat2api", lambda *args, **kwargs: {"ok": True})

    platform = FreebeatPlatform(RegisterConfig(executor_type="protocol"))
    account = Account(
        platform="freebeat",
        email="user@example.com",
        password="",
        user_id="user_old",
        token="tok_old",
        extra={
            "account_overview": {
                "legacy_extra": {
                    "proxy_url": "http://proxy.example:8080",
                }
            }
        },
    )

    result = platform.execute_action("relogin_email_code", account, {"code": "112233"})

    assert result["ok"] is True
    assert proxies == ["http://proxy.example:8080"]


def test_freebeat_relogin_proxy_param_overrides_saved_proxy():
    platform = FreebeatPlatform(RegisterConfig(executor_type="protocol"))
    account = Account(
        platform="freebeat",
        email="user@example.com",
        password="",
        extra={
            "account_overview": {
                "legacy_extra": {
                    "proxy_url": "http://saved-proxy.example:8080",
                }
            }
        },
    )

    assert platform._proxy_for_account(account, {"proxy": "http://override-proxy.example:8080"}) == "http://override-proxy.example:8080"


def test_freebeat_relogin_email_code_can_send_and_read_otp(monkeypatch):
    events: list[tuple[str, object]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def send_email_verify_code(self, email, *, verify_source):
            events.append(("send", email))
            return {"code": 0}

        def verify_email_code(self, email, code, *, next_action=None, next_router_state_tree=None):
            events.append(("login", email, code))
            return {
                "code": 0,
                "data": {
                    "token": "tok_auto",
                    "accessToken": "tok_auto",
                    "deviceToken": "dev_auto",
                    "userId": "user_auto",
                    "expireTime": 1781635058486,
                },
            }

        def fetch_account_state(self, token):
            events.append(("state", token))
            return {
                "token": token,
                "credits": {"free": "700", "boost": "200", "event": "100", "membership": "0", "totalCredits": 1000},
                "signin_status": {"signedToday": False, "canSignIn": True},
                "last_keepalive_at": "2026-05-20T00:00:00Z",
            }

        def auth_state(self):
            return {"cookies": "authToken=tok_auto; fb_session=sess_auto", "cookie_header": "authToken=tok_auto; fb_session=sess_auto"}

    class FakeMailbox:
        def get_current_ids(self, account):
            events.append(("baseline", account.email, account.account_id, account.extra.get("mailbox_provider_key")))
            return {"old"}

        def wait_for_code(self, account, **kwargs):
            events.append(("wait", account.email, account.extra.get("mailbox_provider_key"), kwargs.get("before_ids")))
            return "778899"

    monkeypatch.setattr("platforms.freebeat.plugin.FreebeatClient", FakeClient)
    def fake_create_mailbox(provider, extra, proxy=None):
        events.append(("mailbox_proxy", proxy))
        return FakeMailbox()

    monkeypatch.setattr("platforms.freebeat.plugin.create_mailbox", fake_create_mailbox)
    monkeypatch.setattr("platforms.freebeat.plugin.sync_account_to_freebeat2api", lambda *args, **kwargs: {"ok": True, "account": {"id": 7}})

    platform = FreebeatPlatform(RegisterConfig(executor_type="protocol"))
    account = Account(
        platform="freebeat",
        email="user@example.com",
        password="",
        user_id="user_old",
        token="tok_old",
        extra={
            "provider_resources": [
                {
                    "provider_type": "mailbox",
                    "provider_name": "cloud_mail",
                    "resource_type": "mailbox",
                    "resource_identifier": "user@example.com",
                    "handle": "user@example.com",
                    "metadata": {"account_id": "user@example.com", "email": "user@example.com"},
                }
            ],
            "account_overview": {"legacy_extra": {"proxy_url": "http://proxy.example:8080"}},
        },
    )

    result = platform.execute_action("relogin_email_code", account, {})

    assert result["ok"] is True
    assert result["data"]["access_token"] == "tok_auto"
    assert ("mailbox_proxy", "http://proxy.example:8080") in events
    assert ("baseline", "user@example.com", "user@example.com", "cloud_mail") in events
    assert ("send", "user@example.com") in events
    assert ("wait", "user@example.com", "cloud_mail", {"old"}) in events
    assert ("login", "user@example.com", "778899") in events


def test_freebeat_platform_declares_protocol_mailbox_capability():
    platform = FreebeatPlatform(RegisterConfig(executor_type="protocol"))
    actions = {item["id"] for item in platform.get_platform_actions()}

    assert platform.supported_executors == ["protocol"]
    assert platform.supported_identity_modes == ["mailbox"]
    assert {
        "daily_sign_in",
        "claim_questionnaire",
        "relogin_email_code",
        "keepalive_sync",
        "stop_daily_sign_in",
        "resume_daily_sign_in",
    } <= actions
    assert "send_login_code" not in actions
    assert "refresh_session" not in actions
    assert "relogin_email_code" in STATEFUL_ACTION_IDS


def test_freebeat_registration_result_maps_access_token_as_primary():
    platform = FreebeatPlatform(RegisterConfig(executor_type="protocol"))

    result = platform._map_freebeat_result(
        {
            "email": "user@example.com",
            "user_id": "user_123",
            "token": "tok_primary",
            "device_token": "dev_primary",
            "account_overview": {"valid": True, "plan_state": "free"},
        }
    )

    account = Account(
        platform="freebeat",
        email=result.email,
        password=result.password,
        user_id=result.user_id,
        token=result.token,
        extra=result.extra,
    )

    assert account.token == "tok_primary"
    assert account.extra["access_token"] == "tok_primary"
    assert account.extra["device_token"] == "dev_primary"


def test_freebeat_registration_result_maps_cookies():
    platform = FreebeatPlatform(RegisterConfig(executor_type="protocol"))

    result = platform._map_freebeat_result(
        {
            "email": "user@example.com",
            "user_id": "user_123",
            "token": "tok_primary",
            "cookies": "authToken=tok_primary; fb_session=sess_primary",
            "account_overview": {"valid": True, "plan_state": "free"},
        }
    )

    assert result.extra["cookies"] == "authToken=tok_primary; fb_session=sess_primary"
    assert result.extra["cookie_header"] == "authToken=tok_primary; fb_session=sess_primary"


def test_freebeat_keepalive_sync_pushes_to_freebeat2api(monkeypatch):
    calls: list[tuple[bool, bool, bool, str]] = []

    def fake_sync(account, *, log_fn=None, heartbeat=False, balance=False, sign_in=False, **kwargs):
        calls.append((heartbeat, balance, sign_in, account.token))
        return {"ok": True, "account": {"id": 7}}

    monkeypatch.setattr("platforms.freebeat.plugin.sync_account_to_freebeat2api", fake_sync)
    monkeypatch.setattr(
        FreebeatPlatform,
        "_load_state",
        lambda self, account, **kwargs: {
            "summary": {
                "valid": True,
                "email": account.email,
                "user_id": account.user_id,
                "access_token": account.token,
                "total_credits": 1000,
            }
        },
    )

    platform = FreebeatPlatform(RegisterConfig(executor_type="protocol"))
    account = Account(
        platform="freebeat",
        email="user@example.com",
        password="",
        user_id="user_123",
        token="tok_123",
        extra={"access_token": "tok_123"},
    )

    result = platform.execute_action("keepalive_sync", account, {})

    assert result["ok"] is True
    assert result["data"]["freebeat2api_synced"] is True
    assert calls == [(True, True, False, "tok_123")]


def test_freebeat_daily_sign_in_syncs_to_freebeat2api(monkeypatch):
    calls: list[tuple[bool, bool, bool, str]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def daily_sign_in(self, token):
            assert token == "tok_123"
            return {"status": "signed", "reward_amount": 200}

    def fake_sync(account, *, log_fn=None, heartbeat=False, balance=False, sign_in=False, **kwargs):
        calls.append((heartbeat, balance, sign_in, account.token))
        return {"ok": True, "account": {"id": 7}}

    monkeypatch.setattr("platforms.freebeat.plugin.FreebeatClient", FakeClient)
    monkeypatch.setattr("platforms.freebeat.plugin.sync_account_to_freebeat2api", fake_sync)
    monkeypatch.setattr(
        FreebeatPlatform,
        "_load_state",
        lambda self, account, **kwargs: {
            "token": account.token,
            "access_token": account.token,
            "summary": {
                "valid": True,
                "email": account.email,
                "user_id": account.user_id,
                "access_token": account.token,
                "total_credits": 1200,
            },
        },
    )

    platform = FreebeatPlatform(RegisterConfig(executor_type="protocol"))
    account = Account(
        platform="freebeat",
        email="user@example.com",
        password="",
        user_id="user_123",
        token="tok_123",
        extra={"access_token": "tok_123"},
    )

    result = platform.execute_action("daily_sign_in", account, {})

    assert result["ok"] is True
    assert result["data"]["daily_sign_in_status"] == "signed"
    assert result["data"]["freebeat2api_synced"] is True
    assert calls == [(False, True, True, "tok_123")]


def test_freebeat_registration_auto_sync_pushes_latest_account_to_freebeat2api(monkeypatch):
    import application.tasks as tasks
    import core.freebeat2api_sync as sync_module

    calls: list[tuple[bool, bool, bool, str]] = []
    logs: list[tuple[str, str]] = []

    class Logger:
        def log(self, message, level="info"):
            logs.append((message, level))

    def fake_sync(account, *, log_fn=None, heartbeat=False, balance=False, sign_in=False, **kwargs):
        calls.append((heartbeat, balance, sign_in, account.token))
        return {"ok": True, "account": {"id": 9}}

    monkeypatch.setattr(sync_module, "sync_account_to_freebeat2api", fake_sync)
    monkeypatch.setattr(sync_module, "is_freebeat2api_configured", lambda: True)

    account = Account(
        platform="freebeat",
        email="new@example.com",
        password="",
        user_id="user_new",
        token="tok_new",
        extra={
            "access_token": "tok_new",
            "last_daily_sign_in_status": "signed",
            "daily_sign_in": {"status": "signed"},
        },
    )

    tasks._auto_sync_freebeat2api(Logger(), account)

    assert calls == [(True, True, True, "tok_new")]
    assert any("Freebeat account synced" in message for message, _ in logs)


def test_freebeat_stop_and_resume_daily_signin_persist_account_marker():
    with Session(engine) as session:
        model = AccountModel(platform="freebeat", email="manual@example.com", password="")
        session.add(model)
        session.commit()
        session.refresh(model)
        account_id = int(model.id or 0)

    runtime = PlatformRuntime()

    stop_result = runtime.execute_action(
        ActionExecutionCommand(
            platform="freebeat",
            account_id=account_id,
            action_id="stop_daily_sign_in",
            params={"reason": "manual"},
        )
    )

    assert stop_result.ok is True
    with Session(engine) as session:
        graph = load_account_graphs(session, [account_id])[account_id]
    assert graph["overview"]["freebeat_daily_sign_in_disabled"] is True
    assert graph["overview"]["freebeat_daily_sign_in_state"] == "disabled"

    resume_result = runtime.execute_action(
        ActionExecutionCommand(
            platform="freebeat",
            account_id=account_id,
            action_id="resume_daily_sign_in",
            params={},
        )
    )

    assert resume_result.ok is True
    with Session(engine) as session:
        graph = load_account_graphs(session, [account_id])[account_id]
    assert graph["overview"]["freebeat_daily_sign_in_disabled"] is False
    assert graph["overview"]["freebeat_daily_sign_in_state"] == "enabled"
