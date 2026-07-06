from __future__ import annotations

import json

from sqlmodel import Session

from core.account_graph import load_account_graphs
from core.base_platform import Account, RegisterConfig
from core.db import AccountModel, engine
from core.platform_accounts import build_platform_account
from domain.actions import ActionExecutionCommand
from infrastructure.platform_runtime import PlatformRuntime, STATEFUL_ACTION_IDS
from platforms.freebeat.core import (
    FREEBEAT_EN_NEXT_ROUTER_STATE_TREE,
    FREEBEAT_ZH_VIDEO_NEXT_ROUTER_STATE_TREE,
    FREEBEAT_LEGACY_NEXT_ROUTER_STATE_TREE,
    FREEBEAT_DEFAULT_NEXT_ACTION,
    FREEBEAT_FALLBACK_NEXT_ACTIONS,
    FREEBEAT_DEFAULT_NEXT_ROUTER_STATE_TREE,
    FreebeatClient,
    _total_credits_from_state,
    _extract_login_payload,
)
from platforms.freebeat.browser_email import _candidate_page_urls, _is_freebeat_page_url
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


def test_freebeat_next_action_login_parser_accepts_rsc_prefix():
    payload = (
        '2:"$Sreact.fragment"\n'
        '3:I[829209,["/_next/static/chunks/7b2195c52b577e49.js"],"default"]\n'
        '4:{"code":0,"msg":"","data":{"token":"tok_456","accessToken":"tok_456",'
        '"deviceToken":"dev_456","userId":"user_456","newUser":true,"expireTime":1781635058486}}\n'
    )

    parsed = _extract_login_payload(payload)

    assert parsed["data"]["token"] == "tok_456"
    assert parsed["data"]["userId"] == "user_456"


def test_freebeat_verify_email_code_uses_english_root_action_route_by_default():
    calls: list[dict] = []

    class Response:
        status_code = 200
        text = (
            '2:"$Sreact.fragment"\n'
            '3:{"code":0,"msg":"","data":{"token":"tok_123","accessToken":"tok_123",'
            '"deviceToken":"dev_123","userId":"user_123","expireTime":1781635058486}}\n'
        )

    client = FreebeatClient(log_fn=lambda message: None, deployment_id="dpl_test")
    client._warmup_frontend_session = lambda: None

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response()

    client.s.post = fake_post

    result = client.verify_email_code("user@example.com", "123456")

    assert result["data"]["token"] == "tok_123"
    assert calls[0]["url"] == "https://freebeat.ai/"
    assert calls[0]["headers"]["referer"] == "https://freebeat.ai/"
    assert calls[0]["headers"]["accept-language"] == "en-US,en;q=0.9"
    assert calls[0]["headers"]["next-action"] == FREEBEAT_DEFAULT_NEXT_ACTION
    assert calls[0]["headers"]["cache-control"] == "no-cache"
    assert calls[0]["headers"]["pragma"] == "no-cache"
    assert calls[0]["headers"]["next-router-state-tree"] == FREEBEAT_DEFAULT_NEXT_ROUTER_STATE_TREE
    assert calls[0]["headers"]["x-deployment-id"] == "dpl_test"
    assert calls[0]["data"] == '[{"email":"user@example.com","code":"123456"}]'


def test_freebeat_verify_email_code_keeps_explicit_frontend_path():
    calls: list[dict] = []

    class Response:
        status_code = 200
        text = (
            '2:"$Sreact.fragment"\n'
            '3:{"code":0,"msg":"","data":{"token":"tok_123","accessToken":"tok_123",'
            '"deviceToken":"dev_123","userId":"user_123","expireTime":1781635058486}}\n'
        )

    client = FreebeatClient(log_fn=lambda message: None, frontend_path="/tw", deployment_id="dpl_test")
    client._warmup_frontend_session = lambda: None

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response()

    client.s.post = fake_post

    result = client.verify_email_code("user@example.com", "123456", next_router_state_tree="legacy-tree")

    assert result["data"]["token"] == "tok_123"
    assert calls[0]["url"] == "https://freebeat.ai/tw"
    assert calls[0]["headers"]["referer"] == "https://freebeat.ai/tw"
    assert calls[0]["headers"]["next-router-state-tree"] == "legacy-tree"


