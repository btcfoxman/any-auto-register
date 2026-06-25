"""WeryAI protocol client for the ImgsWeryai account platform."""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Callable, get_args
from urllib.parse import urlparse

from curl_cffi.requests import Session

from core.base_platform import Account


WERYAI_API_BASE = "https://api-growth-agent.weryai.com/growthai"
WERYAI_WEB_BASE = "https://www.weryai.com"
WERYAI_DEFAULT_APP_KEY = "20006012"
WERYAI_DEFAULT_VER_CODE = "1.9.0"
WERYAI_DEFAULT_LANG = "en"
WERYAI_DEFAULT_CHANNEL = "official"
WERYAI_DEFAULT_IMPERSONATE = "chrome"
WERYAI_IMPERSONATE_FALLBACKS = ("chrome", "chrome136", "chrome133a", "chrome131", "chrome124", "chrome120", "chrome110")
WERYAI_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
WERYAI_DEFAULT_SEC_CH_UA = '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"'
WERYAI_DF_ID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


class WeryAIAuthError(RuntimeError):
    """Raised when WeryAI returns an auth or account-state error."""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def text(value: Any) -> str:
    return str(value or "").strip()


def first_text(*values: Any) -> str:
    for value in values:
        item = text(value)
        if item:
            return item
    return ""


def supported_weryai_impersonates() -> set[str]:
    try:
        from curl_cffi.requests import impersonate as curl_impersonate

        literal = getattr(curl_impersonate, "BrowserTypeLiteral", None)
        if literal is not None:
            supported = {str(item) for item in get_args(literal)}
            if supported:
                return supported
        browser_type = getattr(curl_impersonate, "BrowserType", None)
        if browser_type is not None:
            supported = {str(item.value) for item in browser_type}
            if supported:
                return supported
        target_map = getattr(curl_impersonate, "REAL_TARGET_MAP", None)
        if isinstance(target_map, dict):
            return {str(item) for item in target_map.keys()} | {str(item) for item in target_map.values()}
        return set()
    except Exception:
        return set()


def normalize_weryai_impersonate(value: Any) -> str:
    requested = text(value) or WERYAI_DEFAULT_IMPERSONATE
    supported = supported_weryai_impersonates()
    if supported and requested not in supported:
        for candidate in WERYAI_IMPERSONATE_FALLBACKS:
            if candidate in supported:
                return candidate
        return WERYAI_DEFAULT_IMPERSONATE
    return requested


def unsupported_impersonate_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "impersonating" in message and "not supported" in message


def weryai_impersonate_candidates(current: str) -> list[str]:
    candidates = [text(current) or WERYAI_DEFAULT_IMPERSONATE]
    supported = supported_weryai_impersonates()
    for candidate in WERYAI_IMPERSONATE_FALLBACKS:
        if candidate in candidates:
            continue
        if supported and candidate not in supported:
            continue
        candidates.append(candidate)
    return candidates


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).strip())
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def random_df_id(length: int = 48) -> str:
    return "".join(secrets.choice(WERYAI_DF_ID_ALPHABET) for _ in range(max(16, int(length or 48))))


def normalize_proxy_url(proxy: str | None) -> str | None:
    value = text(proxy)
    if not value:
        return None
    if value.lower().startswith("socks://"):
        return f"socks5://{value.split('://', 1)[1]}"
    return value


def client_ip_from_proxy(proxy: str | None) -> str:
    parsed = urlparse(text(proxy))
    host = parsed.hostname or ""
    parts = host.split(".")
    if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
        return host
    return ""


def valid_cookie_pair(name: Any, value: Any) -> tuple[str, str] | None:
    cookie_name = text(name)
    cookie_value = text(value)
    if not cookie_name or cookie_value == "":
        return None
    if any(ch in cookie_name for ch in ";\r\n\t "):
        return None
    if any(ch in cookie_value for ch in ";\r\n"):
        return None
    return cookie_name, cookie_value


def cookie_header_from_any(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""
        if raw.startswith(("[", "{")):
            try:
                return cookie_header_from_any(json.loads(raw))
            except Exception:
                return raw
        return raw
    pairs: list[str] = []
    if isinstance(value, dict):
        if "name" in value and "value" in value:
            pair = valid_cookie_pair(value.get("name"), value.get("value"))
            return f"{pair[0]}={pair[1]}" if pair else ""
        iterable = value.items()
    elif isinstance(value, list):
        iterable = ((item.get("name"), item.get("value")) for item in value if isinstance(item, dict))
    else:
        return ""
    for name, cookie_value in iterable:
        pair = valid_cookie_pair(name, cookie_value)
        if pair:
            pairs.append(f"{pair[0]}={pair[1]}")
    return "; ".join(dict.fromkeys(pairs))


def cookie_list_from_session(session: Session) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    try:
        for cookie in session.cookies.jar:
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "expires": cookie.expires,
                    "secure": bool(cookie.secure),
                    "httpOnly": bool(getattr(cookie, "has_nonstandard_attr", lambda _name: False)("HttpOnly")),
                }
            )
    except Exception:
        try:
            for name, value in session.cookies.get_dict().items():
                cookies.append({"name": name, "value": value})
        except Exception:
            return []
    return cookies


