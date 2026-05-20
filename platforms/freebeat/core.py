"""Freebeat email login, rewards and account state protocol client."""
from __future__ import annotations

import json
import math
import re
from http.cookiejar import Cookie
from datetime import datetime, timezone
from typing import Any, Callable

from curl_cffi.requests import Session

from core.base_platform import Account


FREEBEAT_BASE = "https://freebeat.ai"
FREEBEAT_UPLOAD_BASE = "https://api.freebeatfit.com"
FREEBEAT_REGISTER_REFERER = f"{FREEBEAT_BASE}/zh/ai-video-generator"
FREEBEAT_SEND_CODE_PATH = "/api/proxy/v1/user/com/sendEmailVerifyCodeV2"
FREEBEAT_DEFAULT_VERIFY_SOURCE = "WEB_SHOPIFY_LOGIN"
FREEBEAT_DEFAULT_NEXT_ACTION = "40284e1e63e50bc18b2033770e8fa1412662d607d8"
FREEBEAT_ONBOARDING_CODE = "onboarding_v1"
FREEBEAT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
FREEBEAT_SEC_CH_UA = '"Not/A)Brand";v="99", "Chromium";v="148"'

DEFAULT_ONBOARDING_ANSWERS = [
    {"questionKey": "q1_describe_you", "options": ["content_creator"]},
    {"questionKey": "q2_hear_about", "options": ["google"]},
    {"questionKey": "q3_use_for", "options": ["client_projects"]},
]

FREEBEAT_COOKIE_DOMAINS = ("freebeat.ai", "freebeatfit.com")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


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
        for name, cookie_value in iterable:
            if isinstance(cookie_value, (dict, list)):
                continue
            pair = _valid_cookie_pair(name, cookie_value)
            if pair:
                pairs.append(f"{pair[0]}={pair[1]}")
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            pair = _valid_cookie_pair(item.get("name"), item.get("value"))
            if pair:
                pairs.append(f"{pair[0]}={pair[1]}")
    return "; ".join(dict.fromkeys(pairs))


def _cookie_records_to_header(records: list[dict[str, Any]]) -> str:
    return _cookie_header_from_any(records)


def _auth_token_cookie(token: str) -> str:
    pair = _valid_cookie_pair("authToken", token)
    return f"{pair[0]}={pair[1]}" if pair else ""


def _clip(value: Any, left: int = 10, right: int = 6) -> str:
    text = str(value or "").strip()
    if len(text) <= left + right + 3:
        return text
    return f"{text[:left]}...{text[-right:]}"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _ms_to_iso(value: Any) -> str:
    raw = _safe_int(value)
    if raw <= 0:
        return ""
    seconds = raw / 1000 if raw > 10_000_000_000 else raw
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return ""


def _days_until_ms(value: Any) -> int | None:
    raw = _safe_int(value)
    if raw <= 0:
        return None
    seconds = raw / 1000 if raw > 10_000_000_000 else raw
    return max(math.floor((seconds - datetime.now(timezone.utc).timestamp()) / 86400), 0)


def _email_domain(email: Any) -> str:
    text = str(email or "").strip()
    if "@" not in text:
        return ""
    return text.rsplit("@", 1)[1].strip().lower()


def _response_text(response: Any) -> str:
    try:
        return str(response.text or "")
    except Exception:
        return ""


def _json_from_response(response: Any) -> dict[str, Any]:
    text = _response_text(response).strip()
    if not text:
        return {"code": 0, "msg": "", "data": None}
    try:
        payload = response.json()
    except Exception:
        payload = json.loads(text)
    if isinstance(payload, dict):
        return payload
    return {"code": 0, "msg": "", "data": payload}


def _validate_api_payload(payload: dict[str, Any], *, label: str) -> dict[str, Any]:
    if "code" not in payload:
        return payload
    code = payload.get("code")
    if code in (0, "0", None):
        return payload
    msg = str(payload.get("msg") or payload.get("message") or "").strip()
    snippet = _json_dumps(payload)[:300]
    raise RuntimeError(f"Freebeat {label} rejected: code={code} msg={msg or snippet}")


def _is_send_code_already_sent(payload: dict[str, Any]) -> bool:
    code = payload.get("code")
    msg = str(payload.get("msg") or payload.get("message") or "").strip().lower()
    return code in (409, "409") and "already" in msg and "sent" in msg


