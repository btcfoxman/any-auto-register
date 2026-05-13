"""Sync LingYaQQ accounts to a lingya2api instance."""
from __future__ import annotations

import logging
from typing import Any

import requests

from platforms.lingya_qq.cookies import (
    build_lingya_qq_account_fields,
    format_lingya_qq_cookie_header,
)
from platforms.lingya_qq.core import DEFAULT_SEC_CH_UA, DEFAULT_SEC_CH_UA_PLATFORM, DEFAULT_USER_AGENT


logger = logging.getLogger(__name__)


class Lingya2ApiClient:
    def __init__(self, base_url: str, api_key: str = "", *, timeout: int = 15):
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.timeout = int(timeout or 15)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
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


def _get_lingya2api_config() -> tuple[str, str, int]:
    try:
        from core.config_store import config_store

        base_url = config_store.get("lingya2api_url", "")
        api_key = config_store.get("lingya2api_api_key", "")
        max_concurrency = _clamp_concurrency(config_store.get("lingya2api_max_concurrency", "1"))
        return base_url, api_key, max_concurrency
    except Exception:
        return "", "", 1


def build_lingya2api_payload(
    account: Any,
    *,
    max_concurrency: int = 1,
    extra_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_extra = dict(getattr(account, "extra", {}) or {})
    overview = base_extra.get("account_overview") if isinstance(base_extra.get("account_overview"), dict) else {}
    legacy_extra = overview.get("legacy_extra") if isinstance(overview.get("legacy_extra"), dict) else {}
    extra = dict(legacy_extra)
    extra.update({key: value for key, value in overview.items() if key != "legacy_extra"})
    extra.update(base_extra)
    if extra_overrides:
        extra.update(extra_overrides)
    cookie_fields = build_lingya_qq_account_fields(extra)

    vdevice_guid = _first_text(
        cookie_fields.get("vdevice_guid"),
        extra.get("vdevice_guid"),
        extra.get("device_guid"),
        extra.get("video_guid"),
    )
    if not vdevice_guid:
        raise ValueError(
            "LingYaQQ account is missing vdevice_guid; relogin or import cookies before syncing to lingya2api"
        )
    cookie_fields["vdevice_guid"] = vdevice_guid

    vuserid = _first_text(
        cookie_fields.get("vuid"),
        cookie_fields.get("v_vuserid"),
        cookie_fields.get("vuserid"),
        cookie_fields.get("vqq_vuserid"),
        extra.get("vuid"),
        getattr(account, "user_id", ""),
    )
    vusession = _first_text(
        cookie_fields.get("vusession"),
        cookie_fields.get("v_vusession"),
        cookie_fields.get("vqq_vusession"),
        extra.get("vusession"),
        getattr(account, "token", ""),
    )
    vurefresh = _first_text(cookie_fields.get("vurefresh"), cookie_fields.get("v_vurefresh"), extra.get("vurefresh"))
    if vuserid:
        cookie_fields.setdefault("v_vuserid", vuserid)
        cookie_fields.setdefault("vuserid", vuserid)
        cookie_fields.setdefault("vqq_vuserid", vuserid)
    if vusession:
        cookie_fields.setdefault("v_vusession", vusession)
        cookie_fields.setdefault("vusession", vusession)
        cookie_fields.setdefault("vqq_vusession", vusession)
    if vurefresh:
        cookie_fields.setdefault("v_vurefresh", vurefresh)

    cookie_header = format_lingya_qq_cookie_header(cookie_fields) or _text(cookie_fields.get("cookies"))
    if not cookie_header:
        raise ValueError("LingYaQQ account has no usable cookie header")

    name = (
        _text(extra.get("lingya2api_name"))
        or _text(getattr(account, "email", ""))
        or vuserid
        or vdevice_guid
    )
    if not name:
        raise ValueError("LingYaQQ account has no usable lingya2api account name")

    return {
        "name": name[:80],
        "cookie": cookie_header,
        "vuserid": vuserid,
        "vdevice_guid": vdevice_guid,
        "nick": _text(cookie_fields.get("nick") or extra.get("nick")),
        "main_login": _text(cookie_fields.get("v_main_login") or extra.get("main_login")) or "phone",
        "user_agent": _text(extra.get("user_agent")) or DEFAULT_USER_AGENT,
        "sec_ch_ua": _text(extra.get("sec_ch_ua")) or DEFAULT_SEC_CH_UA,
        "sec_ch_ua_platform": _text(extra.get("sec_ch_ua_platform")) or DEFAULT_SEC_CH_UA_PLATFORM,
        "proxy_url": _text(extra.get("proxy_url") or extra.get("proxy") or extra.get("proxyUrl")),
        "enabled": _as_bool(extra.get("lingya2api_enabled"), True),
        "enable_auto_maintenance": _as_bool(extra.get("lingya2api_enable_auto_maintenance"), False),
        "max_concurrency": _clamp_concurrency(extra.get("lingya2api_max_concurrency"), max_concurrency),
    }


def sync_account_to_lingya2api(
    account: Any,
    *,
    log_fn=None,
    heartbeat: bool = False,
    check: bool = False,
    extra_overrides: dict[str, Any] | None = None,
) -> dict[str, Any] | bool:
    log = log_fn or logger.info
    base_url, api_key, max_concurrency = _get_lingya2api_config()
    if not base_url:
        return False

    try:
        payload = build_lingya2api_payload(
            account,
            max_concurrency=max_concurrency,
            extra_overrides=extra_overrides,
        )
        client = Lingya2ApiClient(base_url, api_key)
        account_result = client.upsert_account(payload)
        account_id = int(account_result.get("id") or account_result.get("account_id") or 0)
        heartbeat_result = client.heartbeat(account_id) if heartbeat and account_id > 0 else None
        check_result = client.check_account(account_id) if check and account_id > 0 else None
        log(f"  [Lingya2API] synced LingYaQQ account: {payload['name']}")
        return {
            "ok": True,
            "account": account_result,
            "heartbeat": heartbeat_result,
            "check": check_result,
            "payload": {
                **payload,
                "cookie": "***",
            },
        }
    except Exception as exc:
        log(f"  [Lingya2API] sync failed: {exc}")
        return False


def get_lingya2api_account_snapshot(account: Any, *, log_fn=None) -> dict[str, Any] | None:
    log = log_fn or logger.info
    base_url, api_key, max_concurrency = _get_lingya2api_config()
    if not base_url:
        return None
    try:
        payload = build_lingya2api_payload(account, max_concurrency=max_concurrency)
        expected_name = _text(payload.get("name"))
        expected_vuserid = _text(payload.get("vuserid"))
        expected_device = _text(payload.get("vdevice_guid"))
        client = Lingya2ApiClient(base_url, api_key)
        for item in client.list_accounts():
            if not isinstance(item, dict):
                continue
            if expected_name and _text(item.get("name")) == expected_name:
                return item
            if expected_vuserid and _text(item.get("vuserid")) == expected_vuserid:
                return item
            if expected_device and _text(item.get("vdevice_guid")) == expected_device:
                return item
    except Exception as exc:
        log(f"  [Lingya2API] account snapshot failed: {exc}")
    return None
