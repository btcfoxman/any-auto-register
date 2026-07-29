"""Sync Higgsfield accounts to a higg2api instance."""
from __future__ import annotations

import logging
from typing import Any

import requests

from platforms.higg.core import cookie_header_from_any, decode_jwt_claims, extract_higg_account_context


logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_bool(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_concurrency(value: Any, default: int = 1) -> int:
    return min(max(_as_int(value, default), 1), 10)


class Higg2ApiClient:
    def __init__(self, base_url: str, api_key: str = "", *, timeout: int = 20):
        self.base_url = _text(base_url).rstrip("/")
        self.api_key = _text(api_key)
        self.timeout = max(int(timeout or 20), 5)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-API-Key"] = self.api_key
        return headers

    def _post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}{path}",
            json=payload or {},
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else {"data": value}

    def upsert_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/accounts", payload)

    def check_account(self, account_id: int) -> dict[str, Any]:
        return self._post(f"/api/accounts/{int(account_id)}/check")

    def refresh_balance(self, account_id: int) -> dict[str, Any]:
        response = requests.get(
            f"{self.base_url}/api/accounts/{int(account_id)}/balance",
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else {"data": value}


def _get_higg2api_config() -> tuple[str, str, int, bool]:
    try:
        from core.config_store import config_store

        return (
            config_store.get("higg2api_url", ""),
            config_store.get("higg2api_api_key", ""),
            _clamp_concurrency(config_store.get("higg2api_max_concurrency", "1")),
            _as_bool(config_store.get("higg2api_enable_auto_maintenance", ""), True),
        )
    except Exception:
        return "", "", 1, True


def is_higg2api_configured() -> bool:
    return bool(_text(_get_higg2api_config()[0]))


def build_higg2api_payload(
    account: Any,
    *,
    max_concurrency: int = 1,
    auto_maintenance_default: bool = True,
    extra_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = extract_higg_account_context(account)
    if extra_overrides:
        context.update(extra_overrides)
    token = _text(context.get("clerk_jwt") or context.get("token") or getattr(account, "token", ""))
    claims = decode_jwt_claims(token)
    session_id = _text(context.get("session_id") or claims.get("sid"))
    if not token or not session_id:
        raise ValueError("Higgsfield account is missing Clerk JWT/session_id")
    email = _text(context.get("email") or getattr(account, "email", "") or claims.get("email"))
    user_id = _text(context.get("user_id") or getattr(account, "user_id", "") or claims.get("sub"))
    name = _text(context.get("higg2api_name") or email or user_id)
    if not name:
        raise ValueError("Higgsfield account has no usable Higg2API account name")
    cookies = cookie_header_from_any(
        context.get("cookies")
        or context.get("cookie_header")
        or context.get("higg_cookies")
    )
    maintenance = _as_bool(
        context.get("higg2api_enable_auto_maintenance"),
        auto_maintenance_default,
    )
    if _as_bool(context.get("higg_keepalive_disabled"), False):
        maintenance = False
    payload = {
        "name": name[:120],
        "email": email,
        "user_id": user_id,
        "session_id": session_id,
        "token": token,
        "clerk_jwt": token,
        "cookies": cookies,
        "cookie_header": cookies,
        "datadome": _text(context.get("datadome")),
        "user_agent": _text(context.get("user_agent")),
        "sec_ch_ua": _text(context.get("sec_ch_ua")),
        "sec_ch_ua_platform": _text(context.get("sec_ch_ua_platform")),
        "proxy_url": _text(
            context.get("higg2api_proxy_url")
            or context.get("proxy_url")
            or context.get("resolved_proxy")
            or context.get("proxy")
        ),
        "workspace_id": _text(context.get("workspace_id") or claims.get("workspace_id")),
        "folder_id": _text(context.get("folder_id")),
        "enabled": _as_bool(context.get("higg2api_enabled"), True),
        "enable_auto_maintenance": maintenance,
        "max_concurrency": _clamp_concurrency(
            context.get("higg2api_max_concurrency"),
            max_concurrency,
        ),
    }
    balance = context.get("credits_balance")
    if balance in (None, ""):
        balance = context.get("total_credits")
    if balance not in (None, ""):
        payload["last_balance"] = balance
    if context.get("free_generations") not in (None, ""):
        payload["free_generations"] = context.get("free_generations")
    return payload


def sync_account_to_higg2api(
    account: Any,
    *,
    log_fn=None,
    check: bool = False,
    balance: bool = False,
    extra_overrides: dict[str, Any] | None = None,
) -> dict[str, Any] | bool:
    log = log_fn or logger.info
    base_url, api_key, max_concurrency, maintenance = _get_higg2api_config()
    if not _text(base_url):
        return False
    try:
        payload = build_higg2api_payload(
            account,
            max_concurrency=max_concurrency,
            auto_maintenance_default=maintenance,
            extra_overrides=extra_overrides,
        )
        client = Higg2ApiClient(base_url, api_key)
        account_result = client.upsert_account(payload)
        account_id = _as_int(account_result.get("id") or account_result.get("account_id"), 0)
        check_result = client.check_account(account_id) if check and account_id > 0 else None
        balance_result = client.refresh_balance(account_id) if balance and account_id > 0 else None
        log(f"  [Higg2API] synced Higgsfield account: {payload['name']}")
        return {
            "ok": True,
            "account": account_result,
            "check": check_result,
            "balance": balance_result,
            "payload": {
                **payload,
                "token": "***",
                "clerk_jwt": "***",
                "cookies": "***" if payload.get("cookies") else "",
                "cookie_header": "***" if payload.get("cookie_header") else "",
                "datadome": "***" if payload.get("datadome") else "",
            },
        }
    except Exception as exc:
        log(f"  [Higg2API] sync failed: {exc}")
        return False