def test_freebeat_verify_email_code_pairs_explicit_root_path_with_english_router_state():
    calls: list[dict] = []

    class Response:
        status_code = 200
        text = (
            '2:"$Sreact.fragment"\n'
            '3:{"code":0,"msg":"","data":{"token":"tok_123","accessToken":"tok_123",'
            '"deviceToken":"dev_123","userId":"user_123","expireTime":1781635058486}}\n'
        )

    client = FreebeatClient(log_fn=lambda message: None, frontend_path="/", deployment_id="dpl_test")
    client._warmup_frontend_session = lambda: None

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response()

    client.s.post = fake_post

    result = client.verify_email_code("user@example.com", "123456")

    assert result["data"]["token"] == "tok_123"
    assert calls[0]["url"] == "https://freebeat.ai/"
    assert calls[0]["headers"]["accept-language"] == "en-US,en;q=0.9"
    assert calls[0]["headers"]["next-router-state-tree"] == FREEBEAT_EN_NEXT_ROUTER_STATE_TREE


def test_freebeat_verify_email_code_falls_back_to_zh_video_generator_when_default_action_missing():
    calls: list[dict] = []

    class Response404:
        status_code = 404
        text = "Server action not found."

    class Response200:
        status_code = 200
        text = (
            '2:"$Sreact.fragment"\n'
            '3:{"code":0,"msg":"","data":{"token":"tok_123","accessToken":"tok_123",'
            '"deviceToken":"dev_123","userId":"user_123","expireTime":1781635058486}}\n'
        )

    client = FreebeatClient(log_fn=lambda message: None, deployment_id="dpl_test")
    client._warmup_frontend_session = lambda: None

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response404() if len(calls) == 1 else Response200()

    client.s.post = fake_post

    result = client.verify_email_code("user@example.com", "123456")

    assert result["data"]["token"] == "tok_123"
    assert [item["url"] for item in calls] == ["https://freebeat.ai/", "https://freebeat.ai/zh/ai-video-generator"]
    assert calls[0]["headers"]["next-router-state-tree"] == FREEBEAT_DEFAULT_NEXT_ROUTER_STATE_TREE
    assert calls[1]["headers"]["referer"] == "https://freebeat.ai/zh/ai-video-generator"
    assert calls[1]["headers"]["accept-language"] == "zh-CN,zh;q=0.9,en;q=0.8"
    assert calls[1]["headers"]["next-router-state-tree"] == FREEBEAT_ZH_VIDEO_NEXT_ROUTER_STATE_TREE
    assert calls[1]["data"] == '[{"email":"user@example.com","code":"123456"}]'


def test_freebeat_verify_email_code_falls_back_to_legacy_tw_after_default_and_zh_video_miss():
    calls: list[dict] = []

    class Response404:
        status_code = 404
        text = "Server action not found."

    class Response200:
        status_code = 200
        text = (
            '2:"$Sreact.fragment"\n'
            '3:{"code":0,"msg":"","data":{"token":"tok_123","accessToken":"tok_123",'
            '"deviceToken":"dev_123","userId":"user_123","expireTime":1781635058486}}\n'
        )

    client = FreebeatClient(log_fn=lambda message: None, deployment_id="dpl_test")
    client._warmup_frontend_session = lambda: None

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response200() if len(calls) == 3 else Response404()

    client.s.post = fake_post

    result = client.verify_email_code("user@example.com", "123456")

    assert result["data"]["token"] == "tok_123"
    assert [item["url"] for item in calls] == [
        "https://freebeat.ai/",
        "https://freebeat.ai/zh/ai-video-generator",
        "https://freebeat.ai/tw",
    ]
    assert calls[2]["headers"]["referer"] == "https://freebeat.ai/tw"
    assert calls[2]["headers"]["next-router-state-tree"] == FREEBEAT_LEGACY_NEXT_ROUTER_STATE_TREE