def response_json(response: Any) -> Any:
    raw = text(getattr(response, "text", ""))
    if not raw:
        return {}
    try:
        return response.json()
    except Exception:
        return json.loads(raw)


def raise_weryai_api_error(data: Any, label: str) -> None:
    if not isinstance(data, dict):
        return
    status = data.get("status")
    success = data.get("success")
    if status in (None, 200) and success is not False:
        return
    message = first_text(data.get("desc"), data.get("message"), data.get("msg"), data.get("error"), data)
    raise WeryAIAuthError(f"WeryAI {label} failed: {message}")


class WeryAIClient:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        log_fn: Callable[[str], None] = print,
        access_token: str = "",
        cookies: Any = None,
        cookie_header: str = "",
        df_id: str = "",
        client_ip: str = "",
        app_key: str = WERYAI_DEFAULT_APP_KEY,
        ver_code: str = WERYAI_DEFAULT_VER_CODE,
        lang: str = WERYAI_DEFAULT_LANG,
        channel: str = WERYAI_DEFAULT_CHANNEL,
        impersonate: str = WERYAI_DEFAULT_IMPERSONATE,
        user_agent: str = WERYAI_DEFAULT_USER_AGENT,
        sec_ch_ua: str = WERYAI_DEFAULT_SEC_CH_UA,
    ) -> None:
        self.proxy = normalize_proxy_url(proxy)
        self.log = log_fn
        self.access_token = text(access_token)
        self.df_id = text(df_id) or random_df_id()
        self.client_ip = text(client_ip) or client_ip_from_proxy(self.proxy)
        self.app_key = text(app_key) or WERYAI_DEFAULT_APP_KEY
        self.ver_code = text(ver_code) or WERYAI_DEFAULT_VER_CODE
        self.lang = text(lang) or WERYAI_DEFAULT_LANG
        self.channel = text(channel) or WERYAI_DEFAULT_CHANNEL
        requested_impersonate = text(impersonate) or WERYAI_DEFAULT_IMPERSONATE
        self.impersonate = normalize_weryai_impersonate(requested_impersonate)
        if self.impersonate != requested_impersonate:
            self.log(f"WeryAI impersonate {requested_impersonate} is not supported; using {self.impersonate}")
        self.user_agent = text(user_agent) or WERYAI_DEFAULT_USER_AGENT
        self.sec_ch_ua = text(sec_ch_ua) or WERYAI_DEFAULT_SEC_CH_UA
        self.proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        self.session = self._new_session()
        self._load_cookies(cookies or cookie_header)

    def _new_session(self) -> Session:
        return Session(impersonate=self.impersonate, proxies=self.proxies, timeout=30)

    def _load_cookies(self, cookies: Any) -> None:
        header = cookie_header_from_any(cookies)
        for item in header.split(";"):
            if "=" not in item:
                continue
            name, cookie_value = item.split("=", 1)
            pair = valid_cookie_pair(name.strip(), cookie_value.strip())
            if pair:
                try:
                    self.session.cookies.set(pair[0], pair[1], domain=".weryai.com", path="/")
                except Exception:
                    pass

    def common_params(self, *, team_id: Any = "", product_id: Any = "") -> dict[str, Any]:
        return {
            "app_key": self.app_key,
            "ver_code": self.ver_code,
            "lang": self.lang,
            "client_ip": self.client_ip,
            "df_id": self.df_id,
            "channel": self.channel,
            "fbclid": "",
            "msclkid": "",
            "bing_acc": "",
            "teamId": text(team_id),
            "productId": text(product_id),
        }

    def headers(self, *, auth: bool = False, json_content: bool = True) -> dict[str, str]:
        headers = {
            "accept": "application/json",
            "accept-language": self.lang,
            "referer": f"{WERYAI_WEB_BASE}/",
            "user-agent": self.user_agent,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
        if json_content:
            headers["content-type"] = "application/json"
        if auth and self.access_token:
            headers["authorization"] = self.access_token
        return headers

    def _url(self, path: str) -> str:
        return f"{WERYAI_API_BASE}{path}"

    def _reset_session_impersonate(self, impersonate: str) -> None:
        cookie_header = cookie_header_from_any(cookie_list_from_session(self.session))
        self.impersonate = impersonate
        self.session = self._new_session()
        self._load_cookies(cookie_header)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = False,
        label: str = "request",
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        request_timeout = timeout_seconds if timeout_seconds is not None else 30
        response = None
        last_error: Exception | None = None
        for impersonate in weryai_impersonate_candidates(self.impersonate):
            if impersonate != self.impersonate:
                self.log(f"WeryAI retry {label} with impersonate {impersonate}")
                self._reset_session_impersonate(impersonate)
            try:
                response = self.session.request(
                    method,
                    self._url(path),
                    params=params or {},
                    data=json_dumps(body) if body is not None else None,
                    headers=self.headers(auth=auth, json_content=True),
                    timeout=request_timeout,
                )
                break
            except Exception as exc:
                if not unsupported_impersonate_error(exc):
                    raise
                last_error = exc
                continue
        if response is None:
            raise last_error or RuntimeError(f"WeryAI {label} failed before receiving a response")
        response.raise_for_status()
        data = response_json(response)
        if isinstance(data, dict):
            raise_weryai_api_error(data, label)
            return data
        return {"data": data}

    def send_email_ticket(self, email: str) -> dict[str, Any]:
        body = {
            "email": text(email),
            "ticket_use_type": "REGISTER",
            "team_id": "",
            "product_id": "",
        }
        return self._request_json(
            "POST",
            "/api/v1/email/ticket",
            params=self.common_params(),
            body=body,
            label="email ticket",
        )

    def login_with_email_code(self, email: str, password: str, code: str) -> dict[str, Any]:
        body = {
            "email": text(email),
            "pwd": text(password),
            "ticket": text(code),
            "oauth_enum": "EMAIL_REGISTERED",
            "team_id": "",
            "product_id": "",
        }
        data = self._request_json(
            "POST",
            "/api/v1/login",
            params=self.common_params(),
            body=body,
            label="login",
        )
        login_data = data.get("data") if isinstance(data.get("data"), dict) else {}
        token = text(login_data.get("access_token"))
        if token:
            self.access_token = token
        return data

    def current_context(self, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/api/v1/auth/brands/current-context",
            params=self.common_params(),
            auth=True,
            label="current context",
            timeout_seconds=timeout_seconds,
        )

    def user_info(self, *, team_id: Any = "", product_id: Any = "", timeout_seconds: float | None = None) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/api/v1/auth/user/info",
            params=self.common_params(team_id=team_id, product_id=product_id),
            auth=True,
            label="user info",
            timeout_seconds=timeout_seconds,
        )

    def account_summary(
        self,
        *,
        team_id: Any = "",
        product_id: Any = "",
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/api/v1/auth/account/summary",
            params=self.common_params(team_id=team_id, product_id=product_id),
            auth=True,
            label="account summary",
            timeout_seconds=timeout_seconds,
        )

    def credits_balance(
        self,
        *,
        team_id: Any = "",
        product_id: Any = "",
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/api/v1/auth/teams/credits/balance",
            params=self.common_params(team_id=team_id, product_id=product_id),
            auth=True,
            label="credits balance",
            timeout_seconds=timeout_seconds,
        )

    def auth_state(self) -> dict[str, Any]:
        cookies = cookie_list_from_session(self.session)
        cookie_header = cookie_header_from_any(cookies)
        return {
            "cookies": cookie_header,
            "cookie_header": cookie_header,
            "weryai_cookies": cookies,
            "weryai_cookie_header": cookie_header,
            "df_id": self.df_id,
            "client_ip": self.client_ip,
            "user_agent": self.user_agent,
            "sec_ch_ua": self.sec_ch_ua,
            "sec_ch_ua_platform": '"Windows"',
            "weryai_impersonate": self.impersonate,
        }

    def fetch_account_state(
        self,
        *,
        access_token: str = "",
        team_id: Any = "",
        product_id: Any = "",
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        token = text(access_token) or self.access_token
        if not token:
            raise WeryAIAuthError("WeryAI account is missing access_token")
        self.access_token = token
        resolved_team_id = text(team_id)
        context: dict[str, Any] = {}
        if not resolved_team_id:
            context = self.current_context(timeout_seconds=timeout_seconds)
            context_data = context.get("data") if isinstance(context.get("data"), dict) else {}
            current_team = context_data.get("current_team") if isinstance(context_data.get("current_team"), dict) else {}
            resolved_team_id = first_text(current_team.get("id"), context_data.get("team_id"), context_data.get("teamId"))
        resolved_product_id = text(product_id)
        user_info = self.user_info(team_id=resolved_team_id, product_id=resolved_product_id, timeout_seconds=timeout_seconds)
        summary = self.account_summary(team_id=resolved_team_id, product_id=resolved_product_id, timeout_seconds=timeout_seconds)
        balance = self.credits_balance(team_id=resolved_team_id, product_id=resolved_product_id, timeout_seconds=timeout_seconds)
        state = {
            "access_token": self.access_token,
            "authorization": self.access_token,
            "team_id": resolved_team_id,
            "teamId": resolved_team_id,
            "product_id": resolved_product_id,
            "productId": resolved_product_id,
            "current_context": context,
            "user_info": user_info,
            "account_summary": summary,
            "credits_balance": balance.get("data"),
            "last_keepalive_at": utcnow_iso(),
        }
        state.update(self.auth_state())
        state["summary"] = summarize_weryai_account_state(state)
        return state


def extract_weryai_account_context(account: Account) -> dict[str, Any]:
    extra = dict(getattr(account, "extra", {}) or {})
    overview = extra.get("account_overview") if isinstance(extra.get("account_overview"), dict) else {}
    legacy_extra = overview.get("legacy_extra") if isinstance(overview.get("legacy_extra"), dict) else {}
    merged = dict(legacy_extra)
    merged.update({key: value for key, value in overview.items() if key != "legacy_extra"})
    merged.update(extra)
    return {
        "email": first_text(merged.get("email"), getattr(account, "email", "")),
        "password": first_text(getattr(account, "password", ""), merged.get("password")),
        "access_token": first_text(
            merged.get("access_token"),
            merged.get("authorization"),
            merged.get("accessToken"),
            merged.get("token"),
            getattr(account, "token", ""),
        ),
        "team_id": first_text(merged.get("team_id"), merged.get("teamId")),
        "product_id": first_text(merged.get("product_id"), merged.get("productId")),
        "df_id": first_text(merged.get("df_id"), merged.get("device_fingerprint_id")),
        "client_ip": first_text(merged.get("client_ip"), merged.get("weryai_client_ip")),
        "cookies": merged.get("weryai_cookies")
        or merged.get("weryai_cookie_header")
        or merged.get("cookies")
        or merged.get("cookie_header"),
        "cookie_header": first_text(merged.get("weryai_cookie_header"), merged.get("cookie_header"), merged.get("cookies")),
        "proxy_url": first_text(
            merged.get("imgs_weryai_proxy_url"),
            merged.get("weryai_proxy_url"),
            merged.get("proxy_url"),
            merged.get("proxyUrl"),
            merged.get("resolved_proxy"),
            merged.get("proxy"),
        ),
        "user_agent": first_text(merged.get("user_agent"), WERYAI_DEFAULT_USER_AGENT),
        "sec_ch_ua": first_text(merged.get("sec_ch_ua"), WERYAI_DEFAULT_SEC_CH_UA),
        "impersonate": normalize_weryai_impersonate(
            first_text(merged.get("weryai_impersonate"), merged.get("impersonate"), WERYAI_DEFAULT_IMPERSONATE)
        ),
    }


def summarize_weryai_account_state(state: dict[str, Any], *, fallback_email: str = "") -> dict[str, Any]:
    user_info_resp = state.get("user_info") if isinstance(state.get("user_info"), dict) else {}
    user_data = user_info_resp.get("data") if isinstance(user_info_resp.get("data"), dict) else {}
    account_summary_resp = state.get("account_summary") if isinstance(state.get("account_summary"), dict) else {}
    summary_data = account_summary_resp.get("data") if isinstance(account_summary_resp.get("data"), dict) else {}
    credits_balance_raw = state.get("credits_balance")
    credits_summary = summary_data.get("credits_balance") if isinstance(summary_data.get("credits_balance"), dict) else {}
    remaining = safe_float(first_text(credits_balance_raw, credits_summary.get("balance")), 0.0)
    email = first_text(user_data.get("email"), summary_data.get("email"), state.get("email"), fallback_email)
    user_id = first_text(user_data.get("uid"), user_data.get("user_id"), user_data.get("id"), state.get("user_id"))
    team_id = first_text(state.get("team_id"), state.get("teamId"))
    product_id = first_text(state.get("product_id"), state.get("productId"))
    plan_name = first_text(summary_data.get("account_type"), "Free")
    checked_at = utcnow_iso()
    chips: list[str] = []
    chips.append(f"credits {remaining:g}")
    if team_id:
        chips.append(f"team {team_id[:8]}")
    if product_id:
        chips.append(f"product {product_id}")
    overview = {
        "valid": True,
        "email": email,
        "remote_email": email,
        "user_id": user_id,
        "uid": user_id,
        "team_id": team_id,
        "teamId": team_id,
        "product_id": product_id,
        "productId": product_id,
        "plan": plan_name,
        "plan_name": plan_name,
        "plan_state": "free",
        "account_type": summary_data.get("account_type"),
        "register_time": summary_data.get("register_time"),
        "credits_balance": remaining,
        "remaining_credits": remaining,
        "balance": remaining,
        "df_id": first_text(state.get("df_id")),
        "client_ip": first_text(state.get("client_ip")),
        "last_keepalive_at": first_text(state.get("last_keepalive_at")),
        "checked_at": checked_at,
        "chips": chips,
    }
    return {
        **overview,
        "credits": {
            "balance": remaining,
            "raw_balance": credits_balance_raw,
            "summary": credits_summary,
        },
        "account_overview": overview,
    }


def partial_weryai_account_state(
    account: Account | None = None,
    *,
    access_token: str = "",
    client: WeryAIClient | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    context = extract_weryai_account_context(account) if account else {}
    token = text(access_token) or first_text(context.get("access_token"))
    state = {
        "access_token": token,
        "authorization": token,
        "team_id": first_text(context.get("team_id")),
        "teamId": first_text(context.get("team_id")),
        "product_id": first_text(context.get("product_id")),
        "productId": first_text(context.get("product_id")),
        "email": first_text(context.get("email")),
        "df_id": first_text(context.get("df_id"), getattr(client, "df_id", "")),
        "client_ip": first_text(context.get("client_ip"), getattr(client, "client_ip", "")),
        "account_state_partial": True,
        "account_state_error": str(error or ""),
        "last_keepalive_at": utcnow_iso(),
    }
    if client:
        state.update(client.auth_state())
    summary = summarize_weryai_account_state(state, fallback_email=state.get("email", ""))
    overview = dict(summary.get("account_overview") or {})
    overview["valid"] = False if not token else True
    overview["account_state_partial"] = True
    overview["account_state_error"] = str(error or "")
    summary["account_overview"] = overview
    state["summary"] = summary
    return state


def load_weryai_account_state(
    account: Account,
    *,
    proxy: str | None = None,
    log_fn: Callable[[str], None] = print,
    force_refresh: bool = False,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    context = extract_weryai_account_context(account)
    client = WeryAIClient(
        proxy=proxy or context.get("proxy_url"),
        log_fn=log_fn,
        access_token=context.get("access_token", ""),
        cookies=context.get("cookies"),
        cookie_header=context.get("cookie_header", ""),
        df_id=context.get("df_id", ""),
        client_ip=context.get("client_ip", ""),
        impersonate=context.get("impersonate", ""),
        user_agent=context.get("user_agent", ""),
        sec_ch_ua=context.get("sec_ch_ua", ""),
    )
    try:
        state = client.fetch_account_state(
            access_token=context.get("access_token", ""),
            team_id=context.get("team_id", ""),
            product_id=context.get("product_id", ""),
            timeout_seconds=timeout_seconds,
        )
        state["force_refresh"] = bool(force_refresh)
        return state
    except Exception as exc:
        log_fn(f"WeryAI account state refresh failed: {exc}")
        raise


def fetch_weryai_account_state_with_polling(
    client: WeryAIClient,
    *,
    access_token: str,
    team_id: str = "",
    product_id: str = "",
    attempts: int = 3,
    interval_seconds: float = 2.0,
    timeout_seconds: float = 6.0,
    log_fn: Callable[[str], None] = print,
) -> dict[str, Any]:
    total_attempts = max(1, int(attempts or 1))
    wait_seconds = max(0.0, float(interval_seconds or 0))
    last_error: Exception | None = None
    for attempt in range(1, total_attempts + 1):
        try:
            if attempt > 1:
                log_fn(f"WeryAI login state poll {attempt}/{total_attempts}")
            return client.fetch_account_state(
                access_token=access_token,
                team_id=team_id,
                product_id=product_id,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= total_attempts:
                break
            log_fn(f"WeryAI login state not ready, retry in {wait_seconds:.1f}s ({attempt}/{total_attempts}): {exc}")
            if wait_seconds:
                time.sleep(wait_seconds)
    raise last_error or RuntimeError("WeryAI login state polling failed")