def _extract_login_payload(text: str) -> dict[str, Any]:
    """Parse Next.js text/x-component response and return the login result object."""
    raw = str(text or "").strip()
    if not raw:
        raise RuntimeError("Freebeat login returned an empty response")

    candidates: list[Any] = []
    try:
        candidates.append(json.loads(raw))
    except Exception:
        pass

    for line in raw.splitlines():
        _, sep, tail = line.partition(":")
        if not sep:
            continue
        payload = tail.strip()
        if not payload.startswith("{"):
            continue
        try:
            candidates.append(json.loads(payload))
        except Exception:
            continue

    for item in candidates:
        if not isinstance(item, dict):
            continue
        if "code" in item:
            _validate_api_payload(item, label="WebLogin")
            data = item.get("data")
            if isinstance(data, dict) and (
                data.get("token") or data.get("accessToken") or data.get("deviceToken")
            ):
                return item
            if item.get("code") in (0, "0") and isinstance(data, dict):
                return item

    snippet = raw[:500].replace("\n", "\\n")
    raise RuntimeError(f"Freebeat login response did not include token payload: {snippet}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _credit_value(credits: dict[str, Any], key: str) -> int:
    return _safe_int(credits.get(key), 0)


def summarize_freebeat_account_state(state: dict[str, Any], *, fallback_email: str = "") -> dict[str, Any]:
    login = dict(state.get("login") or {})
    login_data = dict(login.get("data") or login)
    credits = dict(state.get("credits") or {})
    signin = dict(state.get("signin_status") or state.get("signin") or {})
    token = str(state.get("token") or login_data.get("token") or login_data.get("accessToken") or "").strip()
    user_id = str(state.get("user_id") or login_data.get("userId") or login_data.get("user_id") or "").strip()
    expire_time = login_data.get("expireTime") or state.get("expire_time") or state.get("token_expire_time_ms")

    total_credits = _credit_value(credits, "totalCredits")
    free_credits = _credit_value(credits, "free")
    boost_credits = _credit_value(credits, "boost")
    event_credits = _credit_value(credits, "event")
    membership_credits = _credit_value(credits, "membership")
    subscription_type = str(credits.get("userSubscriptionType") or login_data.get("member") or "0").strip()
    is_member = _boolish(login_data.get("vip")) or membership_credits > 0 or subscription_type not in {"", "0", "free"}
    plan_name = "Member" if is_member else "Free"
    plan_state = "subscribed" if is_member else "free"
    signed_today = bool(signin.get("signedToday"))
    can_sign_in = bool(signin.get("canSignIn"))
    token_expire_at = _ms_to_iso(expire_time)
    token_expire_days = _days_until_ms(expire_time)

    chips = [f"积分 {total_credits}"]
    chips.append("今日已签到" if signed_today else "今日未签到")
    if token_expire_days is not None:
        chips.append(f"Token {token_expire_days}天")

    summary: dict[str, Any] = {
        "valid": bool(token and (credits or state.get("valid", True))),
        "email": str(state.get("email") or login_data.get("email") or fallback_email or "").strip(),
        "remote_email": str(state.get("email") or fallback_email or "").strip(),
        "user_id": user_id,
        "account_id": user_id,
        "plan": plan_name,
        "plan_name": plan_name,
        "plan_state": plan_state,
        "remaining_credits": str(total_credits),
        "total_credits": total_credits,
        "free_credits": free_credits,
        "boost_credits": boost_credits,
        "event_credits": event_credits,
        "membership_credits": membership_credits,
        "user_subscription_type": subscription_type,
        "signed_today": signed_today,
        "can_sign_in": can_sign_in,
        "next_refresh_at": signin.get("nextRefreshAt") or "",
        "next_refresh_at_iso": _ms_to_iso(signin.get("nextRefreshAt")),
        "server_utc_date": str(signin.get("serverUtcDate") or ""),
        "token_expire_time_ms": _safe_int(expire_time),
        "token_expire_at": token_expire_at,
        "token_expire_in_days": token_expire_days,
        "last_keepalive_at": str(state.get("last_keepalive_at") or ""),
        "account_state_partial": bool(state.get("account_state_partial")),
        "account_state_error": str(state.get("account_state_error") or ""),
        "last_questionnaire_status": str(state.get("last_questionnaire_status") or ""),
        "last_daily_sign_in_status": str(state.get("last_daily_sign_in_status") or ""),
        "credits": credits,
        "signin": signin,
        "chips": chips,
    }
    summary["account_overview"] = {
        key: value
        for key, value in summary.items()
        if key not in {"account_overview", "credits", "signin"}
    }
    return summary


def partial_freebeat_account_state(token: str, *, client: Any = None, error: Any = "") -> dict[str, Any]:
    state: dict[str, Any] = {
        "token": str(token or "").strip(),
        "credits": {},
        "signin_status": {},
        "credits_payload": {},
        "signin_payload": {},
        "last_keepalive_at": _now_iso(),
        "account_state_partial": True,
        "account_state_error": str(error or ""),
        "valid": True,
    }
    if client is not None and hasattr(client, "auth_state"):
        try:
            state.update(client.auth_state())
        except Exception:
            pass
    return state


def extract_freebeat_account_context(account: Account | Any) -> dict[str, str]:
    extra = dict(getattr(account, "extra", {}) or {})
    overview = extra.get("account_overview") if isinstance(extra.get("account_overview"), dict) else {}
    legacy_extra = overview.get("legacy_extra") if isinstance(overview.get("legacy_extra"), dict) else {}
    token = (
        extra.get("access_token")
        or extra.get("accessToken")
        or extra.get("token")
        or extra.get("device_token")
        or extra.get("legacy_token")
        or getattr(account, "token", "")
        or ""
    )
    return {
        "token": str(token or "").strip(),
        "access_token": str(extra.get("access_token") or extra.get("accessToken") or token or "").strip(),
        "device_token": str(extra.get("device_token") or extra.get("deviceToken") or "").strip(),
        "user_id": str(extra.get("user_id") or extra.get("userId") or getattr(account, "user_id", "") or "").strip(),
        "email": str(extra.get("email") or getattr(account, "email", "") or "").strip(),
        "expire_time": str(extra.get("expire_time") or extra.get("expireTime") or "").strip(),
        "cookies": _cookie_header_from_any(
            extra.get("cookies")
            or extra.get("cookie_header")
            or extra.get("freebeat_cookies")
            or legacy_extra.get("cookies")
            or legacy_extra.get("cookie_header")
            or overview.get("cookies")
            or overview.get("cookie_header")
        ),
    }


class FreebeatClient:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        log_fn: Callable[[str], None] = print,
        cookie_header: str = "",
        cookies: Any = None,
    ):
        self._log = log_fn
        self._cookie_header = _cookie_header_from_any(cookie_header or cookies)
        proxies = {"http": proxy, "https": proxy} if proxy else None
        self.s = Session(impersonate="chrome", proxies=proxies, timeout=30)
        self.s.headers.update(
            {
                "accept": "*/*",
                "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
                "referer": FREEBEAT_REGISTER_REFERER,
                "sec-ch-ua": FREEBEAT_SEC_CH_UA,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "fb-language": "en",
                "x-platform-type": "web",
                "user-agent": FREEBEAT_USER_AGENT,
            }
        )

    def log(self, message: str) -> None:
        self._log(message)

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
            if domain and not any(domain.lstrip(".").endswith(item) for item in FREEBEAT_COOKIE_DOMAINS):
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
        header = _cookie_records_to_header(self.cookie_records())
        return header or self._cookie_header

    def auth_state(self) -> dict[str, Any]:
        cookie_header = self.cookie_header()
        return {
            "cookies": cookie_header,
            "cookie_header": cookie_header,
            "freebeat_cookies": self.cookie_records(),
        }

    def _url(self, path_or_url: str, *, base: str = FREEBEAT_BASE) -> str:
        raw = str(path_or_url or "").strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        if not raw.startswith("/"):
            raw = f"/{raw}"
        return f"{base}{raw}"

    def _frontend_headers(
        self,
        *,
        accept: str = "application/json, text/plain, */*",
        content_type: str = "",
        token: str = "",
        include_cookie: bool = True,
        include_fetch_headers: bool = True,
    ) -> dict[str, str]:
        request_headers = {
            "accept": accept,
            "referer": FREEBEAT_REGISTER_REFERER,
            "sec-ch-ua": FREEBEAT_SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "user-agent": FREEBEAT_USER_AGENT,
        }
        if include_fetch_headers:
            request_headers["fb-language"] = "en"
            request_headers["x-platform-type"] = "web"
        if content_type:
            request_headers["content-type"] = content_type
        if token:
            request_headers["Authorization"] = token
            request_headers["token"] = token
            request_headers["udt"] = token
        if include_cookie:
            cookie_header = self.cookie_header() or _auth_token_cookie(token)
            if cookie_header:
                request_headers["cookie"] = cookie_header
        return request_headers

    def _warmup_frontend_session(self) -> None:
        try:
            response = self.s.get(
                FREEBEAT_REGISTER_REFERER,
                headers=self._frontend_headers(
                    accept=(
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "image/avif,image/webp,image/apng,*/*;q=0.8"
                    ),
                    include_cookie=False,
                    include_fetch_headers=False,
                ),
            )
            self.log(f"GET /zh/ai-video-generator warmup -> {response.status_code}")
        except Exception as exc:
            self.log(f"Freebeat frontend warmup failed: {exc}")

    def _api_json(
        self,
        method: str,
        path_or_url: str,
        *,
        json_body: Any = None,
        token: str = "",
        headers: dict[str, str] | None = None,
        label: str = "api",
        base: str = FREEBEAT_BASE,
        validate_code: bool = True,
    ) -> dict[str, Any]:
        request_headers = self._frontend_headers(
            content_type="application/json" if json_body is not None else "",
            token=token,
        )
        if headers:
            request_headers.update(headers)
        data = _json_dumps(json_body) if json_body is not None else None
        url = self._url(path_or_url, base=base)
        response = None
        for attempt in range(2):
            response = self.s.request(
                method.upper(),
                url,
                headers=request_headers,
                data=data,
            )
            if response.status_code == 403 and attempt == 0 and base == FREEBEAT_BASE:
                self.log(f"{method.upper()} {path_or_url} -> 403, warming up frontend session and retrying")
                self._warmup_frontend_session()
                continue
            break
        self.log(f"{method.upper()} {path_or_url} -> {response.status_code}")
        if response.status_code != 200:
            snippet = _response_text(response)[:300]
            raise RuntimeError(f"Freebeat {label} failed: HTTP {response.status_code} {snippet}")
        payload = _json_from_response(response)
        return _validate_api_payload(payload, label=label) if validate_code else payload

    def send_email_verify_code(
        self,
        email: str,
        *,
        verify_source: str = FREEBEAT_DEFAULT_VERIFY_SOURCE,
    ) -> dict[str, Any]:
        payload = {"email": str(email).strip(), "verifySource": str(verify_source or FREEBEAT_DEFAULT_VERIFY_SOURCE)}
        domain = _email_domain(email)
        label = f"sendEmailVerifyCodeV2 domain={domain}" if domain else "sendEmailVerifyCodeV2"
        result = self._api_json("POST", FREEBEAT_SEND_CODE_PATH, json_body=payload, label=label, validate_code=False)
        if _is_send_code_already_sent(result):
            self.log("Freebeat login code was already sent; continuing to wait for mailbox code")
            return result
        return _validate_api_payload(result, label=label)

    def verify_email_code(
        self,
        email: str,
        code: str,
        *,
        next_action: str | None = None,
        next_router_state_tree: str | None = None,
    ) -> dict[str, Any]:
        email = str(email or "").strip()
        code = str(code or "").strip()
        if not email:
            raise RuntimeError("Freebeat login requires email")
        if not re.fullmatch(r"\d{4,8}", code):
            raise RuntimeError(f"Freebeat email verification code is invalid: {code!r}")

        action_id = str(next_action or FREEBEAT_DEFAULT_NEXT_ACTION).strip()
        headers = {
            "accept": "text/x-component",
            "content-type": "text/plain;charset=UTF-8",
            "origin": FREEBEAT_BASE,
            "referer": FREEBEAT_REGISTER_REFERER,
            "next-action": action_id,
        }
        if next_router_state_tree:
            headers["next-router-state-tree"] = str(next_router_state_tree)
        response = self.s.post(
            FREEBEAT_REGISTER_REFERER,
            headers=headers,
            data=_json_dumps([{"email": email, "code": code}]),
        )
        self.log(f"POST /zh/ai-video-generator WebLogin -> {response.status_code}")
        if response.status_code != 200:
            snippet = _response_text(response)[:500]
            raise RuntimeError(f"Freebeat WebLogin failed: HTTP {response.status_code} {snippet}")
        payload = _extract_login_payload(_response_text(response))
        data = dict(payload.get("data") or {})
        token = str(data.get("token") or data.get("accessToken") or data.get("deviceToken") or "").strip()
        if not token:
            raise RuntimeError(f"Freebeat WebLogin did not return token: {_json_dumps(payload)[:300]}")
        self.log(f"Freebeat WebLogin ok: user={data.get('userId', '')} token={_clip(token)}")
        return payload

    def find_credits(self, token: str) -> dict[str, Any]:
        return self._api_json("GET", "/api/proxy/v1/user/credits/findCredits", token=token, label="findCredits")

    def signin_status(self, token: str) -> dict[str, Any]:
        return self._api_json("GET", "/api/proxy/v1/user/signin/status", token=token, label="signin/status")

    def signin_submit(self, token: str) -> dict[str, Any]:
        return self._api_json("POST", "/api/proxy/v1/user/signin/submit", json_body={}, token=token, label="signin/submit")

    def questionnaire_check(
        self,
        token: str,
        *,
        questionnaire_code: str = FREEBEAT_ONBOARDING_CODE,
    ) -> dict[str, Any]:
        path = f"/api/proxy/v1/user/questionnaire/check?questionnaireCode={questionnaire_code}"
        return self._api_json("GET", path, token=token, label="questionnaire/check")

    def questionnaire_submit(
        self,
        token: str,
        *,
        questionnaire_code: str = FREEBEAT_ONBOARDING_CODE,
        answers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "questionnaireCode": questionnaire_code,
            "source": "TRIGGER",
            "triggerEvent": "register_success",
            "answers": answers or DEFAULT_ONBOARDING_ANSWERS,
        }
        return self._api_json("POST", "/api/proxy/v1/user/questionnaire/submit", json_body=payload, token=token, label="questionnaire/submit")

    def claim_questionnaire(
        self,
        token: str,
        *,
        questionnaire_code: str = FREEBEAT_ONBOARDING_CODE,
        answers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        check = self.questionnaire_check(token, questionnaire_code=questionnaire_code)
        try:
            submitted = self.questionnaire_submit(
                token,
                questionnaire_code=questionnaire_code,
                answers=answers,
            )
        except RuntimeError as exc:
            message = str(exc)
            lowered = message.lower()
            if any(marker in lowered for marker in ("already", "duplicate", "submitted", "complete", "已", "重复")):
                return {
                    "status": "already_claimed",
                    "credits_granted": 0,
                    "check": check,
                    "message": message,
                }
            raise
        data = dict(submitted.get("data") or {})
        return {
            "status": "claimed",
            "credits_granted": _safe_int(data.get("creditsGranted")),
            "check": check,
            "submit": submitted,
        }

    def daily_sign_in(self, token: str) -> dict[str, Any]:
        before = self.signin_status(token)
        info = dict(before.get("data") or {})
        if not info.get("canSignIn"):
            return {
                "status": "already_signed" if info.get("signedToday") else "not_available",
                "reward_amount": 0,
                "before": before,
                "after": before,
            }
        submitted = self.signin_submit(token)
        submit_info = dict(submitted.get("data") or {})
        return {
            "status": "signed" if submit_info.get("granted") else "not_granted",
            "reward_amount": _safe_int(submit_info.get("rewardAmount")),
            "before": before,
            "after": submitted,
        }

    def get_rule_config(self, token: str = "") -> dict[str, Any]:
        return self._api_json("GET", "/api/proxy/v1/aiVideo/getRuleConfig", token=token, label="getRuleConfig")

    def get_model_rule_config(self, token: str, *, model_id: int = 101, business_type: int = 3) -> dict[str, Any]:
        payload = {"businessType": int(business_type), "modelId": int(model_id)}
        return self._api_json(
            "POST",
            "/api/proxy/v1/aiModelConfig/model/getModelRuleConfig",
            json_body=payload,
            token=token,
            label="getModelRuleConfig",
        )

    def fetch_account_state(self, token: str) -> dict[str, Any]:
        token = str(token or "").strip()
        if not token:
            raise RuntimeError("缺少 Freebeat token")
        credits_payload = self.find_credits(token)
        signin_payload = self.signin_status(token)
        state = {
            "token": token,
            "credits": dict(credits_payload.get("data") or {}),
            "signin_status": dict(signin_payload.get("data") or {}),
            "credits_payload": credits_payload,
            "signin_payload": signin_payload,
            "last_keepalive_at": _now_iso(),
        }
        state.update(self.auth_state())
        return state


def load_freebeat_account_state(
    account: Any,
    *,
    proxy: str | None = None,
    log_fn: Callable[[str], None] = print,
    force_refresh: bool = False,
    auto_sign_in: bool = False,
) -> dict[str, Any]:
    context = extract_freebeat_account_context(account)
    client = FreebeatClient(proxy=proxy, log_fn=log_fn, cookie_header=context["cookies"])
    token = context["token"]
    if not token:
        raise RuntimeError("缺少 Freebeat token，无法查询账号状态")

    daily_result: dict[str, Any] | None = None
    if auto_sign_in:
        daily_result = client.daily_sign_in(token)
    state = client.fetch_account_state(token)
    state.update(
        {
            "email": context["email"],
            "user_id": context["user_id"],
            "expire_time": context["expire_time"],
            "access_token": context["access_token"] or token,
            "device_token": context["device_token"],
            "force_refresh": bool(force_refresh),
        }
    )
    if daily_result:
        state["daily_sign_in"] = daily_result
        state["last_daily_sign_in_status"] = daily_result.get("status", "")
    state["summary"] = summarize_freebeat_account_state(state, fallback_email=context["email"])
    return state