def test_freebeat_verify_email_code_retries_fallback_action_after_all_routes_miss():
    calls: list[dict] = []

    class Response404:
        status_code = 404
        text = "Server action not found."

    class Response200:
        status_code = 200
        text = (
            '2:"$Sreact.fragment"\n'
            '3:{"code":0,"msg":"","data":{"token":"tok_123","accessToken":"tok_123",'
            '"deviceToken":"dev_123","userId":"user_123","expireTime":1781635058486}}\n'
        )

    client = FreebeatClient(log_fn=lambda message: None, deployment_id="dpl_test")
    client._warmup_frontend_session = lambda: None

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response200() if len(calls) == 4 else Response404()

    client.s.post = fake_post

    result = client.verify_email_code("user@example.com", "123456")

    assert result["data"]["token"] == "tok_123"
    assert [item["url"] for item in calls] == [
        "https://freebeat.ai/",
        "https://freebeat.ai/zh/ai-video-generator",
        "https://freebeat.ai/tw",
        "https://freebeat.ai/",
    ]
    assert [item["headers"]["next-action"] for item in calls] == [
        FREEBEAT_DEFAULT_NEXT_ACTION,
        FREEBEAT_DEFAULT_NEXT_ACTION,
        FREEBEAT_DEFAULT_NEXT_ACTION,
        FREEBEAT_FALLBACK_NEXT_ACTIONS[0],
    ]


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


def test_freebeat_send_code_includes_turnstile_token_when_provided():
    calls: list[dict] = []

    class Response200:
        status_code = 200
        text = '{"code":0,"data":true}'

        def json(self):
            return {"code": 0, "data": True}

    client = FreebeatClient(log_fn=lambda message: None)

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return Response200()

    client.s.request = fake_request

    result = client.send_email_verify_code(
        "user@example.com",
        turnstile_token="turnstile-token-123",
    )

    assert result["data"] is True
    body = json.loads(calls[0]["data"])
    assert body == {
        "email": "user@example.com",
        "verifySource": "WEB_SHOPIFY_LOGIN",
        "turnstileToken": "turnstile-token-123",
    }


def test_freebeat_browser_send_code_tries_login_pages_after_root():
    assert _candidate_page_urls("/") == [
        "https://freebeat.ai/",
        "https://freebeat.ai/login?redirectTo=%2F",
        "https://freebeat.ai/tw/login?redirectTo=%2Ftw",
    ]


def test_freebeat_browser_send_code_rejects_external_oauth_urls():
    assert _is_freebeat_page_url("https://freebeat.ai/tw/login?redirectTo=%2Ftw") is True
    assert _is_freebeat_page_url("https://accounts.google.com/v3/signin/identifier") is False


def test_freebeat_questionnaire_check_failure_does_not_block_submit(monkeypatch):
    client = FreebeatClient(log_fn=lambda message: None)
    submit_calls: list[tuple[str, str]] = []

    def fake_check(token, *, questionnaire_code):
        raise TimeoutError("questionnaire check not ready")

    def fake_submit(token, *, questionnaire_code, answers=None):
        submit_calls.append((token, questionnaire_code))
        return {"code": 0, "data": {"creditsGranted": 300}}

    monkeypatch.setattr(client, "questionnaire_check", fake_check)
    monkeypatch.setattr(client, "questionnaire_submit", fake_submit)

    result = client.claim_questionnaire("tok_123", retry_attempts=1)

    assert result["status"] == "claimed"
    assert result["credits_granted"] == 300
    assert result["check"]["status"] == "check_failed"
    assert submit_calls == [("tok_123", "onboarding_v1")]


def test_freebeat_questionnaire_submit_retries_transient_failure(monkeypatch):
    client = FreebeatClient(log_fn=lambda message: None)
    submit_calls: list[str] = []
    sleep_calls: list[float] = []

    monkeypatch.setattr("platforms.freebeat.core.time.sleep", sleep_calls.append)
    monkeypatch.setattr(
        client,
        "questionnaire_check",
        lambda token, *, questionnaire_code: {"code": 0, "data": {"eligible": True}},
    )

    def fake_submit(token, *, questionnaire_code, answers=None):
        submit_calls.append(token)
        if len(submit_calls) == 1:
            raise RuntimeError("temporary questionnaire submit failure")
        return {"code": 0, "data": {"creditsGranted": 300}}

    monkeypatch.setattr(client, "questionnaire_submit", fake_submit)

    result = client.claim_questionnaire("tok_123", retry_attempts=2, retry_delay_seconds=0.25)

    assert result["status"] == "claimed"
    assert submit_calls == ["tok_123", "tok_123"]
    assert sleep_calls == [0.25]


