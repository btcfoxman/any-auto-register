"""Sync QuickFrame accounts to a quickframe2api instance."""
from __future__ import annotations

import json
import logging
from typing import Any

import requests


logger = logging.getLogger(__name__)

QUICKFRAME2API_DEFAULT_API_KEY = "sk-test-api-key"


class QuickFrame2ApiAuthError(RuntimeError):
    pass


class QuickFrame2ApiClient:
    def __init__(self, base_url: str, api_key: str = "", *, timeout: int = 15):
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.timeout = int(timeout or 15)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-API-Key"] = self.api_key
            headers["X-Admin-Token"] = self.api_key
        return headers

    def _raise_for_status(self, response: requests.Response, path: str) -> None:
        if response.status_code == 401:
            key_state = "configured" if self.api_key else "missing"
            raise QuickFrame2ApiAuthError(
                f"HTTP 401 Unauthorized for {self.base_url}{path}; "
                f"quickframe2api_api_key is {key_state} and must match the QuickFrame2API service key"
            )
        response.raise_for_status()

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}{path}",
            json=body or {},
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._raise_for_status(response, path)
        data = response.json()
        return data if isinstance(data, dict) else {"data": data}

    def _get(self, path: str) -> Any:
        response = requests.get(
            f"{self.base_url}{path}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._raise_for_status(response, path)
        return response.json()

    def upsert_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/accounts", payload)

    def list_accounts(self) -> list[dict[str, Any]]:
        data = self._get("/api/accounts")
        return data if isinstance(data, list) else []

    def heartbeat(self, account_id: int) -> dict[str, Any]:
        return self._post(f"/api/accounts/{int(account_id)}/heartbeat")

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


def _cookie_header_from_any(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text.startswith(("[", "{")):
            try:
                return _cookie_header_from_any(json.loads(text))
            except Exception:
                return text
        return text
    pairs: list[str] = []
    if isinstance(value, dict):
        if "name" in value and "value" in value:
            name = _text(value.get("name"))
            cookie_value = _text(value.get("value"))
            return f"{name}={cookie_value}" if name and cookie_value else ""
        items = value.items()
    elif isinstance(value, list):
        items = ((item.get("name"), item.get("value")) for item in value if isinstance(item, dict))
    else:
        return ""
    for name, cookie_value in items:
        name_text = _text(name)
        value_text = _text(cookie_value)
        if not name_text or not value_text:
            continue
        if any(ch in name_text for ch in ";\r\n\t ") or any(ch in value_text for ch in ";\r\n"):
            continue
        pairs.append(f"{name_text}={value_text}")
    return "; ".join(dict.fromkeys(pairs))


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


def _get_quickframe2api_config() -> tuple[str, str, int, bool]:
    try:
        from core.config_store import config_store

        base_url = _text(config_store.get("quickframe2api_url", ""))
        api_key = _text(config_store.get("quickframe2api_api_key", ""))
        if base_url and not api_key:
            api_key = QUICKFRAME2API_DEFAULT_API_KEY
        max_concurrency = _clamp_concurrency(config_store.get("quickframe2api_max_concurrency", "1"))
        auto_maintenance = _as_bool(config_store.get("quickframe2api_enable_auto_maintenance", ""), True)
        return base_url, api_key, max_concurrency, auto_maintenance
    except Exception:
        return "", "", 1, True


def is_quickframe2api_configured() -> bool:
    base_url, _, _, _ = _get_quickframe2api_config()
    return bool(_text(base_url))


def build_quickframe2api_payload(
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
        extra.get("quickframe_access_token"),
        extra.get("token"),
        getattr(account, "token", ""),
    )
    cookies = _cookie_header_from_any(
        extra.get("cookies")
        or extra.get("cookie_header")
        or extra.get("quickframe_cookies")
        or extra.get("quickframe_cookie_header")
    )
    if not token and not cookies:
        raise ValueError("QuickFrame account is missing token/cookies; relogin before syncing to quickframe2api")

    email = _first_text(extra.get("email"), getattr(account, "email", ""))
    user_id = _first_text(
        extra.get("user_id"),
        extra.get("userId"),
        extra.get("account_id"),
        getattr(account, "user_id", ""),
    )
    workspace_id = _first_text(extra.get("workspace_id"), extra.get("workspaceId"))
    name = _first_text(extra.get("quickframe2api_name"), email, user_id, workspace_id)
    if not name:
        raise ValueError("QuickFrame account has no usable quickframe2api account name")

    auto_maintenance_enabled = _as_bool(
        extra.get("quickframe2api_enable_auto_maintenance"),
        auto_maintenance_default,
    )
    if _as_bool(extra.get("quickframe_keepalive_disabled"), False) or _as_bool(extra.get("quickframe_retired"), False):
        auto_maintenance_enabled = False

    return {
        "name": name[:80],
        "token": token,
        "access_token": token,
        "email": email,
        "user_id": user_id,
        "workspace_id": workspace_id,
        "user_agent": _text(extra.get("user_agent")),
        "sec_ch_ua": _text(extra.get("sec_ch_ua")),
        "sec_ch_ua_platform": _text(extra.get("sec_ch_ua_platform")),
        "cookies": cookies,
        "cookie_header": cookies,
        "proxy_url": _text(
            extra.get("quickframe2api_proxy_url")
            or extra.get("proxy_url")
            or extra.get("proxyUrl")
            or extra.get("resolved_proxy")
            or extra.get("proxy")
        ),
        "enabled": _as_bool(extra.get("quickframe2api_enabled"), True),
        "enable_auto_maintenance": auto_maintenance_enabled,
        "max_concurrency": _clamp_concurrency(extra.get("quickframe2api_max_concurrency"), max_concurrency),
        "session_id": _text(extra.get("session_id")),
        "session_status": _text(extra.get("session_status")),
        "free_exports_remaining": extra.get("free_exports_remaining"),
        "has_active_subscription": bool(extra.get("has_active_subscription")),
        "token_expires_at": _text(extra.get("token_expires_at")),
    }


def sync_account_to_quickframe2api(
    account: Any,
    *,
    log_fn=None,
    heartbeat: bool = False,
    check: bool = False,
    extra_overrides: dict[str, Any] | None = None,
) -> dict[str, Any] | bool:
    log = log_fn or logger.info
    base_url, api_key, max_concurrency, auto_maintenance_default = _get_quickframe2api_config()
    if not base_url:
        return False

    try:
        payload = build_quickframe2api_payload(
            account,
            max_concurrency=max_concurrency,
            auto_maintenance_default=auto_maintenance_default,
            extra_overrides=extra_overrides,
        )
        client = QuickFrame2ApiClient(base_url, api_key)
        account_result = client.upsert_account(payload)
        account_id = int(account_result.get("id") or account_result.get("account_id") or 0)
        heartbeat_result = (
            _optional_quickframe2api_call(log, "heartbeat", lambda: client.heartbeat(account_id))
            if heartbeat and account_id > 0
            else None
        )
        check_result = (
            _optional_quickframe2api_call(log, "check", lambda: client.check_account(account_id))
            if check and account_id > 0
            else None
        )
        log(f"  [QuickFrame2API] synced QuickFrame account: {payload['name']}")
        return {
            "ok": True,
            "account": account_result,
            "heartbeat": heartbeat_result,
            "check": check_result,
            "payload": {
                **payload,
                "token": "***" if payload.get("token") else "",
                "access_token": "***" if payload.get("access_token") else "",
                "cookies": "***" if payload.get("cookies") else "",
                "cookie_header": "***" if payload.get("cookie_header") else "",
            },
        }
    except Exception as exc:
        log(f"  [QuickFrame2API] sync failed: {exc}")
        return False


def _optional_quickframe2api_call(log_fn, label: str, call) -> dict[str, Any]:
    try:
        data = call()
        return data if isinstance(data, dict) else {"data": data}
    except Exception as exc:
        error = str(exc)
        log_fn(f"  [QuickFrame2API] {label} failed after account sync: {error}")
        return {"ok": False, "error": error}
