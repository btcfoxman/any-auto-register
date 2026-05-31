from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from sqlmodel import Session

from core.account_graph import load_account_graphs
from core.base_platform import Account, RegisterConfig
from core.db import AccountModel, engine
from domain.actions import ActionExecutionCommand
from infrastructure.platform_runtime import PlatformRuntime, STATEFUL_ACTION_IDS
from platforms.quickframe.core import (
    QUICKFRAME_EFFECT_VIDEO_SUBSCRIPTION_PATH,
    QuickFrameClient,
    parse_quickframe_effect_subscription_message,
    quickframe_effect_subscription_messages,
    summarize_quickframe_account_state,
)
from platforms.quickframe.plugin import QuickFramePlatform
from platforms.quickframe.plugin import QUICKFRAME_EMAIL_CODE_PATTERN
from platforms.quickframe.protocol_mailbox import QuickFrameProtocolMailboxWorker


class Response:
    def __init__(self, status_code: int = 200, text: str = "", headers: dict | None = None, json_data=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._json_data = json_data

    def json(self):
        return self._json_data


def test_quickframe_begin_email_challenge_uses_captured_auth0_form():
    calls: list[dict] = []
    client = QuickFrameClient(log_fn=lambda message: None)

    def fake_start_login(email):
        client.login_state = "state-123"
        client.login_identifier_url = "https://login.quickframe.com/u/login/identifier?state=state-123"
        return {"state": "state-123", "identifier_url": client.login_identifier_url}

    def fake_post(url, **kwargs):
        calls.append({"method": "POST", "url": url, **kwargs})
        return Response(
            status_code=302,
            headers={"location": "/u/login/passwordless-email-challenge?state=state-123"},
        )

    def fake_get(url, **kwargs):
        calls.append({"method": "GET", "url": url, **kwargs})
        return Response(
            status_code=200,
            text='<input type="hidden" name="state" value="state-123">',
        )

    client.start_login = fake_start_login
    client.s.post = fake_post
    client.s.get = fake_get

    pending = client.begin_email_challenge("new@example.com")

    form = {key: values[0] for key, values in parse_qs(calls[0]["data"]).items()}
    assert calls[0]["url"] == "https://login.quickframe.com/u/login/identifier?state=state-123"
    assert calls[0]["headers"]["origin"] == "https://login.quickframe.com"
    assert form == {
        "state": "state-123",
        "username": "new@example.com",
        "js-available": "true",
        "webauthn-available": "true",
        "is-brave": "false",
        "webauthn-platform-available": "true",
    }
    assert pending["quickframe_login_state"] == "state-123"
    assert pending["quickframe_challenge_url"].endswith("/u/login/passwordless-email-challenge?state=state-123")


def test_quickframe_begin_email_challenge_uses_identifier_form_action_and_hidden_fields():
    calls: list[dict] = []
    client = QuickFrameClient(log_fn=lambda message: None)
    responses = [
        Response(status_code=302, headers={"location": "https://login.quickframe.com/authorize?state=query-state"}),
        Response(status_code=302, headers={"location": "/u/login/identifier?state=query-state"}),
        Response(
            status_code=200,
            text=(
                '<form method="post" action="/u/login/identifier?state=hidden-state">'
                '<input type="hidden" name="state" value="hidden-state">'
                '<input type="hidden" name="action" value="default">'
                '<input type="text" name="username" value="">'
                "</form>"
            ),
        ),
        Response(
            status_code=200,
            text='<form method="post"><input type="hidden" name="state" value="hidden-state"><input name="code"></form>',
        ),
    ]

    def fake_get(url, **kwargs):
        calls.append({"method": "GET", "url": url, **kwargs})
        return responses.pop(0)

    def fake_post(url, **kwargs):
        calls.append({"method": "POST", "url": url, **kwargs})
        return Response(
            status_code=302,
            headers={"location": "/u/login/passwordless-email-challenge?state=hidden-state"},
        )

    client.s.get = fake_get
    client.s.post = fake_post

    pending = client.begin_email_challenge("new@example.com")

    post_call = next(item for item in calls if item["method"] == "POST")
    form = {key: values[0] for key, values in parse_qs(post_call["data"]).items()}
    assert post_call["url"] == "https://login.quickframe.com/u/login/identifier?state=hidden-state"
    assert form["state"] == "hidden-state"
    assert form["action"] == "default"
    assert form["username"] == "new@example.com"
    assert form["js-available"] == "true"
    assert pending["quickframe_login_state"] == "hidden-state"


def test_quickframe_begin_email_challenge_includes_auth0_submit_button_action():
    calls: list[dict] = []
    client = QuickFrameClient(log_fn=lambda message: None)
    responses = [
        Response(status_code=302, headers={"location": "https://login.quickframe.com/authorize?state=query-state"}),
        Response(status_code=302, headers={"location": "/u/login/identifier?state=query-state"}),
        Response(
            status_code=200,
            text=(
                '<form method="post" action="/u/login/identifier?state=query-state">'
                '<input type="hidden" name="state" value="query-state">'
                '<input type="text" name="username" value="">'
                '<button type="submit" name="action" value="default">Continue</button>'
                "</form>"
            ),
        ),
        Response(
            status_code=200,
            text='<form method="post"><input type="hidden" name="state" value="query-state"><input name="code"></form>',
        ),
    ]

    def fake_get(url, **kwargs):
        calls.append({"method": "GET", "url": url, **kwargs})
        return responses.pop(0)

    def fake_post(url, **kwargs):
        calls.append({"method": "POST", "url": url, **kwargs})
        return Response(
            status_code=302,
            headers={"location": "/u/login/passwordless-email-challenge?state=query-state"},
        )

    client.s.get = fake_get
    client.s.post = fake_post

    client.begin_email_challenge("new@example.com")

    post_call = next(item for item in calls if item["method"] == "POST")
    form = {key: values[0] for key, values in parse_qs(post_call["data"]).items()}
    assert form["action"] == "default"
    assert form["username"] == "new@example.com"


def test_quickframe_begin_email_challenge_accepts_direct_passwordless_challenge_redirect(monkeypatch):
    calls: list[dict] = []
    client = QuickFrameClient(log_fn=lambda message: None)
    monkeypatch.setattr("platforms.quickframe.core._quickframe_anonymous_id", lambda: "anonymous_00000000-0000-4000-8000-000000000001")
    monkeypatch.setattr("platforms.quickframe.core._quickframe_visitor_id", lambda: "visitor1234567890123")
    monkeypatch.setattr("platforms.quickframe.core._quickframe_event_id", lambda: "1780217363226.Xaxk79")
    responses = [
        Response(status_code=302, headers={"location": "https://login.quickframe.com/authorize?state=state-direct"}),
        Response(status_code=302, headers={"location": "/u/login/passwordless-email-challenge?state=state-direct"}),
        Response(status_code=200, text='<input type="hidden" name="state" value="state-direct">'),
    ]

    def fake_get(url, **kwargs):
        calls.append({"method": "GET", "url": url, **kwargs})
        return responses.pop(0)

    def fake_post(url, **kwargs):
        raise AssertionError("direct passwordless challenge should not post identifier form again")

    client.s.get = fake_get
    client.s.post = fake_post

    pending = client.begin_email_challenge("new@example.com")

    auth_login = urlparse(calls[0]["url"])
    auth_query = {key: values[0] for key, values in parse_qs(auth_login.query).items()}
    assert f"{auth_login.scheme}://{auth_login.netloc}{auth_login.path}" == "https://server.cs.quickframe.com/auth/login"
    assert auth_query == {
        "returnUrl": "https://ai.quickframe.com/",
        "login_hint": "new@example.com",
        "previous_anonymous_id": "anonymous_00000000-0000-4000-8000-000000000001",
        "visitorId": "visitor1234567890123",
        "eventId": "1780217363226.Xaxk79",
    }
    assert [item["url"] for item in calls[1:]] == [
        "https://login.quickframe.com/authorize?state=state-direct",
        "https://login.quickframe.com/u/login/passwordless-email-challenge?state=state-direct",
    ]
    assert pending["quickframe_login_state"] == "state-direct"
    assert pending["quickframe_login_identifier_url"] == ""
    assert pending["quickframe_challenge_url"].endswith("/u/login/passwordless-email-challenge?state=state-direct")
    assert any(item["name"] == "dd_anonymous_user_id" for item in pending["quickframe_pending_cookies"])


def test_quickframe_headers_let_cookie_jar_scope_live_auth_cookies():
    client = QuickFrameClient(log_fn=lambda message: None)
    client.s.cookies.set("auth0", "sess", domain="login.quickframe.com", path="/")

    headers = client._headers(include_cookie=True)

    assert "cookie" not in headers
    assert client.cookie_header() == "auth0=sess"


def test_quickframe_headers_keep_raw_cookie_fallback_for_stored_api_session():
    client = QuickFrameClient(log_fn=lambda message: None, cookie_header="cs_session=sess_123")

    headers = client._headers(include_cookie=True)

    assert headers["cookie"] == "cs_session=sess_123"


def test_quickframe_pending_cookie_header_is_seeded_to_login_host():
    client = QuickFrameClient(
        log_fn=lambda message: None,
        cookie_header="auth0=sess; did=device",
        challenge_url="https://login.quickframe.com/u/login/passwordless-email-challenge?state=state-123",
        login_state="state-123",
    )

    records = client.cookie_records()

    assert {item["name"]: item["domain"] for item in records} == {
        "auth0": "login.quickframe.com",
        "did": "login.quickframe.com",
    }
    assert "cookie" not in client._headers(include_cookie=True, manual_cookie=False)


def test_quickframe_follow_login_redirects_matches_captured_callback_headers():
    calls: list[dict] = []
    client = QuickFrameClient(log_fn=lambda message: None)
    responses = [
        Response(
            status_code=302,
            headers={"location": "https://server.cs.quickframe.com/auth/callback?code=code-123&state=state-123"},
        ),
        Response(status_code=302, headers={"location": "https://ai.quickframe.com/"}),
        Response(status_code=200),
    ]

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return responses.pop(0)

    client.s.get = fake_get

    client._follow_login_redirects(
        "https://login.quickframe.com/authorize/resume?state=resume-123",
        referer="https://login.quickframe.com/u/login/passwordless-email-challenge?state=challenge-123",
    )

    assert calls[0]["headers"]["origin"] == "https://login.quickframe.com"
    assert calls[0]["headers"]["referer"].endswith("passwordless-email-challenge?state=challenge-123")
    assert calls[1]["url"].startswith("https://server.cs.quickframe.com/auth/callback")
    assert calls[1]["headers"]["origin"] == "https://login.quickframe.com"
    assert "referer" not in calls[1]["headers"]
    assert calls[2]["url"] == "https://ai.quickframe.com/"
    assert calls[2]["headers"]["origin"] == "https://login.quickframe.com"
    assert "referer" not in calls[2]["headers"]
    assert all("cookie" not in item["headers"] for item in calls)


def test_quickframe_follow_login_redirects_rejects_auth0_logout():
    client = QuickFrameClient(log_fn=lambda message: None)

    def fake_get(url, **kwargs):
        return Response(status_code=302, headers={"location": "https://login.quickframe.com/v2/logout?client_id=client"})

    client.s.get = fake_get

    try:
        client._follow_login_redirects(
            "https://server.cs.quickframe.com/auth/callback?code=code-123&state=state-123",
            referer="https://login.quickframe.com/authorize/resume?state=resume-123",
        )
    except RuntimeError as exc:
        assert "Auth0 logout" in str(exc)
    else:
        raise AssertionError("expected Auth0 logout redirect to fail")


def test_quickframe_summary_maps_captured_session_token_and_billing_shape():
    state = {
        "session_info": {
            "user": {"email": "new@example.com", "primaryEmailAddress": "new@example.com"},
            "session": {"id": "sess_123", "status": "active", "active": True},
        },
        "token_info": {"accessToken": "tok_123", "tokenType": "Bearer", "expiresIn": 86387},
        "access_token": "tok_123",
        "check_session": {
            "email": "new@example.com",
            "workspaceId": 47784,
            "id": 108334,
            "actualUserId": 108334,
            "signupSource": "qfai-direct",
            "hasPremierAccount": False,
        },
        "subscription_status": {"hasActiveSubscription": False, "freeExportsRemaining": 1},
        "usage_limits": {"freeExportsRemaining": 1},
        "last_keepalive_at": "2026-05-31T09:19:20Z",
    }

    summary = summarize_quickframe_account_state(state)

    assert summary["valid"] is True
    assert summary["email"] == "new@example.com"
    assert summary["user_id"] == "108334"
    assert summary["workspace_id"] == "47784"
    assert summary["session_status"] == "active"
    assert summary["plan_state"] == "free"
    assert summary["free_exports_remaining"] == 1
    assert "Workspace 47784" in summary["chips"]


def test_quickframe_fetch_account_state_issues_token_when_session_has_user_without_active_flag(monkeypatch):
    calls: list[str] = []
    client = QuickFrameClient(log_fn=lambda message: None)

    def fake_issue_token():
        calls.append("token")
        client.access_token = "tok_123"
        return {"accessToken": "tok_123", "tokenType": "Bearer", "expiresIn": 86387}

    def fake_trpc_get(procedures, input_data=None, *, token=""):
        calls.append(str(procedures))
        if procedures == "auth.checkSession":
            return [{"result": {"data": {"email": "new@example.com", "id": 108334, "workspaceId": 47784}}}]
        if isinstance(procedures, list) and "billing.getSubscriptionStatus" in procedures:
            return [
                {"result": {"data": []}},
                {"result": {"data": {"hasActiveSubscription": False, "freeExportsRemaining": 1}}},
                {"result": {"data": {"email": "new@example.com", "id": 108334, "workspaceId": 47784}}},
            ]
        return [{"result": {"data": {}}}, {"result": {"data": {}}}]

    monkeypatch.setattr(
        client,
        "get_session",
        lambda: {
            "user": {
                "id": "email|abc",
                "email": "new@example.com",
                "primaryEmailAddress": {"emailAddress": "new@example.com"},
            },
            "session": {"id": "sess_123"},
        },
    )
    monkeypatch.setattr(client, "issue_token", fake_issue_token)
    monkeypatch.setattr(client, "trpc_get", fake_trpc_get)

    state = client.fetch_account_state(force_refresh=True)

    assert calls[0] == "token"
    assert state["access_token"] == "tok_123"
    assert state["summary"]["valid"] is True
    assert state["summary"]["email"] == "new@example.com"
    assert state["summary"]["workspace_id"] == "47784"


def test_quickframe_wss_subscription_helpers_follow_captured_terminal_rules():
    frames = quickframe_effect_subscription_messages("jwt_123", "eg-107384-1780242096729", subscription_id=12)

    assert frames[0] == {"method": "connectionParams", "data": {"token": "jwt_123"}}
    assert frames[1]["params"]["path"] == QUICKFRAME_EFFECT_VIDEO_SUBSCRIPTION_PATH
    assert frames[1]["params"]["input"]["runId"] == "eg-107384-1780242096729"

    started = parse_quickframe_effect_subscription_message('{"id":12,"result":{"type":"started"}}')
    heartbeat = parse_quickframe_effect_subscription_message("PING")
    progress = parse_quickframe_effect_subscription_message(
        '{"id":12,"result":{"type":"data","data":{"type":"progress","data":{"progress":80,"stepName":"Saving asset"}}}}'
    )
    complete = parse_quickframe_effect_subscription_message(
        '{"id":12,"result":{"type":"data","data":{"type":"complete","data":{"runId":"eg-1","assetId":1176212,"videoUrl":"https://res.cloudinary.com/video"}}}}'
    )
    failure = parse_quickframe_effect_subscription_message(
        '{"id":26,"result":{"type":"data","data":{"type":"error","data":{"runId":"eg-2","error":"Generation failed"}}}}'
    )
    stopped = parse_quickframe_effect_subscription_message('{"id":12,"result":{"type":"stopped"}}')

    assert started["terminal"] is False
    assert heartbeat["type"] == "heartbeat"
    assert progress["terminal"] is False
    assert complete["terminal"] is True
    assert complete["ok"] is True
    assert complete["asset_id"] == 1176212
    assert failure["terminal"] is True
    assert failure["ok"] is False
    assert failure["error"] == "Generation failed"
    assert stopped["terminal"] is False


def test_quickframe_protocol_mailbox_worker_maps_registration_result(monkeypatch):
    events: list[tuple[str, object]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def begin_email_challenge(self, email):
            events.append(("begin", email))
            return {"quickframe_login_state": "state-123"}

        def complete_email_challenge(self, code):
            events.append(("complete", code))
            return {
                "session_info": {
                    "user": {"email": "new@example.com"},
                    "session": {"id": "sess_123", "status": "active", "active": True},
                },
                "token_info": {"accessToken": "tok_worker", "tokenType": "Bearer", "expiresIn": 86387},
                "access_token": "tok_worker",
                "cookies": "qf_session=sess_123",
                "cookie_header": "qf_session=sess_123",
                "quickframe_cookies": [{"name": "qf_session", "value": "sess_123"}],
                "check_session": {"email": "new@example.com", "id": 108334, "workspaceId": 47784},
                "subscription_status": {"hasActiveSubscription": False, "freeExportsRemaining": 1},
            }

    monkeypatch.setattr("platforms.quickframe.protocol_mailbox.QuickFrameClient", FakeClient)

    worker = QuickFrameProtocolMailboxWorker(log_fn=lambda message: None)
    result = worker.run(email="new@example.com", otp_callback=lambda: "123456")

    assert result["success"] is True
    assert result["token"] == "tok_worker"
    assert result["user_id"] == "108334"
    assert result["workspace_id"] == "47784"
    assert result["cookie_header"] == "qf_session=sess_123"
    assert events == [("begin", "new@example.com"), ("complete", "123456")]


def test_quickframe_platform_declares_required_actions():
    platform = QuickFramePlatform(RegisterConfig(executor_type="protocol"))
    actions = {item["id"] for item in platform.get_platform_actions()}

    assert platform.supported_executors == ["protocol"]
    assert platform.supported_identity_modes == ["mailbox"]
    assert {
        "get_account_state",
        "keepalive_sync",
        "stop_keepalive",
        "resume_keepalive",
        "send_login_code",
        "relogin_email_code",
        "sync_quickframe2api",
    } <= actions
    assert {
        "keepalive_sync",
        "stop_keepalive",
        "resume_keepalive",
        "relogin_email_code",
        "sync_quickframe2api",
    } <= STATEFUL_ACTION_IDS


def test_quickframe_email_code_pattern_matches_auth0_email_body_only_after_prompt():
    body = (
        "ticket 001234\n"
        "Enter the following verification code when prompted:\n"
        "127861\n"
        "To protect your account, do not share this code.\n"
        "This code was requested from 72.46.139.83 at May 31, 2026, 7:07 PM UTC."
    )

    match = re.search(QUICKFRAME_EMAIL_CODE_PATTERN, body)

    assert match
    assert match.group(1) == "127861"


def test_quickframe_email_code_pattern_matches_auth0_html_body():
    body = """
    <table border="0" cellpadding="0" cellspacing="0" role="presentation" width="100%">
      <tbody><tr>
        <td align="left" style="font-size:0px;padding:0 0 32px;word-break:break-word;">
          <div style="font-family:Inter,Helvetica,Arial,sans-serif;font-size:16px;font-weight:500;line-height:24px;text-align:left;color:#c0c0c0;">
            Enter the following verification code when prompted:
          </div>
        </td>
      </tr>
      <!-- OTP code -->
      <tr>
        <td align="left" style="font-size:0px;padding:0 0 32px;word-break:break-word;">
          <div style="font-family:Inter,Helvetica,Arial,sans-serif;font-size:48px;font-weight:600;line-height:72px;text-align:left;color:#ffffff;">
            579619
          </div>
        </td>
      </tr>
      <tr>
        <td><div>To protect your account, do not share this code.</div></td>
      </tr>
      <tr>
        <td><div>This code was requested from 136.143.254.61 at May 31, 2026, 7:25 PM UTC.</div></td>
      </tr>
      <tr>
        <td><div>823 Congress Ave #1827 Austin, TX 78768</div></td>
      </tr>
    </tbody></table>
    """

    match = re.search(QUICKFRAME_EMAIL_CODE_PATTERN, body)

    assert match
    assert match.group(1) == "579619"


def test_quickframe_send_login_code_persists_pending_auth0_state(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def begin_email_challenge(self, email):
            return {
                "quickframe_login_state": "state-123",
                "quickframe_login_identifier_url": "https://login.quickframe.com/u/login/identifier?state=state-123",
                "quickframe_challenge_url": "https://login.quickframe.com/u/login/passwordless-email-challenge?state=state-123",
                "quickframe_pending_cookies": [{"name": "auth0", "value": "sess"}],
                "quickframe_pending_cookie_header": "auth0=sess",
            }

    monkeypatch.setattr("platforms.quickframe.plugin.QuickFrameClient", FakeClient)

    with Session(engine) as session:
        model = AccountModel(platform="quickframe", email="new@example.com", password="")
        session.add(model)
        session.commit()
        session.refresh(model)
        account_id = int(model.id or 0)

    result = PlatformRuntime().execute_action(
        ActionExecutionCommand(
            platform="quickframe",
            account_id=account_id,
            action_id="send_login_code",
            params={},
        )
    )

    assert result.ok is True
    with Session(engine) as session:
        graph = load_account_graphs(session, [account_id])[account_id]
    credentials = {item["key"]: item["value"] for item in graph["credentials"]}
    assert credentials["quickframe_login_state"] == "state-123"
    assert credentials["quickframe_challenge_url"].endswith("passwordless-email-challenge?state=state-123")
    assert credentials["quickframe_pending_cookie_header"] == "auth0=sess"


def test_quickframe_relogin_email_code_refreshes_session_and_syncs(monkeypatch):
    init_kwargs: list[dict] = []
    sync_calls: list[tuple[bool, bool, str, str]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            init_kwargs.append(kwargs)
            self.challenge_url = kwargs.get("challenge_url") or ""

        def complete_email_challenge(self, code):
            assert code == "112233"
            return {
                "summary": {
                    "valid": True,
                    "email": "new@example.com",
                    "user_id": "108334",
                    "workspace_id": "47784",
                    "free_exports_remaining": 1,
                    "account_overview": {
                        "valid": True,
                        "email": "new@example.com",
                        "user_id": "108334",
                        "workspace_id": "47784",
                        "free_exports_remaining": 1,
                    },
                },
                "access_token": "tok_new",
                "cookies": "qf_session=sess_new",
                "cookie_header": "qf_session=sess_new",
                "quickframe_cookies": [{"name": "qf_session", "value": "sess_new"}],
            }

    def fake_sync(account, *, log_fn=None, heartbeat=False, check=False, **kwargs):
        sync_calls.append((heartbeat, check, account.token, account.extra.get("proxy_url", "")))
        return {"ok": True, "account": {"id": 7}}

    monkeypatch.setattr("platforms.quickframe.plugin.QuickFrameClient", FakeClient)
    monkeypatch.setattr("platforms.quickframe.plugin.sync_account_to_quickframe2api", fake_sync)

    platform = QuickFramePlatform(RegisterConfig(executor_type="protocol"))
    account = Account(
        platform="quickframe",
        email="new@example.com",
        password="",
        token="tok_old",
        extra={
            "quickframe_login_state": "state-123",
            "quickframe_challenge_url": "https://login.quickframe.com/u/login/passwordless-email-challenge?state=state-123",
            "quickframe_pending_cookie_header": "auth0=sess",
            "account_overview": {"legacy_extra": {"proxy_url": "http://proxy.example:8080"}},
        },
    )

    result = platform.execute_action("relogin_email_code", account, {"code": "112233"})

    assert result["ok"] is True
    assert result["data"]["access_token"] == "tok_new"
    assert result["data"]["session_refreshed"] is True
    assert result["data"]["quickframe_login_state"] == ""
    assert init_kwargs[0]["challenge_url"].endswith("passwordless-email-challenge?state=state-123")
    assert init_kwargs[0]["cookie_header"] == "auth0=sess"
    assert sync_calls == [(True, True, "tok_new", "http://proxy.example:8080")]


def test_quickframe_stop_and_resume_keepalive_persist_account_marker(monkeypatch):
    monkeypatch.setattr("platforms.quickframe.plugin.sync_account_to_quickframe2api", lambda *args, **kwargs: False)

    with Session(engine) as session:
        model = AccountModel(platform="quickframe", email="keepalive@example.com", password="")
        session.add(model)
        session.commit()
        session.refresh(model)
        account_id = int(model.id or 0)

    runtime = PlatformRuntime()
    stop_result = runtime.execute_action(
        ActionExecutionCommand(
            platform="quickframe",
            account_id=account_id,
            action_id="stop_keepalive",
            params={"reason": "manual"},
        )
    )

    assert stop_result.ok is True
    with Session(engine) as session:
        graph = load_account_graphs(session, [account_id])[account_id]
    assert graph["overview"]["quickframe_keepalive_disabled"] is True
    assert graph["overview"]["quickframe_keepalive_state"] == "disabled"
    assert graph["overview"]["quickframe2api_enable_auto_maintenance"] is False

    resume_result = runtime.execute_action(
        ActionExecutionCommand(
            platform="quickframe",
            account_id=account_id,
            action_id="resume_keepalive",
            params={},
        )
    )

    assert resume_result.ok is True
    with Session(engine) as session:
        graph = load_account_graphs(session, [account_id])[account_id]
    assert graph["overview"]["quickframe_keepalive_disabled"] is False
    assert graph["overview"]["quickframe_keepalive_state"] == "enabled"
    assert graph["overview"]["quickframe2api_enable_auto_maintenance"] is True