def test_freebeat_daily_sign_in_status_failure_does_not_block_submit(monkeypatch):
    client = FreebeatClient(log_fn=lambda message: None)
    submit_calls: list[str] = []

    def fake_status(token):
        raise TimeoutError("sign-in status not ready")

    def fake_submit(token):
        submit_calls.append(token)
        return {"code": 0, "data": {"granted": True, "rewardAmount": 200}}

    monkeypatch.setattr(client, "signin_status", fake_status)
    monkeypatch.setattr(client, "signin_submit", fake_submit)

    result = client.daily_sign_in("tok_123", retry_attempts=1)

    assert result["status"] == "signed"
    assert result["reward_amount"] == 200
    assert result["before"]["status"] == "status_failed"
    assert submit_calls == ["tok_123"]


def test_freebeat_daily_sign_in_uses_cached_status_payload(monkeypatch):
    client = FreebeatClient(log_fn=lambda message: None)
    submit_calls: list[str] = []

    def fake_status(token):
        raise AssertionError("cached status should avoid duplicate signin/status request")

    def fake_submit(token):
        submit_calls.append(token)
        return {"code": 0, "data": {"granted": True, "rewardAmount": 200}}

    monkeypatch.setattr(client, "signin_status", fake_status)
    monkeypatch.setattr(client, "signin_submit", fake_submit)

    result = client.daily_sign_in(
        "tok_123",
        before_status={"code": 0, "data": {"canSignIn": True, "signedToday": False}},
    )

    assert result["status"] == "signed"
    assert result["reward_amount"] == 200
    assert submit_calls == ["tok_123"]


def test_freebeat_daily_sign_in_submit_retries_transient_failure(monkeypatch):
    client = FreebeatClient(log_fn=lambda message: None)
    submit_calls: list[str] = []
    sleep_calls: list[float] = []

    monkeypatch.setattr("platforms.freebeat.core.time.sleep", sleep_calls.append)
    monkeypatch.setattr(
        client,
        "signin_status",
        lambda token: {"code": 0, "data": {"canSignIn": True, "rewardAmount": 200}},
    )

    def fake_submit(token):
        submit_calls.append(token)
        if len(submit_calls) == 1:
            raise RuntimeError("temporary sign-in submit failure")
        return {"code": 0, "data": {"granted": True, "rewardAmount": 200}}

    monkeypatch.setattr(client, "signin_submit", fake_submit)

    result = client.daily_sign_in("tok_123", retry_attempts=2, retry_delay_seconds=0.25)

    assert result["status"] == "signed"
    assert submit_calls == ["tok_123", "tok_123"]
    assert sleep_calls == [0.25]


def test_freebeat_fetch_account_state_after_reward_polls_until_credits_refresh(monkeypatch):
    client = FreebeatClient(log_fn=lambda message: None)
    states = [
        {"credits": {"totalCredits": 1000}, "signin_status": {"signedToday": True}},
        {"credits": {"totalCredits": 1200}, "signin_status": {"signedToday": True}},
    ]
    calls: list[tuple[str, float | None]] = []
    sleep_calls: list[float] = []

    def fake_fetch(token, *, timeout_seconds=None):
        calls.append((token, timeout_seconds))
        return states.pop(0)

    monkeypatch.setattr(client, "fetch_account_state", fake_fetch)
    monkeypatch.setattr("platforms.freebeat.core.time.sleep", sleep_calls.append)

    result = client.fetch_account_state_after_reward(
        "tok_123",
        expected_min_total_credits=1200,
        attempts=2,
        interval_seconds=0.25,
        timeout_seconds=3.0,
    )

    assert _total_credits_from_state(result) == 1200
    assert calls == [("tok_123", 3.0), ("tok_123", 3.0)]
    assert sleep_calls == [0.25]


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
    monkeypatch.setattr("platforms.freebeat.protocol_mailbox.time.sleep", lambda seconds: None)

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

        def fetch_account_state(self, token, **kwargs):
            calls.append(("state", token))
            return {
                "token": token,
                "credits": {"free": "500", "boost": "300", "event": "200", "membership": "0", "totalCredits": 1000},
                "signin_status": {"signedToday": True, "canSignIn": False, "serverUtcDate": "2026-05-18"},
                "last_keepalive_at": "2026-05-18T00:00:00Z",
            }

        def claim_questionnaire(self, token, **kwargs):
            calls.append(("questionnaire", token))
            return {"status": "claimed", "credits_granted": 300}

        def daily_sign_in(self, token, **kwargs):
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


