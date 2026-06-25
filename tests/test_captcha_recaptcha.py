from __future__ import annotations


class Response:
    def __init__(self, payload: dict, text: str = ""):
        self._payload = payload
        self.text = text or str(payload)

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_twocaptcha_solves_recaptcha(monkeypatch):
    import requests
    import time

    from providers.captcha.twocaptcha import TwoCaptcha

    posts: list[dict] = []

    def fake_post(url, *, data=None, timeout=None):
        posts.append({"url": url, "data": data, "timeout": timeout})
        return Response({"status": 1, "request": "task-123"})

    def fake_get(url, *, params=None, timeout=None):
        return Response({"status": 1, "request": "recaptcha-token-123"})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    token = TwoCaptcha("sk-test").solve_recaptcha(
        "https://login.quickframe.com/u/login/identifier",
        "6LcSiteKey",
        enterprise=True,
        action="login",
    )

    assert token == "recaptcha-token-123"
    assert posts[0]["data"]["method"] == "userrecaptcha"
    assert posts[0]["data"]["googlekey"] == "6LcSiteKey"
    assert posts[0]["data"]["enterprise"] == 1
    assert posts[0]["data"]["action"] == "login"


def test_yescaptcha_solves_recaptcha(monkeypatch):
    import time

    from providers.captcha.yescaptcha import YesCaptcha

    calls: list[dict] = []

    def fake_insecure_request(func, url, *, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        if url.endswith("/createTask"):
            return Response({"taskId": "task-123"})
        return Response({"status": "ready", "solution": {"gRecaptchaResponse": "recaptcha-token-123"}})

    monkeypatch.setattr("providers.captcha.yescaptcha.insecure_request", fake_insecure_request)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    token = YesCaptcha("sk-test").solve_recaptcha(
        "https://login.quickframe.com/u/login/identifier",
        "6LcSiteKey",
        enterprise=True,
        action="login",
    )

    assert token == "recaptcha-token-123"
    task = calls[0]["json"]["task"]
    assert task["type"] == "RecaptchaV2EnterpriseTaskProxyless"
    assert task["websiteURL"] == "https://login.quickframe.com/u/login/identifier"
    assert task["websiteKey"] == "6LcSiteKey"
    assert task["pageAction"] == "login"
