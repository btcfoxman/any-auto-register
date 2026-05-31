"""QuickFrame Auth0 passwordless login and account state client."""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http.cookiejar import Cookie
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

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
QUICKFRAME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
QUICKFRAME_SEC_CH_UA = '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"'
QUICKFRAME_COOKIE_DOMAINS = ("quickframe.com", "mountain.com")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _response_text(response: Any) -> str:
    try:
        return str(response.text or "")
    except Exception:
        return ""


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
    ):
        self._log = log_fn
        self._cookie_header = _cookie_header_from_any(cookie_header or cookies)
        self.access_token = str(access_token or "").strip()
        self.login_state = str(login_state or "").strip()
        self.login_identifier_url = str(login_identifier_url or "").strip()
        self.challenge_url = str(challenge_url or "").strip()
        self.login_identifier_form: dict[str, str] = {}
        self.challenge_form: dict[str, str] = {}
        self.return_url = str(return_url or QUICKFRAME_RETURN_URL).strip()
        proxies = {"http": proxy, "https": proxy} if proxy else None
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

    def log(self, message: str) -> None:
        self._log(message)

    def _seed_cookies(self, cookies: Any) -> None:
        if not isinstance(cookies, list):
            return
        for item in cookies:
            if not isinstance(item, dict):
                continue
            pair = _valid_cookie_pair(item.get("name"), item.get("value"))
            if not pair:
                continue
            domain = str(item.get("domain") or "").strip() or ".quickframe.com"
            path = str(item.get("path") or "/").strip() or "/"
            try:
                self.s.cookies.set(pair[0], pair[1], domain=domain, path=path)
            except Exception:
                continue

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
    ) -> dict[str, str]:
        headers = {
            "accept": accept,
            "accept-language": "zh-HK,zh;q=0.9,en;q=0.8",
            "referer": referer,
            "sec-ch-ua": QUICKFRAME_SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "user-agent": QUICKFRAME_USER_AGENT,
        }
        if content_type:
            headers["content-type"] = content_type
        if origin:
            headers["origin"] = origin
        if authorization:
            headers["authorization"] = authorization
        if include_cookie:
            cookie_header = self.cookie_header()
            if cookie_header:
                headers["cookie"] = cookie_header
        return headers

    def start_login(self, email: str, *, return_url: str | None = None, screen_hint: str = "") -> dict[str, Any]:
        email = str(email or "").strip()
        if not email:
            raise RuntimeError("QuickFrame login requires email")
        target_return_url = str(return_url or self.return_url or QUICKFRAME_RETURN_URL).strip()
        params = {"returnUrl": target_return_url, "login_hint": email}
        if screen_hint:
            params["screen_hint"] = str(screen_hint)
        url = f"{QUICKFRAME_SERVER_BASE}/auth/login?{urlencode(params)}"
        response = self.s.get(
            url,
            headers=self._headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                referer=target_return_url,
                include_cookie=True,
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
        response = self.s.post(
            identifier_url,
            headers=self._headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                content_type="application/x-www-form-urlencoded",
                origin=QUICKFRAME_LOGIN_BASE,
                referer=identifier_url,
                include_cookie=True,
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
            ),
            allow_redirects=False,
        )
        self.log(f"GET /u/login/passwordless-email-challenge -> {challenge.status_code}")
        if challenge.status_code != 200:
            raise RuntimeError(f"QuickFrame passwordless challenge page failed: HTTP {challenge.status_code}")
        html = _response_text(challenge)
        action, fields = _extract_form(html, "code")
        self.challenge_form = fields
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
        response = self.s.post(
            self.challenge_url,
            headers=self._headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                content_type="application/x-www-form-urlencoded",
                origin=QUICKFRAME_LOGIN_BASE,
                referer=self.challenge_url,
                include_cookie=True,
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
            response = self.s.get(
                current_url,
                headers=self._headers(
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    origin=QUICKFRAME_LOGIN_BASE if "login.quickframe.com" in current_referer else "",
                    referer=current_referer,
                    include_cookie=True,
                ),
                allow_redirects=False,
            )
            self.log(f"GET {urlparse(current_url).netloc}{urlparse(current_url).path} -> {response.status_code}")
            if _is_redirect(response.status_code):
                location = str(response.headers.get("location") or "").strip()
                if not location:
                    return
                current_referer = current_url
                current_url = urljoin(current_url, location)
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
        session = session_info.get("session") if isinstance(session_info.get("session"), dict) else {}
        active = bool(session.get("active") or str(session.get("status") or "").lower() == "active")
        token_info: dict[str, Any] = {}
        if active and (force_refresh or not self.access_token):
            token_info = self.issue_token()
        elif self.access_token:
            token_info = {"accessToken": self.access_token, "tokenType": "Bearer", "expiresIn": 0}
        if not self.access_token:
            raise RuntimeError("QuickFrame active session did not produce an access token")

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
    active = bool(session.get("active") or str(session.get("status") or "").lower() == "active")
    user_id = str(check_session.get("id") or check_session.get("actualUserId") or "").strip()
    workspace_id = str(check_session.get("workspaceId") or "").strip()
    email = str(
        check_session.get("email")
        or session_user.get("email")
        or session_user.get("primaryEmailAddress")
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