def test_freebeat_protocol_mailbox_worker_browser_sends_code_and_merges_cookies(monkeypatch):
    calls: list[tuple[str, object]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.cookies = ""

        def merge_cookie_header(self, cookie_header):
            self.cookies = cookie_header
            calls.append(("merge_cookie", cookie_header))

        def send_email_verify_code(self, email, *, verify_source):
            raise AssertionError("protocol send should not be used when browser send succeeds")

        def verify_email_code(self, email, code, *, next_action=None, next_router_state_tree=None):
            calls.append(("login_cookie", self.cookies))
            return {
                "code": 0,
                "data": {
                    "token": "tok_browser",
                    "accessToken": "tok_browser",
                    "deviceToken": "dev_browser",
                    "userId": "user_browser",
                    "newUser": True,
                    "expireTime": 1781635058486,
                },
            }

        def fetch_account_state(self, token, **kwargs):
            return {
                "token": token,
                "credits": {"totalCredits": 1000},
                "signin_status": {"signedToday": True, "canSignIn": False},
            }

        def claim_questionnaire(self, token, **kwargs):
            return {"status": "skipped"}

        def daily_sign_in(self, token, **kwargs):
            return {"status": "skipped", "reward_amount": 0}

    def fake_browser_send(email, **kwargs):
        calls.append(("browser_send", {"email": email, **kwargs}))
        return {
            "ok": True,
            "browser_sent": True,
            "cookie_header": "fb_session=sess_123",
            "turnstile_token": "turnstile-token-123",
            "response": {"code": 0, "data": True},
        }

    monkeypatch.setattr("platforms.freebeat.protocol_mailbox.FreebeatClient", FakeClient)
    monkeypatch.setattr("platforms.freebeat.protocol_mailbox.send_email_verify_code_in_browser", fake_browser_send)

    worker = FreebeatProtocolMailboxWorker(
        proxy="socks5://xray:20004",
        log_fn=lambda message: None,
        browser_send_code=True,
        browser_send_code_headless=True,
        browser_send_code_timeout_seconds=30,
    )
    result = worker.run(
        email="user@example.com",
        otp_callback=lambda: "123456",
        auto_questionnaire=False,
        auto_daily_sign_in=False,
    )

    assert result["success"] is True
    assert result["token"] == "tok_browser"
    assert calls[0][0] == "browser_send"
    assert calls[0][1]["proxy"] == "socks5://xray:20004"
    assert calls[0][1]["headless"] is True
    assert ("merge_cookie", "fb_session=sess_123") in calls
    assert ("login_cookie", "fb_session=sess_123") in calls


def test_freebeat_protocol_mailbox_worker_saves_token_when_state_refresh_times_out(monkeypatch):
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr("platforms.freebeat.protocol_mailbox.time.sleep", lambda seconds: None)

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

        def fetch_account_state(self, token, **kwargs):
            calls.append(("state", token))
            raise TimeoutError("credits timeout")

        def claim_questionnaire(self, token, **kwargs):
            calls.append(("questionnaire", token))
            return {"status": "claimed", "credits_granted": 300}

        def daily_sign_in(self, token, **kwargs):
            calls.append(("signin", token))
            return {"status": "signed", "reward_amount": 200}

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
    assert result["questionnaire"]["status"] == "claimed"
    assert result["daily_sign_in"]["status"] == "signed"
    assert calls[2:5] == [("state", "tok_partial"), ("state", "tok_partial"), ("state", "tok_partial")]
    assert ("questionnaire", "tok_partial") in calls
    assert ("signin", "tok_partial") in calls
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
    synced_proxy_urls: list[str] = []

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

    def fake_sync(account, *args, **kwargs):
        synced_proxy_urls.append(str((account.extra or {}).get("proxy_url") or ""))
        return {"ok": True}

    monkeypatch.setattr("platforms.freebeat.plugin.FreebeatClient", FakeClient)
    monkeypatch.setattr("platforms.freebeat.plugin.sync_account_to_freebeat2api", fake_sync)

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
    assert synced_proxy_urls == ["http://proxy.example:8080"]


def test_freebeat_relogin_syncs_proxy_param_to_freebeat2api(monkeypatch):
    synced_proxy_urls: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def verify_email_code(self, email, code, *, next_action=None, next_router_state_tree=None):
            return {
                "code": 0,
                "data": {
                    "token": "tok_proxy_param",
                    "accessToken": "tok_proxy_param",
                    "deviceToken": "dev_proxy_param",
                    "userId": "user_proxy_param",
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
            return {"cookies": "authToken=tok_proxy_param", "cookie_header": "authToken=tok_proxy_param"}

    def fake_sync(account, *args, **kwargs):
        synced_proxy_urls.append(str((account.extra or {}).get("proxy_url") or ""))
        return {"ok": True}

    monkeypatch.setattr("platforms.freebeat.plugin.FreebeatClient", FakeClient)
    monkeypatch.setattr("platforms.freebeat.plugin.sync_account_to_freebeat2api", fake_sync)

    platform = FreebeatPlatform(RegisterConfig(executor_type="protocol"))
    account = Account(platform="freebeat", email="user@example.com", password="", token="tok_old")

    result = platform.execute_action(
        "relogin_email_code",
        account,
        {"code": "112233", "proxy": "http://override-proxy.example:8080"},
    )

    assert result["ok"] is True
    assert synced_proxy_urls == ["http://override-proxy.example:8080"]


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
    load_calls: list[dict] = []

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
    def fake_load_state(self, account, **kwargs):
        load_calls.append(dict(kwargs))
        total = 1000 if len(load_calls) == 1 else 1200
        return {
            "token": account.token,
            "access_token": account.token,
            "credits": {"totalCredits": total},
            "summary": {
                "valid": True,
                "email": account.email,
                "user_id": account.user_id,
                "access_token": account.token,
                "total_credits": total,
            },
        }

    monkeypatch.setattr(FreebeatPlatform, "_load_state", fake_load_state)

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
    assert result["data"]["total_credits"] == 1200
    assert result["data"]["freebeat2api_synced"] is True
    assert load_calls == [{}, {"expected_min_total_credits": 1200}]
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


def test_freebeat_stop_and_resume_keepalive_persist_account_marker(monkeypatch):
    monkeypatch.setattr("platforms.freebeat.plugin.sync_account_to_freebeat2api", lambda *args, **kwargs: False)
    with Session(engine) as session:
        model = AccountModel(platform="freebeat", email="keepalive@example.com", password="")
        session.add(model)
        session.commit()
        session.refresh(model)
        account_id = int(model.id or 0)

    runtime = PlatformRuntime()

    stop_result = runtime.execute_action(
        ActionExecutionCommand(
            platform="freebeat",
            account_id=account_id,
            action_id="stop_keepalive",
            params={"reason": "manual"},
        )
    )

    assert stop_result.ok is True
    with Session(engine) as session:
        graph = load_account_graphs(session, [account_id])[account_id]
    assert graph["overview"]["freebeat_keepalive_disabled"] is True
    assert graph["overview"]["freebeat_keepalive_state"] == "disabled"
    assert graph["overview"]["freebeat2api_enable_auto_maintenance"] is False

    resume_result = runtime.execute_action(
        ActionExecutionCommand(
            platform="freebeat",
            account_id=account_id,
            action_id="resume_keepalive",
            params={},
        )
    )

    assert resume_result.ok is True
    with Session(engine) as session:
        graph = load_account_graphs(session, [account_id])[account_id]
    assert graph["overview"]["freebeat_keepalive_disabled"] is False
    assert graph["overview"]["freebeat_keepalive_state"] == "enabled"
    assert graph["overview"]["freebeat2api_enable_auto_maintenance"] is True
