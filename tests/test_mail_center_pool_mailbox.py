from __future__ import annotations

import pytest

import core.base_mailbox as mailbox_module
from core.base_mailbox import MAILBOX_FACTORY_REGISTRY, MailboxAccount, MailCenterPoolMailbox
from infrastructure.provider_definitions_repository import _definition_from_seed


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


def test_mail_center_pool_seed_definition_is_available():
    definition = _definition_from_seed("mailbox", "mail_center_pool_api")

    assert definition is not None
    assert definition.driver_type == "mail_center_pool_api"
    fields = {field["key"]: field for field in definition.get_fields()}
    assert fields["mail_center_prefix"]["placeholder"].startswith("留空")
    assert fields["mail_center_domain_strategy"]["type"] == "select"
    assert MAILBOX_FACTORY_REGISTRY["mail_center_pool_api"]


def test_pool_allocates_without_prefix_and_preserves_policy_strategy(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, proxies=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "proxies": proxies, "timeout": timeout})
        return FakeResponse({
            "ok": True,
            "data": {
                "mailboxId": "mailbox-123",
                "email": "x8f2k9@overseasapi.com",
                "domain": "overseasapi.com",
                "strategy": "least_used",
            },
        }, 201)

    monkeypatch.setattr(mailbox_module.requests, "post", fake_post)
    mailbox = MailCenterPoolMailbox(
        api_url="https://mail-center.aiid.qzz.io",
        integration_token="mci_live_secret",
        strategy="least_used",
        prefix="",
    )

    account = mailbox.get_email()

    assert account.email == "x8f2k9@overseasapi.com"
    assert account.account_id == "mailbox-123"
    assert account.extra["provider_resource"]["provider_name"] == "mail_center_pool"
    assert calls == [{
        "url": "https://mail-center.aiid.qzz.io/api/v1/integrations/mailboxes",
        "json": {"strategy": "least_used"},
        "headers": {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": "Bearer mci_live_secret",
        },
        "proxies": None,
        "timeout": 15,
    }]


def test_pool_sends_optional_prefix_and_specific_domain(monkeypatch):
    seen = []

    def fake_post(url, json=None, headers=None, proxies=None, timeout=None):
        seen.append(json)
        return FakeResponse({"ok": True, "data": {
            "mailboxId": "mailbox-456",
            "email": "aar.abc@aiid.qzz.io",
            "domain": "aiid.qzz.io",
            "strategy": "specific",
        }}, 201)

    monkeypatch.setattr(mailbox_module.requests, "post", fake_post)
    mailbox = MailCenterPoolMailbox(
        "https://mail-center.aiid.qzz.io/",
        "token",
        strategy="random",
        domain="AIID.QZZ.IO",
        prefix="aar",
    )

    assert mailbox.get_email().email == "aar.abc@aiid.qzz.io"
    assert seen == [{"strategy": "random", "prefix": "aar", "domain": "aiid.qzz.io"}]


def test_pool_reads_messages_by_opaque_mailbox_id(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, proxies=None, timeout=None):
        calls.append((url, json))
        return FakeResponse({"code": 200, "message": "success", "data": [
            {"emailId": "message-1", "subject": "Your verification code is 654321", "content": ""},
        ]})

    monkeypatch.setattr(mailbox_module.requests, "post", fake_post)
    mailbox = MailCenterPoolMailbox("https://mail-center.aiid.qzz.io", "token")
    allocated = MailboxAccount(email="random@aiid.qzz.io", account_id="box/id")

    assert mailbox.get_current_ids(allocated) == {"message-1"}
    assert mailbox.wait_for_code(allocated, timeout=1) == "654321"
    assert calls[0] == (
        "https://mail-center.aiid.qzz.io/api/v1/integrations/mailboxes/box%2Fid/messages",
        {"type": 0, "isDel": 0, "size": 50, "num": 1, "timeSort": "desc"},
    )


def test_pool_reports_domain_policy_errors(monkeypatch):
    def fake_post(url, json=None, headers=None, proxies=None, timeout=None):
        return FakeResponse({"ok": False, "error": {"code": "NO_ELIGIBLE_DOMAIN", "message": "No eligible domain"}}, 409)

    monkeypatch.setattr(mailbox_module.requests, "post", fake_post)
    mailbox = MailCenterPoolMailbox("https://mail-center.aiid.qzz.io", "token")

    with pytest.raises(RuntimeError, match="No eligible domain"):
        mailbox.get_email()
