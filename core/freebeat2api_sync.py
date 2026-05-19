"""Sync Freebeat accounts to a freebeat2api instance."""
from __future__ import annotations

import logging
from typing import Any

import requests


logger = logging.getLogger(__name__)


class Freebeat2ApiClient:
    def __init__(self, base_url: str, api_key: str = "", *, timeout: int = 15):
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.timeout = int(timeout or 15)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-API-Key"] = self.api_key
        return headers

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}{path}",
            json=body or {},
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"data": data}

    def _get(self, path: str) -> Any:
        response = requests.get(
            f"{self.base_url}{path}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def upsert_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/accounts", payload)

    def list_accounts(self) -> list[dict[str, Any]]:
        data = self._get("/api/accounts")
        return data if isinstance(data, list) else []

    def heartbeat(self, account_id: int) -> dict[str, Any]:
        return self._post(f"/api/accounts/{int(account_id)}/heartbeat")

    def refresh_balance(self, account_id: int) -> dict[str, Any]:
        data = self._get(f"/api/accounts/{int(account_id)}/balance")
        return data if isinstance(data, dict) else {"data": data}

    def sign_in(self, account_id: int) -> dict[str, Any]:
        return self._post(f"/api/accounts/{int(account_id)}/sign-in")

    def check_account(self, account_id: int) -> dict[str, Any]:
        return self._post(f"/api/accounts/{int(account_id)}/check")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


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


