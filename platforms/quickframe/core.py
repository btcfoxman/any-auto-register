"""QuickFrame Auth0 passwordless login and account state client."""
from __future__ import annotations

import base64
import html as html_lib
import json
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http.cookiejar import Cookie
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

from curl_cffi.requests import Session

from core.base_platform import Account


QUICKFRAME_APP_BASE = "https://ai.quickframe.com"
QUICKFRAME_SERVER_BASE = "https://server.cs.quickframe.com"
QUICKFRAME_LOGIN_BASE = "https://login.quickframe.com"
QUICKFRAME_RETURN_URL = f"{QUICKFRAME_APP_BASE}/"
QUICKFRAME_TOKEN_AUDIENCE = "https://ai.quickframe.com"
QUICKFRAME_TOKEN_SCOPE = "openid profile email"
QUICKFRAME_TRPC_WSS_URL = "wss://server.cs.quickframe.com/trpc?connectionParams=1"
QUICKFRAME_EFFECT_VIDEO_SUBSCRIPTION_PATH = "effects.effectVideoGenerationSubscription"
QUICKFRAME_FINGERPRINT_API_KEY = "PR5rgU0BLe8In4lSgde3"
QUICKFRAME_FINGERPRINT_ENDPOINT = "https://verify.quickframe.com"
QUICKFRAME_FINGERPRINT_SCRIPT_URL = (
    f"{QUICKFRAME_FINGERPRINT_ENDPOINT}/web/v4/{QUICKFRAME_FINGERPRINT_API_KEY}?ci=jsl/4.0.3"
)
QUICKFRAME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
QUICKFRAME_SEC_CH_UA = '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"'
QUICKFRAME_COOKIE_DOMAINS = ("quickframe.com", "mountain.com")
_QUICKFRAME_VISITOR_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


class QuickFrameAuthError(RuntimeError):
    pass


