"""Higgsfield Clerk authentication and FNF account-state client."""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests


HIGG_APP_URL = "https://higgsfield.ai/academy/courses/cinema-studio-pro/set-up-your-project"
HIGG_REFERER = "https://higgsfield.ai/"
CLERK_BASE_URL = "https://clerk.higgsfield.ai/v1/client"
FNF_BASE_URL = "https://fnf-api-gw.higgsfield.ai/fnf"
CLERK_API_VERSION = "2026-05-12"
CLERK_JS_VERSION = "6.25.10"
CLERK_TURNSTILE_SITE_KEY = "0x4AAAAAAAFV93qQdS0ycilX"
HIGG_SURFACE = "academy:cinema-studio-pro"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
DEFAULT_SEC_CH_UA = '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"'
DEFAULT_SEC_CH_UA_PLATFORM = '"Windows"'


class HiggError(RuntimeError):
    """Base Higgsfield protocol error."""


class HiggRiskBlocked(HiggError):
    """DataDome or Cloudflare rejected the request."""


@dataclass(slots=True)
class HiggAuthState:
    email: str
    user_id: str
    session_id: str
    token: str
    workspace_id: str
    cookie_header: str
    datadome: str


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_error(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            return _text(first.get("long_message") or first.get("message") or first.get("code")) or fallback
        detail = payload.get("detail")
        if isinstance(detail, dict):
            return _text(detail.get("message") or detail.get("error_type")) or json.dumps(detail, ensure_ascii=False)
        return _text(payload.get("message") or payload.get("error")) or fallback
    return fallback


def decode_jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = _text(token).split(".")[1]
        payload += "=" * (-len(payload) % 4)
        value = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def cookie_header_from_any(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("[", "{")):
            try:
                return cookie_header_from_any(json.loads(text))
            except Exception:
                return text
        return text
    if isinstance(value, dict):
        if "name" in value and "value" in value:
            items = [(value.get("name"), value.get("value"))]
        else:
            items = value.items()
    elif isinstance(value, list):
        items = (
            (item.get("name"), item.get("value"))
            for item in value
            if isinstance(item, dict)
        )
    else:
        return ""
    pairs: list[str] = []
    for name, cookie_value in items:
        name_text = _text(name)
        value_text = _text(cookie_value)
        if not name_text or not value_text:
            continue
        if any(ch in name_text for ch in ";\r\n\t ") or any(ch in value_text for ch in ";\r\n"):
            continue
        pairs.append(f"{name_text}={value_text}")
    return "; ".join(dict.fromkeys(pairs))


def cookie_value(cookie_header: str, name: str) -> str:
    for part in cookie_header_from_any(cookie_header).split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name:
            return value.strip()
    return ""


def session_cookie_value(session: requests.Session, name: str) -> str:
    return next(
        (
            str(cookie.value or "")
            for cookie in session.cookies
            if cookie.name == name and cookie.value
        ),
        "",
    )


def extract_higg_account_context(account: Any) -> dict[str, Any]:
    extra = dict(getattr(account, "extra", {}) or {})
    overview = extra.get("account_overview") if isinstance(extra.get("account_overview"), dict) else {}
    legacy = overview.get("legacy_extra") if isinstance(overview.get("legacy_extra"), dict) else {}
    merged = dict(legacy)
    merged.update({key: value for key, value in overview.items() if key != "legacy_extra"})
    merged.update(extra)
    token = _text(
        merged.get("clerk_jwt")
        or merged.get("token")
        or getattr(account, "token", "")
    )
    cookies = cookie_header_from_any(
        merged.get("cookies")
        or merged.get("cookie_header")
        or merged.get("higg_cookies")
    )
    return {
        **merged,
        "email": _text(merged.get("email") or getattr(account, "email", "")),
        "user_id": _text(merged.get("user_id") or getattr(account, "user_id", "")),
        "session_id": _text(merged.get("session_id") or decode_jwt_claims(token).get("sid")),
        "token": token,
        "workspace_id": _text(merged.get("workspace_id") or decode_jwt_claims(token).get("workspace_id")),
        "cookies": cookies,
        "datadome": _text(merged.get("datadome") or cookie_value(cookies, "datadome")),
        "user_agent": _text(merged.get("user_agent")) or DEFAULT_USER_AGENT,
        "sec_ch_ua": _text(merged.get("sec_ch_ua")) or DEFAULT_SEC_CH_UA,
        "sec_ch_ua_platform": _text(merged.get("sec_ch_ua_platform")) or DEFAULT_SEC_CH_UA_PLATFORM,
        "proxy_url": _text(
            merged.get("higg2api_proxy_url")
            or merged.get("proxy_url")
            or merged.get("resolved_proxy")
            or merged.get("proxy")
        ),
    }


class HiggClient:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        token: str = "",
        session_id: str = "",
        cookies: Any = None,
        datadome: str = "",
        workspace_id: str = "",
        user_agent: str = "",
        sec_ch_ua: str = "",
        sec_ch_ua_platform: str = "",
        timeout: int = 30,
        log_fn=None,
    ):
        self.proxy = _text(proxy)
        self.token = _text(token)
        self.session_id = _text(session_id) or _text(decode_jwt_claims(self.token).get("sid"))
        self.workspace_id = _text(workspace_id) or _text(decode_jwt_claims(self.token).get("workspace_id"))
        self.datadome = _text(datadome)
        self.user_agent = _text(user_agent) or DEFAULT_USER_AGENT
        self.sec_ch_ua = _text(sec_ch_ua) or DEFAULT_SEC_CH_UA
        self.sec_ch_ua_platform = _text(sec_ch_ua_platform) or DEFAULT_SEC_CH_UA_PLATFORM
        self.timeout = max(int(timeout or 30), 5)
        self.log = log_fn or (lambda _message: None)
        self.session = requests.Session()
        self.session.trust_env = False
        if self.proxy:
            self.session.proxies.update({"http": self.proxy, "https": self.proxy})
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "sec-ch-ua": self.sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": self.sec_ch_ua_platform,
                "Referer": HIGG_REFERER,
            }
        )
        self._load_cookies(cookies)
        if not self.datadome:
            self.datadome = session_cookie_value(self.session, "datadome")

    @classmethod
    def from_account(cls, account: Any, *, proxy: str | None = None, log_fn=None) -> "HiggClient":
        context = extract_higg_account_context(account)
        return cls(
            proxy=proxy or context.get("proxy_url"),
            token=context.get("token", ""),
            session_id=context.get("session_id", ""),
            cookies=context.get("cookies", ""),
            datadome=context.get("datadome", ""),
            workspace_id=context.get("workspace_id", ""),
            user_agent=context.get("user_agent", ""),
            sec_ch_ua=context.get("sec_ch_ua", ""),
            sec_ch_ua_platform=context.get("sec_ch_ua_platform", ""),
            log_fn=log_fn,
        )

    def _load_cookies(self, cookies: Any) -> None:
        header = cookie_header_from_any(cookies)
        for pair in header.split(";"):
            name, separator, value = pair.strip().partition("=")
            if separator and name and value:
                self.session.cookies.set(name, value)

    def cookie_header(self) -> str:
        return "; ".join(
            f"{cookie.name}={cookie.value}"
            for cookie in self.session.cookies
            if cookie.name and cookie.value
        )

    def _clerk_url(self, path: str) -> str:
        separator = "&" if "?" in path else "?"
        return (
            f"{CLERK_BASE_URL}{path}{separator}"
            f"__clerk_api_version={quote(CLERK_API_VERSION)}&_clerk_js_version={quote(CLERK_JS_VERSION)}"
        )

    def _clerk_post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            self._clerk_url(path),
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        try:
            payload = response.json()
        except Exception as exc:
            raise HiggError(f"Clerk returned non-JSON HTTP {response.status_code}") from exc
        if response.status_code >= 400:
            raise HiggError(_json_error(payload, f"Clerk HTTP {response.status_code}"))
        return payload if isinstance(payload, dict) else {}

    def create_signup(self, email: str, password: str, captcha_token: str) -> dict[str, Any]:
        return self._clerk_post(
            "/sign_ups",
            {
                "email_address": email,
                "password": password,
                "unsafe_metadata": json.dumps(
                    {
                        "abTests": {
                            "multi-step-mobile-checkout": "control",
                            "pricing-pro-hide": "hide",
                        }
                    },
                    separators=(",", ":"),
                ),
                "locale": "zh-HK",
                "captcha_token": captcha_token,
                "captcha_widget_type": "invisible",
            },
        )

    def prepare_email_verification(self, signup_id: str) -> dict[str, Any]:
        return self._clerk_post(
            f"/sign_ups/{signup_id}/prepare_verification",
            {"strategy": "email_code"},
        )

    def attempt_email_verification(self, signup_id: str, code: str) -> dict[str, Any]:
        payload = self._clerk_post(
            f"/sign_ups/{signup_id}/attempt_verification",
            {"code": code, "strategy": "email_code"},
        )
        response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        client = payload.get("client") if isinstance(payload.get("client"), dict) else {}
        session_id = _text(response.get("created_session_id") or client.get("last_active_session_id"))
        sessions = client.get("sessions") if isinstance(client.get("sessions"), list) else []
        session = next(
            (item for item in sessions if isinstance(item, dict) and _text(item.get("id")) == session_id),
            sessions[0] if sessions and isinstance(sessions[0], dict) else {},
        )
        token_data = session.get("last_active_token") if isinstance(session.get("last_active_token"), dict) else {}
        self.session_id = session_id or _text(session.get("id"))
        self.token = _text(token_data.get("jwt"))
        if not self.session_id or not self.token:
            raise HiggError("Clerk verification response is missing session_id or JWT")
        return payload

    def touch_session(self) -> dict[str, Any]:
        if not self.session_id:
            raise HiggError("Missing Clerk session_id")
        payload = self._clerk_post(
            f"/sessions/{self.session_id}/touch",
            {"active_organization_id": "", "intent": "select_session"},
        )
        response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        token_data = response.get("last_active_token") if isinstance(response.get("last_active_token"), dict) else {}
        self.token = _text(token_data.get("jwt")) or self.token
        return payload

    def refresh_token(self) -> str:
        if not self.session_id:
            self.session_id = _text(decode_jwt_claims(self.token).get("sid"))
        if not self.session_id or not self.token:
            raise HiggError("Missing Clerk session_id or JWT")
        payload = self._clerk_post(
            f"/sessions/{self.session_id}/tokens",
            {
                "tab_state": "focused",
                "token": self.token,
            },
        )
        token = _text(payload.get("jwt"))
        if not token:
            raise HiggError("Clerk token refresh returned no JWT")
        self.token = token
        self.workspace_id = _text(decode_jwt_claims(token).get("workspace_id")) or self.workspace_id
        return token

    def _fnf_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Origin": "https://higgsfield.ai",
            "Referer": HIGG_REFERER,
            "hf-surface": HIGG_SURFACE,
        }
        datadome = self.datadome or session_cookie_value(self.session, "datadome")
        if datadome:
            headers["x-datadome-clientid"] = datadome
        return headers

    def fnf_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> Any:
        if not self.token:
            raise HiggError("Missing Clerk JWT")
        response = self.session.request(
            method,
            f"{FNF_BASE_URL}{path}",
            json=json_body,
            headers=self._fnf_headers(),
            timeout=self.timeout,
        )
        if response.status_code == 401 and retry_auth:
            self.refresh_token()
            return self.fnf_request(method, path, json_body=json_body, retry_auth=False)
        content_type = _text(response.headers.get("content-type")).lower()
        if response.status_code == 403 and "json" not in content_type:
            raise HiggRiskBlocked("Higgsfield generation context was blocked by DataDome or Cloudflare")
        try:
            payload = response.json()
        except Exception as exc:
            raise HiggError(f"Higgsfield returned non-JSON HTTP {response.status_code}") from exc
        if response.status_code >= 400:
            raise HiggError(_json_error(payload, f"Higgsfield HTTP {response.status_code}"))
        return payload

    def ensure_workspace(self) -> str:
        claims = decode_jwt_claims(self.token)
        self.workspace_id = _text(claims.get("workspace_id")) or self.workspace_id
        if self.workspace_id:
            return self.workspace_id
        workspaces = self.fnf_request("GET", "/workspaces")
        items = workspaces if isinstance(workspaces, list) else []
        workspace = next((item for item in items if isinstance(item, dict) and item.get("id")), None)
        if not workspace:
            raise HiggError("Higgsfield account has no workspace")
        self.workspace_id = _text(workspace.get("id"))
        self.fnf_request("POST", "/workspaces/context", json_body={"workspace_id": self.workspace_id})
        self.refresh_token()
        return self.workspace_id

    def get_wallet(self) -> dict[str, Any]:
        value = self.fnf_request("GET", "/workspaces/wallet")
        return value if isinstance(value, dict) else {}

    def get_free_generations(self) -> dict[str, Any]:
        value = self.fnf_request(
            "GET",
            "/user/free-gens/v2?surfaces=academy%3Acinema-studio-pro",
        )
        return value if isinstance(value, dict) else {}

    def fetch_account_state(self) -> dict[str, Any]:
        self.refresh_token()
        self.ensure_workspace()
        wallet = self.get_wallet()
        free_gens = self.get_free_generations()
        surface_items = free_gens.get("surface_items") if isinstance(free_gens.get("surface_items"), dict) else {}
        cinema_items = surface_items.get(HIGG_SURFACE) if isinstance(surface_items.get(HIGG_SURFACE), list) else []
        seedance = next(
            (
                item
                for item in cinema_items
                if isinstance(item, dict) and _text(item.get("job_set_type")) == "seedance_2_0"
            ),
            {},
        )
        claims = decode_jwt_claims(self.token)
        balance = wallet.get("credits_balance")
        free_count = seedance.get("counter")
        return {
            "valid": bool(self.token and self.session_id),
            "generation_ready": True,
            "email": _text(claims.get("email")),
            "user_id": _text(claims.get("sub")),
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "clerk_jwt": self.token,
            "token": self.token,
            "cookies": self.cookie_header(),
            "cookie_header": self.cookie_header(),
            "datadome": self.datadome or session_cookie_value(self.session, "datadome"),
            "user_agent": self.user_agent,
            "sec_ch_ua": self.sec_ch_ua,
            "sec_ch_ua_platform": self.sec_ch_ua_platform,
            "credits_balance": balance,
            "total_credits": wallet.get("total_credits"),
            "free_generations": free_count,
            "wallet": wallet,
            "free_gens": free_gens,
            "checked_at": int(time.time()),
            "chips": [
                f"Credits {balance if balance is not None else '-'}",
                f"Free gens {free_count if free_count is not None else '-'}",
            ],
        }

    def auth_state(self, *, email: str = "", user_id: str = "") -> HiggAuthState:
        claims = decode_jwt_claims(self.token)
        return HiggAuthState(
            email=_text(email or claims.get("email")),
            user_id=_text(user_id or claims.get("sub")),
            session_id=self.session_id or _text(claims.get("sid")),
            token=self.token,
            workspace_id=self.workspace_id or _text(claims.get("workspace_id")),
            cookie_header=self.cookie_header(),
            datadome=self.datadome or session_cookie_value(self.session, "datadome"),
        )