def _merged_extra(account: Any, extra_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    base_extra = dict(getattr(account, "extra", {}) or {})
    overview = base_extra.get("account_overview") if isinstance(base_extra.get("account_overview"), dict) else {}
    legacy_extra = overview.get("legacy_extra") if isinstance(overview.get("legacy_extra"), dict) else {}
    extra = dict(legacy_extra)
    extra.update({key: value for key, value in overview.items() if key != "legacy_extra"})
    extra.update(base_extra)
    if extra_overrides:
        extra.update(extra_overrides)
    return extra


def _get_freebeat2api_config() -> tuple[str, str, int, bool]:
    try:
        from core.config_store import config_store

        base_url = config_store.get("freebeat2api_url", "")
        api_key = config_store.get("freebeat2api_api_key", "")
        max_concurrency = _clamp_concurrency(config_store.get("freebeat2api_max_concurrency", "1"))
        auto_maintenance = _as_bool(config_store.get("freebeat2api_enable_auto_maintenance", ""), True)
        return base_url, api_key, max_concurrency, auto_maintenance
    except Exception:
        return "", "", 1, True


def is_freebeat2api_configured() -> bool:
    base_url, _, _, _ = _get_freebeat2api_config()
    return bool(_text(base_url))


def build_freebeat2api_payload(
    account: Any,
    *,
    max_concurrency: int = 1,
    auto_maintenance_default: bool = True,
    extra_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extra = _merged_extra(account, extra_overrides)
    token = _first_text(
        extra.get("access_token"),
        extra.get("accessToken"),
        extra.get("token"),
        getattr(account, "token", ""),
        extra.get("legacy_token"),
        extra.get("device_token"),
        extra.get("deviceToken"),
    )
    if not token:
        raise ValueError("Freebeat account is missing token; relogin before syncing to freebeat2api")

    email = _first_text(extra.get("email"), getattr(account, "email", ""))
    user_id = _first_text(
        extra.get("user_id"),
        extra.get("userId"),
        extra.get("account_id"),
        getattr(account, "user_id", ""),
    )
    name = _first_text(extra.get("freebeat2api_name"), email, user_id)
    if not name:
        raise ValueError("Freebeat account has no usable freebeat2api account name")

    return {
        "name": name[:80],
        "token": token,
        "email": email,
        "user_id": user_id,
        "user_agent": _text(extra.get("user_agent")),
        "sec_ch_ua": _text(extra.get("sec_ch_ua")),
        "sec_ch_ua_platform": _text(extra.get("sec_ch_ua_platform")),
        "proxy_url": _text(extra.get("freebeat2api_proxy_url") or extra.get("proxy_url") or extra.get("proxy")),
        "enabled": _as_bool(extra.get("freebeat2api_enabled"), True),
        "enable_auto_maintenance": _as_bool(
            extra.get("freebeat2api_enable_auto_maintenance"),
            auto_maintenance_default,
        ),
        "max_concurrency": _clamp_concurrency(extra.get("freebeat2api_max_concurrency"), max_concurrency),
    }


def sync_account_to_freebeat2api(
    account: Any,
    *,
    log_fn=None,
    heartbeat: bool = False,
    balance: bool = False,
    sign_in: bool = False,
    check: bool = False,
    extra_overrides: dict[str, Any] | None = None,
) -> dict[str, Any] | bool:
    log = log_fn or logger.info
    base_url, api_key, max_concurrency, auto_maintenance_default = _get_freebeat2api_config()
    if not base_url:
        return False

    try:
        payload = build_freebeat2api_payload(
            account,
            max_concurrency=max_concurrency,
            auto_maintenance_default=auto_maintenance_default,
            extra_overrides=extra_overrides,
        )
        client = Freebeat2ApiClient(base_url, api_key)
        account_result = client.upsert_account(payload)
        account_id = int(account_result.get("id") or account_result.get("account_id") or 0)
        heartbeat_result = (
            _optional_freebeat2api_call(log, "heartbeat", lambda: client.heartbeat(account_id))
            if heartbeat and account_id > 0
            else None
        )
        balance_result = (
            _optional_freebeat2api_call(log, "balance", lambda: client.refresh_balance(account_id))
            if balance and account_id > 0
            else None
        )
        sign_in_result = (
            _optional_freebeat2api_call(log, "sign-in", lambda: client.sign_in(account_id))
            if sign_in and account_id > 0
            else None
        )
        check_result = (
            _optional_freebeat2api_call(log, "check", lambda: client.check_account(account_id))
            if check and account_id > 0
            else None
        )
        log(f"  [Freebeat2API] synced Freebeat account: {payload['name']}")
        return {
            "ok": True,
            "account": account_result,
            "heartbeat": heartbeat_result,
            "balance": balance_result,
            "sign_in": sign_in_result,
            "check": check_result,
            "payload": {
                **payload,
                "token": "***",
            },
        }
    except Exception as exc:
        log(f"  [Freebeat2API] sync failed: {exc}")
        return False


def _optional_freebeat2api_call(log_fn, label: str, call) -> dict[str, Any]:
    try:
        data = call()
        return data if isinstance(data, dict) else {"data": data}
    except Exception as exc:
        error = str(exc)
        log_fn(f"  [Freebeat2API] {label} refresh failed after account sync: {error}")
        return {"ok": False, "error": error}


def get_freebeat2api_account_snapshot(account: Any, *, log_fn=None) -> dict[str, Any] | None:
    log = log_fn or logger.info
    base_url, api_key, max_concurrency, auto_maintenance_default = _get_freebeat2api_config()
    if not base_url:
        return None
    try:
        payload = build_freebeat2api_payload(
            account,
            max_concurrency=max_concurrency,
            auto_maintenance_default=auto_maintenance_default,
        )
        expected_name = _text(payload.get("name"))
        expected_email = _text(payload.get("email"))
        expected_user_id = _text(payload.get("user_id"))
        client = Freebeat2ApiClient(base_url, api_key)
        for item in client.list_accounts():
            if not isinstance(item, dict):
                continue
            if expected_name and _text(item.get("name")) == expected_name:
                return item
            if expected_email and _text(item.get("email")) == expected_email:
                return item
            if expected_user_id and _text(item.get("user_id")) == expected_user_id:
                return item
    except Exception as exc:
        log(f"  [Freebeat2API] account snapshot failed: {exc}")
    return None