class QuickFrameSignupUnavailableError(QuickFrameAuthError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _response_text(response: Any) -> str:
    try:
        return str(response.text or "")
    except Exception:
        return ""


def _quickframe_auth_error_from_url(value: Any, *, include_nested: bool = True) -> str:
    pending = [str(value or "").strip()]
    seen: set[str] = set()
    while pending:
        url = pending.pop(0)
        if not url or url in seen:
            continue
        seen.add(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in ("auth_error", "error"):
            error = str((query.get(key) or [""])[0] or "").strip()
            if error:
                return unquote(error)
        if not include_nested:
            continue
        for key in ("returnTo", "return_to", "redirect_uri", "post_logout_redirect_uri"):
            for nested in query.get(key) or []:
                nested_url = unquote(str(nested or "").strip())
                if nested_url and nested_url not in seen:
                    pending.append(nested_url)
    return ""


def _raise_quickframe_auth_error(error: Any) -> None:
    error_text = str(error or "").strip() or "unknown"
    if error_text == "signup_unavailable":
        raise QuickFrameSignupUnavailableError(
            "QuickFrame signup unavailable (auth_error=signup_unavailable); account is not usable"
        )
    raise QuickFrameAuthError(f"QuickFrame auth failed: auth_error={error_text}")


def _valid_cookie_pair(name: Any, value: Any) -> tuple[str, str] | None:
    cookie_name = str(name or "").strip()
    cookie_value = str(value or "").strip()
    if not cookie_name or cookie_value == "":
        return None
    if any(ch in cookie_name for ch in ";\r\n\t "):
        return None
    if any(ch in cookie_value for ch in ";\r\n"):
        return None
    return cookie_name, cookie_value


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
            pair = _valid_cookie_pair(value.get("name"), value.get("value"))
            return f"{pair[0]}={pair[1]}" if pair else ""
        iterable = value.items()
    elif isinstance(value, list):
        iterable = ((item.get("name"), item.get("value")) for item in value if isinstance(item, dict))
    else:
        return ""
    for name, cookie_value in iterable:
        pair = _valid_cookie_pair(name, cookie_value)
        if pair:
            pairs.append(f"{pair[0]}={pair[1]}")
    return "; ".join(dict.fromkeys(pairs))


def _cookie_pairs_from_header(value: Any) -> list[tuple[str, str]]:
    header = _cookie_header_from_any(value)
    if not header:
        return []
    pairs: list[tuple[str, str]] = []
    for item in header.split(";"):
        if "=" not in item:
            continue
        name, cookie_value = item.split("=", 1)
        pair = _valid_cookie_pair(name.strip(), cookie_value.strip())
        if pair:
            pairs.append(pair)
    return list(dict.fromkeys(pairs))


def _random_token(length: int) -> str:
    return "".join(secrets.choice(_QUICKFRAME_VISITOR_ALPHABET) for _ in range(length))


def _quickframe_anonymous_id() -> str:
    return f"anonymous_{uuid.uuid4()}"


def _quickframe_visitor_id() -> str:
    return _random_token(20)


def _quickframe_event_id() -> str:
    return f"{int(time.time() * 1000)}.{_random_token(6)}"


def _normalize_proxy_url(proxy: str | None) -> str | None:
    value = str(proxy or "").strip()
    if not value:
        return None
    if value.lower().startswith("socks://"):
        return f"socks5://{value.split('://', 1)[1]}"
    return value


def _playwright_proxy_config(proxy: str | None) -> dict[str, str] | None:
    value = _normalize_proxy_url(proxy)
    if not value:
        return None
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.hostname:
        return {"server": value}
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server = f"{server}:{parsed.port}"
    config: dict[str, str] = {"server": server}
    if parsed.username:
        config["username"] = unquote(parsed.username)
    if parsed.password:
        config["password"] = unquote(parsed.password)
    return config


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _boolish(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _json_from_response(response: Any) -> Any:
    text = _response_text(response).strip()
    if not text:
        return {}
    try:
        return response.json()
    except Exception:
        return json.loads(text)


def _extract_html_state(html: str) -> str:
    match = re.search(r'name=["\']state["\']\s+value=["\']([^"\']+)["\']', str(html or ""))
    return match.group(1).strip() if match else ""


def _html_text_summary(html: str, *, limit: int = 220) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", str(html or ""))
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


class _HtmlFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        values = {str(key).lower(): value for key, value in attrs}
        if name == "form":
            self._current = {
                "action": str(values.get("action") or ""),
                "method": str(values.get("method") or "GET").upper(),
                "fields": {},
            }
            return
        if name not in {"input", "button"}:
            return
        field_name = str(values.get("name") or "").strip()
        if not field_name:
            return
        target = self._current
        if target is None:
            if not self.forms or self.forms[-1].get("_implicit") is not True:
                self.forms.append({"action": "", "method": "GET", "fields": {}, "_implicit": True})
            target = self.forms[-1]
        if name == "button":
            target["fields"].setdefault(field_name, str(values.get("value") or ""))
        else:
            target["fields"][field_name] = str(values.get("value") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None

    def close(self) -> None:
        super().close()
        if self._current is not None:
            self.forms.append(self._current)
            self._current = None


def _extract_form(html: str, preferred_field: str) -> tuple[str, dict[str, str]]:
    parser = _HtmlFormParser()
    try:
        parser.feed(str(html or ""))
        parser.close()
    except Exception:
        return "", {}
    preferred = str(preferred_field or "").strip()
    for form in parser.forms:
        fields = form.get("fields") if isinstance(form.get("fields"), dict) else {}
        if preferred and preferred in fields:
            return str(form.get("action") or ""), {str(key): str(value) for key, value in fields.items()}
    for form in parser.forms:
        fields = form.get("fields") if isinstance(form.get("fields"), dict) else {}
        if "state" in fields:
            return str(form.get("action") or ""), {str(key): str(value) for key, value in fields.items()}
    return "", {}


def _extract_turnstile_sitekey(html: str) -> str:
    text = str(html or "")
    candidates = [text]
    try:
        unescaped = html_lib.unescape(text)
        if unescaped != text:
            candidates.append(unescaped)
    except Exception:
        pass

    patterns = (
        r'data-captcha-sitekey=["\']([^"\']+)["\']',
        r'data-sitekey=["\']([^"\']+)["\']',
        r'["\']siteKey["\']\s*:\s*["\']([^"\']+)["\']',
        r'["\']sitekey["\']\s*:\s*["\']([^"\']+)["\']',
        r'["\']captchaSiteKey["\']\s*:\s*["\']([^"\']+)["\']',
        r'["\']site_key["\']\s*:\s*["\']([^"\']+)["\']',
        r"\b(0x4[A-Za-z0-9_-]{20,})\b",
    )
    for candidate in candidates:
        for pattern in patterns:
            match = re.search(pattern, candidate, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return ""


def _query_state(url: str) -> str:
    try:
        parsed = urlparse(url)
        return str((parse_qs(parsed.query).get("state") or [""])[0]).strip()
    except Exception:
        return ""


def _is_redirect(status_code: int) -> bool:
    return 300 <= int(status_code or 0) < 400


def _jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    raw = parts[1]
    raw += "=" * (-len(raw) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


def _token_expire_at(expires_in: Any) -> str:
    seconds = _safe_int(expires_in)
    if seconds <= 0:
        return ""
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _first_trpc_data(payload: Any, index: int = 0) -> Any:
    try:
        item = payload[index] if isinstance(payload, list) else payload
        if isinstance(item, dict):
            return ((item.get("result") or {}).get("data"))
    except Exception:
        pass
    return None


def _session_user_email(user: dict[str, Any]) -> str:
    primary = user.get("primaryEmailAddress")
    primary_email = primary.get("emailAddress") if isinstance(primary, dict) else primary
    return str(user.get("email") or primary_email or "").strip()


def _quickframe_session_active(session_info: dict[str, Any]) -> bool:
    session = session_info.get("session") if isinstance(session_info.get("session"), dict) else {}
    user = session_info.get("user") if isinstance(session_info.get("user"), dict) else {}
    status = str(session.get("status") or session_info.get("status") or "").strip().lower()
    if status in {"inactive", "expired", "invalid", "unauthenticated", "anonymous"}:
        return False
    if bool(session.get("active") or session_info.get("active") or session_info.get("authenticated") or session_info.get("isAuthenticated")):
        return True
    if status in {"active", "authenticated", "valid"}:
        return True
    return bool(user.get("id") or _session_user_email(user))


def collect_quickframe_browser_auth_context(
    *,
    proxy: str | None = None,
    log_fn: Callable[[str], None] = print,
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    """Collect the same FingerprintJS login context used by the captured frontend."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(f"Playwright is not available: {exc}") from exc

    proxy_config = _playwright_proxy_config(proxy)
    with sync_playwright() as playwright:
        launch_options: dict[str, Any] = {"headless": True}
        if proxy_config:
            launch_options["proxy"] = proxy_config
        browser = playwright.chromium.launch(**launch_options)
        try:
            context = browser.new_context(
                user_agent=QUICKFRAME_USER_AGENT,
                locale="zh-HK",
                viewport={"width": 1536, "height": 791},
            )
            try:
                page = context.new_page()
                page.goto(QUICKFRAME_RETURN_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                result = page.evaluate(
                    """
                    async ({ apiKey, endpoint, scriptUrl }) => {
                        const cookieName = "dd_anonymous_user_id";
                        const cookieRe = /^anonymous_[0-9a-f-]{36}$/i;
                        const readCookie = () => {
                            for (const item of document.cookie.split("; ")) {
                                if (item.startsWith(`${cookieName}=`)) {
                                    return decodeURIComponent(item.slice(cookieName.length + 1));
                                }
                            }
                            return "";
                        };
                        const writeCookie = (value) => {
                            document.cookie = `${cookieName}=${encodeURIComponent(value)}; Max-Age=31536000; Path=/; SameSite=Lax; Secure; Domain=.quickframe.com`;
                        };
                        let previousAnonymousId = readCookie();
                        if (!cookieRe.test(previousAnonymousId || "")) {
                            previousAnonymousId = `anonymous_${crypto.randomUUID()}`;
                            writeCookie(previousAnonymousId);
                        }
                        const module = await import(scriptUrl);
                        const agent = module.start({ apiKey, endpoints: endpoint });
                        const fp = await agent.get({ timeout: 10000 });
                        return {
                            previous_anonymous_id: previousAnonymousId,
                            visitorId: fp.visitor_id || fp.visitorId || "",
                            eventId: fp.event_id || fp.eventId || "",
                        };
                    }
                    """,
                    {
                        "apiKey": QUICKFRAME_FINGERPRINT_API_KEY,
                        "endpoint": QUICKFRAME_FINGERPRINT_ENDPOINT,
                        "scriptUrl": QUICKFRAME_FINGERPRINT_SCRIPT_URL,
                    },
                )
                cookies = context.cookies(
                    [
                        QUICKFRAME_APP_BASE,
                        QUICKFRAME_SERVER_BASE,
                        QUICKFRAME_LOGIN_BASE,
                        QUICKFRAME_FINGERPRINT_ENDPOINT,
                    ]
                )
                data = dict(result or {})
                data["cookies"] = cookies
                return data
            finally:
                context.close()
        finally:
            browser.close()


def quickframe_effect_subscription_messages(
    access_token: str,
    run_id: str,
    *,
    subscription_id: int = 1,
) -> list[dict[str, Any]]:
    """Build the captured tRPC WSS auth and effect-generation subscription frames."""
    token = str(access_token or "").strip()
    job_id = str(run_id or "").strip()
    if not token:
        raise RuntimeError("QuickFrame WSS subscription requires access token")
    if not job_id:
        raise RuntimeError("QuickFrame WSS subscription requires runId/jobId")
    return [
        {"method": "connectionParams", "data": {"token": token}},
        {
            "id": int(subscription_id),
            "method": "subscription",
            "params": {
                "input": {"runId": job_id},
                "path": QUICKFRAME_EFFECT_VIDEO_SUBSCRIPTION_PATH,
            },
        },
    ]


def parse_quickframe_effect_subscription_message(message: Any) -> dict[str, Any]:
    """Parse captured effect-generation WSS frames without treating heartbeat/cleanup as terminal."""
    if isinstance(message, (bytes, bytearray)):
        text = message.decode("utf-8", errors="ignore").strip()
    elif isinstance(message, str):
        text = message.strip()
    else:
        text = ""
    if text.upper() in {"PING", "PONG"}:
        return {"type": "heartbeat", "terminal": False, "raw": text}
    try:
        payload = json.loads(text) if text else message
    except Exception as exc:
        return {"type": "unknown", "terminal": False, "raw": text, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"type": "unknown", "terminal": False, "raw": payload}

    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    result_type = str(result.get("type") or "").strip()
    if result_type in {"started", "stopped"}:
        return {
            "id": payload.get("id"),
            "type": result_type,
            "terminal": False,
            "raw": payload,
        }
    if result_type != "data":
        return {
            "id": payload.get("id"),
            "type": result_type or str(payload.get("method") or "unknown"),
            "terminal": False,
            "raw": payload,
        }

    envelope = result.get("data") if isinstance(result.get("data"), dict) else {}
    event_type = str(envelope.get("type") or "").strip()
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    parsed = {
        "id": payload.get("id"),
        "type": event_type,
        "terminal": event_type in {"complete", "error"},
        "ok": True if event_type == "complete" else False if event_type == "error" else None,
        "run_id": str(data.get("runId") or ""),
        "asset_id": data.get("assetId"),
        "video_url": str(data.get("videoUrl") or ""),
        "progress": data.get("progress"),
        "step_name": str(data.get("stepName") or ""),
        "status": str(data.get("status") or ""),
        "error": str(data.get("error") or ""),
        "raw": payload,
    }
    return parsed


def _account_extra(account: Account | Any) -> dict[str, Any]:
    extra = dict(getattr(account, "extra", {}) or {})
    overview = extra.get("account_overview") if isinstance(extra.get("account_overview"), dict) else {}
    legacy = overview.get("legacy_extra") if isinstance(overview.get("legacy_extra"), dict) else {}
    merged = dict(legacy)
    merged.update({key: value for key, value in overview.items() if key != "legacy_extra"})
    merged.update(extra)
    return merged


class QuickFrameClient:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        log_fn: Callable[[str], None] = print,
        cookie_header: str = "",
        cookies: Any = None,
        access_token: str = "",
        login_state: str = "",
        login_identifier_url: str = "",
        challenge_url: str = "",
        return_url: str = QUICKFRAME_RETURN_URL,
        turnstile_solver: Callable[[str, str], str] | Any = None,
        browser_fingerprint: bool = False,
        fingerprint_collector: Callable[..., dict[str, Any]] | None = None,
    ):
        self._log = log_fn
        self._cookie_header = _cookie_header_from_any(cookie_header or cookies)
        self.proxy = _normalize_proxy_url(proxy)
        self.access_token = str(access_token or "").strip()
        self.login_state = str(login_state or "").strip()
        self.login_identifier_url = str(login_identifier_url or "").strip()
        self.challenge_url = str(challenge_url or "").strip()
        self.login_identifier_form: dict[str, str] = {}
        self.challenge_form: dict[str, str] = {}
        self.login_identifier_html = ""
        self.challenge_html = ""
        self.return_url = str(return_url or QUICKFRAME_RETURN_URL).strip()
        self.previous_anonymous_id = ""
        self.visitor_id = ""
        self.event_id = ""
        self.turnstile_solver = turnstile_solver
        self.browser_fingerprint = bool(browser_fingerprint)
        self.fingerprint_collector = fingerprint_collector
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        self.s = Session(impersonate="chrome", proxies=proxies, timeout=30)
        self.s.headers.update(
            {
                "accept-language": "zh-HK,zh;q=0.9,en;q=0.8",
                "user-agent": QUICKFRAME_USER_AGENT,
                "sec-ch-ua": QUICKFRAME_SEC_CH_UA,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
        )
        self._seed_cookies(cookies)
        self._seed_login_cookie_header()

    def log(self, message: str) -> None:
        self._log(message)

    def _default_cookie_domain(self) -> str:
        target_url = self.challenge_url or self.login_identifier_url
        host = str(urlparse(target_url).hostname or "").strip().lower()
        if host == "login.quickframe.com":
            return "login.quickframe.com"
        return ".quickframe.com"

    def _seed_cookies(self, cookies: Any) -> None:
        if not isinstance(cookies, list):
            return
        for item in cookies:
            if not isinstance(item, dict):
                continue
            pair = _valid_cookie_pair(item.get("name"), item.get("value"))
            if not pair:
                continue
            domain = str(item.get("domain") or "").strip() or self._default_cookie_domain()
            path = str(item.get("path") or "/").strip() or "/"
            try:
                self.s.cookies.set(pair[0], pair[1], domain=domain, path=path)
            except Exception:
                continue

    def _apply_browser_auth_context(self) -> None:
        if not self.browser_fingerprint:
            return
        if self.previous_anonymous_id and self.visitor_id and self.event_id:
            return
        collector = self.fingerprint_collector or collect_quickframe_browser_auth_context
        try:
            data = collector(proxy=self.proxy, log_fn=self.log)
        except Exception as exc:
            self.log(f"QuickFrame FingerprintJS context unavailable, using generated context: {exc}")
            return
        if not isinstance(data, dict):
            return
        previous = str(data.get("previous_anonymous_id") or data.get("previousAnonymousId") or "").strip()
        visitor_id = str(data.get("visitorId") or data.get("visitor_id") or "").strip()
        event_id = str(data.get("eventId") or data.get("event_id") or "").strip()
        if previous:
            self.previous_anonymous_id = previous
        if visitor_id:
            self.visitor_id = visitor_id
        if event_id:
            self.event_id = event_id
        self._seed_cookies(data.get("cookies"))
        if visitor_id and event_id:
            self.log("QuickFrame FingerprintJS context acquired")

    def _seed_login_cookie_header(self) -> None:
        target_url = self.challenge_url or self.login_identifier_url
        host = str(urlparse(target_url).hostname or "").strip().lower()
        if host != "login.quickframe.com":
            return
        for name, cookie_value in _cookie_pairs_from_header(self._cookie_header):
            try:
                self.s.cookies.set(name, cookie_value, domain=host, path="/")
            except Exception:
                continue

    def _ensure_auth_context(self) -> dict[str, str]:
        self._apply_browser_auth_context()
        if not self.previous_anonymous_id:
            self.previous_anonymous_id = _quickframe_anonymous_id()
        if not self.visitor_id:
            self.visitor_id = _quickframe_visitor_id()
        if not self.event_id:
            self.event_id = _quickframe_event_id()
        try:
            self.s.cookies.set("dd_anonymous_user_id", self.previous_anonymous_id, domain=".quickframe.com", path="/")
        except Exception:
            pass
        return {
            "previous_anonymous_id": self.previous_anonymous_id,
            "visitorId": self.visitor_id,
            "eventId": self.event_id,
        }

    def _solve_turnstile(self, page_url: str, sitekey: str) -> str:
        solver = self.turnstile_solver
        if not solver:
            raise RuntimeError(
                "QuickFrame Auth0 security challenge requires Turnstile captcha provider; "
                "enable a captcha provider or use a browser-backed flow"
            )
        if callable(solver):
            token = solver(page_url, sitekey)
        elif hasattr(solver, "solve_turnstile"):
            token = solver.solve_turnstile(page_url, sitekey)
        else:
            raise RuntimeError("QuickFrame Turnstile solver does not expose solve_turnstile")
        token = str(token or "").strip()
        if not token:
            raise RuntimeError("QuickFrame Turnstile solver returned an empty token")
        return token

    def _prepare_auth0_captcha(self, form: dict[str, str], *, page_url: str, html: str, label: str) -> None:
        captcha_fields = [
            name
            for name in ("captcha", "cf-turnstile-response")
            if name in form and not str(form.get(name) or "").strip()
        ]
        if not captcha_fields:
            return
        sitekey = _extract_turnstile_sitekey(html)
        if not sitekey:
            raise RuntimeError(
                f"QuickFrame Auth0 {label} requires captcha but the page did not expose a Turnstile sitekey"
            )
        self.log(f"QuickFrame Auth0 {label}: detected Turnstile security challenge")
        token = self._solve_turnstile(page_url, sitekey)
        for name in captcha_fields:
            form[name] = token

    def cookie_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            jar = self.s.cookies.jar
        except Exception:
            return records
        for cookie in list(jar):
            if not isinstance(cookie, Cookie):
                continue
            domain = str(cookie.domain or "")
            if domain and not any(domain.lstrip(".").endswith(item) for item in QUICKFRAME_COOKIE_DOMAINS):
                continue
            records.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path or "/",
                    "expires": cookie.expires,
                    "secure": bool(cookie.secure),
                }
            )
        return records

    def cookie_header(self) -> str:
        records = self.cookie_records()
        return _cookie_header_from_any(records) or self._cookie_header

    def auth_state(self) -> dict[str, Any]:
        cookie_header = self.cookie_header()
        return {
            "cookies": cookie_header,
            "cookie_header": cookie_header,
            "quickframe_cookies": self.cookie_records(),
            "access_token": self.access_token,
            "accessToken": self.access_token,
        }

    def pending_login_state(self) -> dict[str, Any]:
        return {
            "quickframe_login_state": self.login_state,
            "quickframe_login_identifier_url": self.login_identifier_url,
            "quickframe_challenge_url": self.challenge_url,
            "quickframe_pending_cookies": self.cookie_records(),
            "quickframe_pending_cookie_header": self.cookie_header(),
        }

    def _headers(
        self,
        *,
        accept: str = "*/*",
        content_type: str = "",
        referer: str = QUICKFRAME_RETURN_URL,
        origin: str = "",
        authorization: str = "",
        include_cookie: bool = True,
        manual_cookie: bool = True,
        document_navigation: bool = False,
    ) -> dict[str, str]:
        headers = {
            "accept": accept,
            "accept-language": "zh-HK,zh;q=0.9,en;q=0.8",
            "sec-ch-ua": QUICKFRAME_SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "user-agent": QUICKFRAME_USER_AGENT,
        }
        if referer:
            headers["referer"] = referer
        if content_type:
            headers["content-type"] = content_type
        if origin:
            headers["origin"] = origin
        if authorization:
            headers["authorization"] = authorization
        if document_navigation:
            headers["upgrade-insecure-requests"] = "1"
        if include_cookie and manual_cookie and self._cookie_header and not self.cookie_records():
            headers["cookie"] = self._cookie_header
        return headers

    def start_login(self, email: str, *, return_url: str | None = None, screen_hint: str = "") -> dict[str, Any]:
        email = str(email or "").strip()
        if not email:
            raise RuntimeError("QuickFrame login requires email")
        target_return_url = str(return_url or self.return_url or QUICKFRAME_RETURN_URL).strip()
        params = {"returnUrl": target_return_url, **self._ensure_auth_context()}
        if screen_hint:
            params["screen_hint"] = str(screen_hint)
        url = f"{QUICKFRAME_SERVER_BASE}/auth/login?{urlencode(params)}"
        response = self.s.get(
            url,
            headers=self._headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                referer=target_return_url,
                include_cookie=True,
                manual_cookie=False,
                document_navigation=True,
            ),
            allow_redirects=False,
        )
        self.log(f"GET /auth/login -> {response.status_code}")
        current_url = url
        for _ in range(8):
            if _is_redirect(response.status_code):
                location = str(response.headers.get("location") or "").strip()
                if not location:
                    break
                previous_url = current_url
                current_url = urljoin(current_url, location)
                response = self.s.get(
                    current_url,
                    headers=self._headers(
                        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        referer=previous_url,
                        include_cookie=True,
                        manual_cookie=False,
                        document_navigation=True,
                    ),
                    allow_redirects=False,
                )
                self.log(f"GET {urlparse(current_url).path} -> {response.status_code}")
                continue
            break
        if "/u/login/passwordless-email-challenge" in current_url:
            html = _response_text(response)
            action, fields = _extract_form(html, "code")
            state = str(fields.get("state") or "").strip() or _query_state(current_url) or _extract_html_state(html)
            if not state:
                raise RuntimeError("QuickFrame passwordless challenge page did not include state")
            self.login_state = state
            self.login_identifier_url = ""
            self.challenge_form = fields
            self.challenge_html = html
            self.challenge_url = urljoin(current_url, action) if action else current_url
            return {"state": state, "identifier_url": "", "challenge_url": current_url}
        if "/u/login/identifier" not in current_url:
            raise RuntimeError(f"QuickFrame login did not reach identifier page: {current_url}")
        html = _response_text(response)
        action, fields = _extract_form(html, "username")
        state = str(fields.get("state") or "").strip() or _query_state(current_url) or _extract_html_state(html)
        if not state:
            raise RuntimeError("QuickFrame login identifier page did not include state")
        self.login_state = state
        self.login_identifier_form = fields
        self.login_identifier_html = html
        self.login_identifier_url = urljoin(current_url, action) if action else current_url
        return {"state": state, "identifier_url": self.login_identifier_url}

    def begin_email_challenge(self, email: str) -> dict[str, Any]:
        if not self.login_state or (not self.login_identifier_url and not self.challenge_url):
            self.start_login(email)
        if self.challenge_url and not self.login_identifier_url:
            return self.pending_login_state()
        state = self.login_state
        identifier_url = self.login_identifier_url
        form = {
            **self.login_identifier_form,
            "state": self.login_identifier_form.get("state") or state,
            "username": str(email or "").strip(),
        }
        form.setdefault("js-available", "true")
        form.setdefault("webauthn-available", "true")
        form.setdefault("is-brave", "false")
        form.setdefault("webauthn-platform-available", "true")
        self._prepare_auth0_captcha(form, page_url=identifier_url, html=self.login_identifier_html, label="identifier page")
        response = self.s.post(
            identifier_url,
            headers=self._headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                content_type="application/x-www-form-urlencoded",
                origin=QUICKFRAME_LOGIN_BASE,
                referer=identifier_url,
                include_cookie=True,
                manual_cookie=False,
                document_navigation=True,
            ),
            data=urlencode(form),
            allow_redirects=False,
        )
        self.log(f"POST /u/login/identifier -> {response.status_code}")
        if not _is_redirect(response.status_code):
            body = _response_text(response)
            field_names = ",".join(form.keys())
            raise RuntimeError(
                f"QuickFrame identifier submit failed: HTTP {response.status_code}; "
                f"form_fields={field_names}; error={_html_text_summary(body) or body[:220]}"
            )
        location = str(response.headers.get("location") or "").strip()
        challenge_url = urljoin(identifier_url, location)
        if "/u/login/passwordless-email-challenge" not in challenge_url:
            raise RuntimeError(f"QuickFrame identifier submit did not return passwordless challenge: {challenge_url}")
        challenge = self.s.get(
            challenge_url,
            headers=self._headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                origin=QUICKFRAME_LOGIN_BASE,
                referer=identifier_url,
                include_cookie=True,
                manual_cookie=False,
                document_navigation=True,
            ),
            allow_redirects=False,
        )
        self.log(f"GET /u/login/passwordless-email-challenge -> {challenge.status_code}")
        if challenge.status_code != 200:
            raise RuntimeError(f"QuickFrame passwordless challenge page failed: HTTP {challenge.status_code}")
        html = _response_text(challenge)
        action, fields = _extract_form(html, "code")
        self.challenge_form = fields
        self.challenge_html = html
        self.challenge_url = urljoin(challenge_url, action) if action else challenge_url
        self.login_state = str(fields.get("state") or "").strip() or _extract_html_state(html) or _query_state(challenge_url) or state
        return self.pending_login_state()

    def complete_email_challenge(self, code: str) -> dict[str, Any]:
        code = str(code or "").strip()
        if not re.fullmatch(r"\d{4,8}", code):
            raise RuntimeError(f"QuickFrame email verification code is invalid: {code!r}")
        if not self.challenge_url or not self.login_state:
            raise RuntimeError("QuickFrame passwordless challenge state is missing; send login code first")
        form = {**self.challenge_form, "state": self.challenge_form.get("state") or self.login_state, "code": code}
        self._prepare_auth0_captcha(
            form,
            page_url=self.challenge_url,
            html=self.challenge_html,
            label="passwordless challenge page",
        )
        response = self.s.post(
            self.challenge_url,
            headers=self._headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                content_type="application/x-www-form-urlencoded",
                origin=QUICKFRAME_LOGIN_BASE,
                referer=self.challenge_url,
                include_cookie=True,
                manual_cookie=False,
                document_navigation=True,
            ),
            data=urlencode(form),
            allow_redirects=False,
        )
        self.log(f"POST /u/login/passwordless-email-challenge -> {response.status_code}")
        if not _is_redirect(response.status_code):
            body = _response_text(response)
            field_names = ",".join(form.keys())
            raise RuntimeError(
                f"QuickFrame code submit failed: HTTP {response.status_code}; "
                f"form_fields={field_names}; error={_html_text_summary(body) or body[:220]}"
            )
        next_url = urljoin(self.challenge_url, str(response.headers.get("location") or ""))
        self._follow_login_redirects(next_url, referer=self.challenge_url)
        return self.fetch_account_state(force_refresh=True)

    def login_with_email_code(self, email: str, otp_callback: Callable[[], str] | None) -> dict[str, Any]:
        if not otp_callback:
            raise RuntimeError("QuickFrame email OTP callback is not configured")
        self.begin_email_challenge(email)
        code = str(otp_callback() or "").strip()
        if not code:
            raise RuntimeError("QuickFrame email verification code timed out")
        return self.complete_email_challenge(code)

    def _follow_login_redirects(self, start_url: str, *, referer: str) -> None:
        current_url = start_url
        current_referer = referer
        for _ in range(10):
            current = urlparse(current_url)
            auth_error = _quickframe_auth_error_from_url(current_url, include_nested=False)
            if auth_error:
                _raise_quickframe_auth_error(auth_error)
            is_auth0_logout = current.hostname == "login.quickframe.com" and current.path.startswith("/v2/logout")
            previous = urlparse(current_referer)
            referer_header = current_referer if current.hostname == previous.hostname else ""
            response = self.s.get(
                current_url,
                headers=self._headers(
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    origin=QUICKFRAME_LOGIN_BASE,
                    referer=referer_header,
                    include_cookie=True,
                    manual_cookie=False,
                    document_navigation=True,
                ),
                allow_redirects=False,
            )
            self.log(f"GET {urlparse(current_url).netloc}{urlparse(current_url).path} -> {response.status_code}")
            if _is_redirect(response.status_code):
                location = str(response.headers.get("location") or "").strip()
                if not location:
                    if is_auth0_logout:
                        auth_error = _quickframe_auth_error_from_url(current_url, include_nested=True)
                        if auth_error:
                            _raise_quickframe_auth_error(auth_error)
                        raise RuntimeError("QuickFrame auth callback redirected to Auth0 logout; session was not established")
                    return
                current_referer = current_url
                current_url = urljoin(current_url, location)
                auth_error = _quickframe_auth_error_from_url(current_url, include_nested=False)
                if auth_error:
                    _raise_quickframe_auth_error(auth_error)
                if is_auth0_logout:
                    auth_error = _quickframe_auth_error_from_url(current_referer, include_nested=True)
                    if auth_error:
                        _raise_quickframe_auth_error(auth_error)
                    raise RuntimeError("QuickFrame auth callback redirected to Auth0 logout; session was not established")
                continue
            return

    def get_session(self) -> dict[str, Any]:
        response = self.s.get(
            f"{QUICKFRAME_SERVER_BASE}/session",
            headers=self._headers(accept="*/*", referer=QUICKFRAME_RETURN_URL, include_cookie=True),
        )
        self.log(f"GET /session -> {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"QuickFrame session failed: HTTP {response.status_code} {_response_text(response)[:300]}")
        data = _json_from_response(response)
        if not isinstance(data, dict):
            raise RuntimeError("QuickFrame session response is not a JSON object")
        return data

    def issue_token(self) -> dict[str, Any]:
        response = self.s.post(
            f"{QUICKFRAME_SERVER_BASE}/token",
            headers=self._headers(
                accept="*/*",
                content_type="application/json",
                referer=QUICKFRAME_RETURN_URL,
                include_cookie=True,
            ),
            data=_json_dumps({"audience": QUICKFRAME_TOKEN_AUDIENCE, "scope": QUICKFRAME_TOKEN_SCOPE}),
        )
        self.log(f"POST /token -> {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"QuickFrame token failed: HTTP {response.status_code} {_response_text(response)[:300]}")
        data = _json_from_response(response)
        if not isinstance(data, dict):
            raise RuntimeError("QuickFrame token response is not a JSON object")
        token = str(data.get("accessToken") or "").strip()
        if not token:
            raise RuntimeError("QuickFrame token response did not include accessToken")
        self.access_token = token
        data["token_expires_at"] = _token_expire_at(data.get("expiresIn"))
        return data

    def trpc_get(self, procedures: list[str] | str, input_data: Any = None, *, token: str = "") -> Any:
        if isinstance(procedures, str):
            proc_path = procedures
        else:
            proc_path = ",".join(str(item) for item in procedures if str(item).strip())
        if not proc_path:
            raise RuntimeError("QuickFrame tRPC procedure is empty")
        token_value = str(token or self.access_token or "").strip()
        if not token_value:
            raise RuntimeError("QuickFrame tRPC request requires access token")
        query = {"batch": "1", "input": _json_dumps(input_data if input_data is not None else {})}
        url = f"{QUICKFRAME_SERVER_BASE}/trpc/{proc_path}?{urlencode(query)}"
        response = self.s.get(
            url,
            headers=self._headers(
                accept="*/*",
                content_type="application/json",
                referer=QUICKFRAME_RETURN_URL,
                authorization=f"Bearer {token_value}",
                include_cookie=True,
            ),
        )
        self.log(f"GET /trpc/{proc_path} -> {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"QuickFrame tRPC {proc_path} failed: HTTP {response.status_code} {_response_text(response)[:300]}")
        return _json_from_response(response)

    def fetch_account_state(self, *, force_refresh: bool = False) -> dict[str, Any]:
        session_info = self.get_session()
        active = _quickframe_session_active(session_info)
        token_info: dict[str, Any] = {}
        if active and (force_refresh or not self.access_token):
            token_info = self.issue_token()
        elif self.access_token:
            token_info = {"accessToken": self.access_token, "tokenType": "Bearer", "expiresIn": 0}
        if not self.access_token:
            session = session_info.get("session") if isinstance(session_info.get("session"), dict) else {}
            user = session_info.get("user") if isinstance(session_info.get("user"), dict) else {}
            raise RuntimeError(
                "QuickFrame session did not produce an access token; "
                f"session_status={session.get('status') or session_info.get('status') or '-'}; "
                f"has_user={bool(user.get('id') or _session_user_email(user))}"
            )

        check_payload = self.trpc_get("auth.checkSession")
        combined_payload: Any = []
        usage_payload: Any = []
        try:
            combined_payload = self.trpc_get(["brand.getAllBrandsSummary", "billing.getSubscriptionStatus", "auth.checkSession"])
        except Exception as exc:
            combined_payload = [{"result": {"data": []}}, {"result": {"data": {}}}, {"result": {"data": {}}}]
            self.log(f"QuickFrame combined status query failed, continuing with auth.checkSession: {exc}")
        try:
            usage_payload = self.trpc_get(["billing.getTierPricing", "billing.checkUsageLimits"])
        except Exception as exc:
            usage_payload = []
            self.log(f"QuickFrame usage query failed, continuing without usage limits: {exc}")

        state = {
            "valid": active,
            "session_info": session_info,
            "token_info": token_info,
            "check_session": _first_trpc_data(check_payload, 0),
            "brands": _first_trpc_data(combined_payload, 0),
            "subscription_status": _first_trpc_data(combined_payload, 1),
            "combined_check_session": _first_trpc_data(combined_payload, 2),
            "tier_pricing": _first_trpc_data(usage_payload, 0),
            "usage_limits": _first_trpc_data(usage_payload, 1),
            "last_keepalive_at": _now_iso(),
            "access_token": self.access_token,
            "accessToken": self.access_token,
            "cookies": self.cookie_header(),
            "cookie_header": self.cookie_header(),
            "quickframe_cookies": self.cookie_records(),
        }
        state["summary"] = summarize_quickframe_account_state(state)
        return state


def summarize_quickframe_account_state(state: dict[str, Any], *, fallback_email: str = "") -> dict[str, Any]:
    session_info = state.get("session_info") if isinstance(state.get("session_info"), dict) else {}
    session = session_info.get("session") if isinstance(session_info.get("session"), dict) else {}
    session_user = session_info.get("user") if isinstance(session_info.get("user"), dict) else {}
    check_session = state.get("combined_check_session") or state.get("check_session") or {}
    if not isinstance(check_session, dict):
        check_session = {}
    subscription = state.get("subscription_status") if isinstance(state.get("subscription_status"), dict) else {}
    usage_limits = state.get("usage_limits") if isinstance(state.get("usage_limits"), dict) else {}
    token_info = state.get("token_info") if isinstance(state.get("token_info"), dict) else {}
    token = str(state.get("access_token") or token_info.get("accessToken") or "").strip()
    jwt = _jwt_payload(token)
    active = _quickframe_session_active(session_info)
    user_id = str(check_session.get("id") or check_session.get("actualUserId") or "").strip()
    workspace_id = str(check_session.get("workspaceId") or "").strip()
    email = str(
        check_session.get("email")
        or session_user.get("email")
        or _session_user_email(session_user)
        or jwt.get("mntn_email")
        or fallback_email
        or ""
    ).strip()
    free_exports_remaining = subscription.get("freeExportsRemaining")
    has_subscription = bool(subscription.get("hasActiveSubscription"))
    plan_name = "Subscribed" if has_subscription else "Free"
    plan_state = "subscribed" if has_subscription else "free"
    token_expire_at = str(token_info.get("token_expires_at") or "")
    if not token_expire_at and _safe_int(jwt.get("exp")):
        token_expire_at = datetime.fromtimestamp(_safe_int(jwt.get("exp")), timezone.utc).isoformat().replace("+00:00", "Z")
    chips: list[str] = ["会话有效" if active else "会话无效", plan_name]
    if free_exports_remaining not in (None, ""):
        chips.append(f"导出剩余 {free_exports_remaining}")
    if workspace_id:
        chips.append(f"Workspace {workspace_id}")

    summary: dict[str, Any] = {
        "valid": bool(active and token),
        "email": email,
        "remote_email": email,
        "user_id": user_id,
        "account_id": user_id,
        "workspace_id": workspace_id,
        "workspaceId": workspace_id,
        "session_id": str(session.get("id") or ""),
        "session_status": str(session.get("status") or ""),
        "plan": plan_name,
        "plan_name": plan_name,
        "plan_state": plan_state,
        "has_active_subscription": has_subscription,
        "free_exports_remaining": free_exports_remaining,
        "remaining_credits": "" if free_exports_remaining in (None, "") else str(free_exports_remaining),
        "token_type": str(token_info.get("tokenType") or "Bearer"),
        "token_expires_in": _safe_int(token_info.get("expiresIn")),
        "token_expires_at": token_expire_at,
        "last_keepalive_at": str(state.get("last_keepalive_at") or ""),
        "signup_source": str(check_session.get("signupSource") or ""),
        "has_premier_account": bool(check_session.get("hasPremierAccount")),
        "usage_limits": usage_limits,
        "subscription_status": subscription,
        "chips": chips,
    }
    summary["account_overview"] = {
        key: value
        for key, value in summary.items()
        if key not in {"account_overview", "usage_limits", "subscription_status"}
    }
    return summary


def partial_quickframe_account_state(token: str, *, client: QuickFrameClient | None = None, error: Any = "") -> dict[str, Any]:
    state: dict[str, Any] = {
        "valid": bool(token),
        "access_token": str(token or "").strip(),
        "accessToken": str(token or "").strip(),
        "last_keepalive_at": _now_iso(),
        "account_state_partial": True,
        "account_state_error": str(error or ""),
    }
    if client is not None:
        state.update(client.auth_state())
    state["summary"] = summarize_quickframe_account_state(state)
    return state


def extract_quickframe_account_context(account: Account | Any) -> dict[str, Any]:
    extra = _account_extra(account)
    token = (
        extra.get("access_token")
        or extra.get("accessToken")
        or extra.get("quickframe_access_token")
        or extra.get("token")
        or getattr(account, "token", "")
        or ""
    )
    cookies = _cookie_header_from_any(
        extra.get("cookies")
        or extra.get("cookie_header")
        or extra.get("quickframe_cookies")
        or extra.get("quickframe_cookie_header")
    )
    return {
        "access_token": str(token or "").strip(),
        "cookies": cookies,
        "cookie_header": cookies,
        "user_id": str(extra.get("user_id") or extra.get("account_id") or getattr(account, "user_id", "") or "").strip(),
        "workspace_id": str(extra.get("workspace_id") or extra.get("workspaceId") or "").strip(),
        "email": str(extra.get("email") or getattr(account, "email", "") or "").strip(),
    }


def load_quickframe_account_state(
    account: Account | Any,
    *,
    proxy: str | None = None,
    log_fn: Callable[[str], None] = print,
    force_refresh: bool = False,
) -> dict[str, Any]:
    extra = _account_extra(account)
    pending_cookies = extra.get("quickframe_pending_cookies")
    cookies = extra.get("quickframe_cookies") or extra.get("cookies") or extra.get("cookie_header")
    if pending_cookies and not cookies:
        cookies = pending_cookies
    client = QuickFrameClient(
        proxy=proxy,
        log_fn=log_fn,
        cookie_header=str(extra.get("cookie_header") or extra.get("quickframe_cookie_header") or ""),
        cookies=cookies,
        access_token=str(extra.get("access_token") or extra.get("accessToken") or getattr(account, "token", "") or ""),
    )
    state = client.fetch_account_state(force_refresh=force_refresh)
    summary = dict(state.get("summary") or {})
    if not summary.get("email"):
        summary = summarize_quickframe_account_state(state, fallback_email=str(getattr(account, "email", "") or ""))
        state["summary"] = summary
    return state
