from __future__ import annotations

import os
from collections.abc import Mapping

from infrastructure.provider_settings_repository import ProviderSettingsRepository


_MAIL_CENTER_STRATEGIES = {"round_robin", "random", "least_used"}


def mail_center_pool_payload(environ: Mapping[str, str]) -> dict | None:
    """Build a provider setting only when a deployment token is present."""
    token = str(environ.get("MAIL_CENTER_INTEGRATION_TOKEN") or "").strip()
    if not token:
        return None

    strategy = str(environ.get("MAIL_CENTER_DOMAIN_STRATEGY") or "least_used").strip().lower()
    if strategy not in _MAIL_CENTER_STRATEGIES:
        raise ValueError(f"invalid MAIL_CENTER_DOMAIN_STRATEGY: {strategy}")

    default_value = str(environ.get("MAIL_CENTER_PROVIDER_DEFAULT") or "true").strip().lower()
    is_default = default_value not in {"0", "false", "no", "off"}
    return {
        "provider_type": "mailbox",
        "provider_key": "mail_center_pool_api",
        "display_name": "Mail Center 动态多域邮箱池",
        "auth_mode": "token",
        "enabled": True,
        "is_default": is_default,
        "config": {
            "mail_center_api_url": str(
                environ.get("MAIL_CENTER_API_URL") or "https://mail-center.aiid.qzz.io"
            ).strip().rstrip("/"),
            "mail_center_domain_strategy": strategy,
            "mail_center_domain": str(environ.get("MAIL_CENTER_DOMAIN") or "").strip().lstrip("@").lower(),
            "mail_center_prefix": str(environ.get("MAIL_CENTER_PREFIX") or "").strip().lower(),
        },
        "auth": {"mail_center_integration_token": token},
        "metadata": {"managed_by": "deployment_environment"},
    }


def configure_mail_center_pool_from_env(
    environ: Mapping[str, str] | None = None,
    repository: ProviderSettingsRepository | None = None,
) -> bool:
    payload = mail_center_pool_payload(environ or os.environ)
    if payload is None:
        return False
    repo = repository or ProviderSettingsRepository()
    repo.save(setting_id=None, **payload)
    return True
