from __future__ import annotations

import pytest

import core.base_mailbox as mailbox_module
from core.base_mailbox import CloudMailMailbox, MAILBOX_FACTORY_REGISTRY, MailboxAccount
from infrastructure.provider_definitions_repository import _definition_from_seed


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


def test_cloud_mail_seed_definition_is_available():
    definition = _definition_from_seed("mailbox", "cloud_mail_api")

    assert definition is not None
    assert definition.provider_key == "cloud_mail_api"
    assert definition.driver_type == "cloud_mail_api"
    assert "cloud_mail_api_url" in {field["key"] for field in definition.get_fields()}
    assert MAILBOX_FACTORY_REGISTRY["cloud_mail_api"]
    assert MAILBOX_FACTORY_REGISTRY["cloud_mail"]


def test_get_email_creates_cloud_mail_user(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, proxies=None, timeout=None):
        calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "proxies": proxies,
                "timeout": timeout,
            }
        )
        return FakeResponse({"code": 200, "message": "success", "data": None})

    monkeypatch.setattr(mailbox_module.requests, "post", fake_post)

    mailbox = CloudMailMailbox(
        api_url="https://mail.example.com",
        public_token="public-token",
        domain="example.com",
        prefix="reg",
        password="Secret123!",
    )

    account = mailbox.get_email()

    assert account.email.startswith("reg.")
    assert account.email.endswith("@example.com")
    assert account.account_id == account.email
    assert account.extra["provider_resource"]["provider_name"] == "cloud_mail"
    assert calls == [
        {
            "url": "https://mail.example.com/api/public/addUser",
            "json": {"list": [{"email": account.email, "password": "Secret123!"}]},
            "headers": {
                "accept": "application/json",
                "content-type": "application/json",
                "authorization": "public-token",
            },
            "proxies": None,
            "timeout": 15,
        }
    ]


def test_get_email_does_not_double_api_prefix(monkeypatch):
    seen_urls = []

    def fake_post(url, json=None, headers=None, proxies=None, timeout=None):
        seen_urls.append(url)
        return FakeResponse({"code": 200, "data": None})

    monkeypatch.setattr(mailbox_module.requests, "post", fake_post)

    mailbox = CloudMailMailbox(
        api_url="https://mail.example.com/api",
        public_token="public-token",
        domain="example.com",
    )
    mailbox.get_email()

    assert seen_urls == ["https://mail.example.com/api/public/addUser"]


def test_get_current_ids_reads_public_email_list(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, proxies=None, timeout=None):
        calls.append((url, json))
        return FakeResponse(
            {
                "code": 200,
                "data": [
                    {"emailId": 10, "subject": "old"},
                    {"emailId": 11, "subject": "new"},
                ],
            }
        )

    monkeypatch.setattr(mailbox_module.requests, "post", fake_post)

    mailbox = CloudMailMailbox("https://mail.example.com", "token", "example.com")
    account = MailboxAccount(email="reg.abc@example.com", account_id="reg.abc@example.com")

    assert mailbox.get_current_ids(account) == {"10", "11"}
    assert calls == [
        (
            "https://mail.example.com/api/public/emailList",
            {
                "toEmail": "reg.abc@example.com",
                "type": 0,
                "isDel": 0,
                "size": 50,
                "num": 1,
            },
        )
    ]


def test_wait_for_code_extracts_new_message(monkeypatch):
    def fake_post(url, json=None, headers=None, proxies=None, timeout=None):
        return FakeResponse(
            {
                "code": 200,
                "data": [
                    {"emailId": 1, "subject": "old code 111111", "content": ""},
                    {"emailId": 2, "subject": "Verify", "content": "Your verification code is 654321."},
                ],
            }
        )

    monkeypatch.setattr(mailbox_module.requests, "post", fake_post)

    mailbox = CloudMailMailbox("https://mail.example.com", "token", "example.com")
    account = MailboxAccount(email="reg.abc@example.com")

    assert mailbox.wait_for_code(account, timeout=1, before_ids={"1"}) == "654321"


def test_wait_for_link_extracts_new_message(monkeypatch):
    def fake_post(url, json=None, headers=None, proxies=None, timeout=None):
        return FakeResponse(
            {
                "code": 200,
                "data": [
                    {
                        "emailId": 3,
                        "subject": "Please verify",
                        "content": "Verify your account at https://app.example.com/auth/callback?token=abc",
                    },
                ],
            }
        )

    monkeypatch.setattr(mailbox_module.requests, "post", fake_post)

    mailbox = CloudMailMailbox("https://mail.example.com", "token", "example.com")
    account = MailboxAccount(email="reg.abc@example.com")

    assert mailbox.wait_for_link(account, timeout=1) == "https://app.example.com/auth/callback?token=abc"


def test_missing_public_token_fails_before_request(monkeypatch):
    def fake_post(*args, **kwargs):
        raise AssertionError("request should not be sent")

    monkeypatch.setattr(mailbox_module.requests, "post", fake_post)
    mailbox = CloudMailMailbox("https://mail.example.com", "", "example.com")

    with pytest.raises(RuntimeError, match="public token"):
        mailbox.get_email()


def test_cloud_mail_api_error_is_reported(monkeypatch):
    def fake_post(url, json=None, headers=None, proxies=None, timeout=None):
        return FakeResponse({"code": 401, "message": "invalid token"})

    monkeypatch.setattr(mailbox_module.requests, "post", fake_post)
    mailbox = CloudMailMailbox("https://mail.example.com", "bad-token", "example.com")

    with pytest.raises(RuntimeError, match="invalid token"):
        mailbox.get_email()
