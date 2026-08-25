from __future__ import annotations

import pytest

from infrastructure.provider_bootstrap import mail_center_pool_payload


def test_mail_center_bootstrap_is_disabled_without_a_token():
    assert mail_center_pool_payload({}) is None


def test_mail_center_bootstrap_keeps_optional_domain_and_prefix_empty():
    payload = mail_center_pool_payload({
        "MAIL_CENTER_INTEGRATION_TOKEN": "mci_live_secret",
        "MAIL_CENTER_API_URL": "https://mail-center.aiid.qzz.io/",
        "MAIL_CENTER_DOMAIN_STRATEGY": "least_used",
    })

    assert payload is not None
    assert payload["is_default"] is True
    assert payload["config"] == {
        "mail_center_api_url": "https://mail-center.aiid.qzz.io",
        "mail_center_domain_strategy": "least_used",
        "mail_center_domain": "",
        "mail_center_prefix": "",
    }
    assert payload["auth"] == {"mail_center_integration_token": "mci_live_secret"}


def test_mail_center_bootstrap_validates_strategy_and_normalizes_specific_domain():
    payload = mail_center_pool_payload({
        "MAIL_CENTER_INTEGRATION_TOKEN": "token",
        "MAIL_CENTER_DOMAIN_STRATEGY": "random",
        "MAIL_CENTER_DOMAIN": "@AIID.QZZ.IO",
        "MAIL_CENTER_PREFIX": "AAR",
        "MAIL_CENTER_PROVIDER_DEFAULT": "false",
    })
    assert payload is not None
    assert payload["is_default"] is False
    assert payload["config"]["mail_center_domain"] == "aiid.qzz.io"
    assert payload["config"]["mail_center_prefix"] == "aar"

    with pytest.raises(ValueError, match="MAIL_CENTER_DOMAIN_STRATEGY"):
        mail_center_pool_payload({
            "MAIL_CENTER_INTEGRATION_TOKEN": "token",
            "MAIL_CENTER_DOMAIN_STRATEGY": "unsupported",
        })
