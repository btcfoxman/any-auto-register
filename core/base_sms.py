"""æŽ¥ç æœåŠ¡åŸºç±» + SMS-Activate / HeroSMS å®žçŽ°ã€‚"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import requests

logger = logging.getLogger(__name__)
_SMS_ACTIVE_NUMBER_LOCK = threading.Lock()
_SMS_ACTIVE_NUMBERS: set[str] = set()


def _sms_active_key(provider: str, phone: str) -> str:
    return f"{provider}:{str(phone or '').strip()}"


def _reserve_sms_number(provider: str, phone: str) -> bool:
    key = _sms_active_key(provider, phone)
    if key == f"{provider}:":
        return False
    with _SMS_ACTIVE_NUMBER_LOCK:
        if key in _SMS_ACTIVE_NUMBERS:
            return False
        _SMS_ACTIVE_NUMBERS.add(key)
        return True


def _release_sms_number(provider: str, phone: str) -> None:
    key = _sms_active_key(provider, phone)
    if key == f"{provider}:":
        return
    with _SMS_ACTIVE_NUMBER_LOCK:
        _SMS_ACTIVE_NUMBERS.discard(key)


@dataclass
class SmsActivation:
    """Represents an active phone number rental."""
    activation_id: str
    phone_number: str
    country: str = ""
    metadata: dict = field(default_factory=dict)


class BaseSmsProvider(ABC):
    """Base class for SMS verification code providers."""

    auto_report_success_on_code = True

    @abstractmethod
    def get_number(self, *, service: str, country: str = "") -> SmsActivation:
        """Rent a phone number for the given service."""
        ...

    @abstractmethod
    def get_code(self, activation_id: str, *, timeout: int = 120) -> str:
        """Wait for and return the SMS verification code."""
        ...

    @abstractmethod
    def cancel(self, activation_id: str) -> bool:
        """Cancel/release an activation. Returns True on success."""
        ...

    def report_success(self, activation_id: str) -> bool:
        """Report that the code was used successfully (optional)."""
        return True

    def set_resend_callback(self, callback: Callable[[], None] | None) -> None:
        """Optional hook used by providers that can request upstream resend."""
        return None

    def mark_code_failed(self, activation_id: str, reason: str = "") -> None:
        """Optional hook used when the target service rejects a received code."""
        return None

    def mark_send_failed(self, activation_id: str, reason: str = "") -> None:
        """Optional hook used when the target service rejects the rented phone."""
        return None

    def mark_send_succeeded(self, activation_id: str) -> None:
        """Optional hook used when the target service accepts the rented phone."""
        return None

    def get_reuse_info(self) -> dict:
        """Return provider-specific reuse state for task scheduling."""
        return {}


# ---------------------------------------------------------------------------
# SMS-Activate implementation (https://sms-activate.guru)
# ---------------------------------------------------------------------------

SMS_ACTIVATE_SERVICES = {
    "cursor": "ot",
    "chatgpt": "dr",
    "openai": "dr",
    "google": "go",
    "microsoft": "mg",
    "qq": "qq",
    "lingya_qq": "qq",
    "default": "ot",
}

SMS_ACTIVATE_COUNTRIES = {
    "ru": "0",
    "us": "187",
    "uk": "16",
    "in": "22",
    "id": "6",
    "ph": "4",
    "th": "52",
    "br": "73",
    "default": "0",
}


def _resolve_sms_activate_country_id(country: str, default_country: str) -> str:
    raw = str(country or default_country or "").strip().lower()
    if not raw:
        raw = "default"
    if raw.isdigit():
        return raw
    return SMS_ACTIVATE_COUNTRIES.get(raw, SMS_ACTIVATE_COUNTRIES["default"])


class SmsActivateProvider(BaseSmsProvider):
    """SMS-Activate (sms-activate.guru) provider."""

    BASE_URL = "https://api.sms-activate.guru/stubs/handler_api.php"

    def __init__(self, api_key: str, *, default_country: str = "", proxy: str = None):
        self.api_key = api_key
        self.default_country = default_country or "ru"
        self._proxy = {"http": proxy, "https": proxy} if proxy else None

    def _request(self, action: str, **params) -> str:
        params["api_key"] = self.api_key
        params["action"] = action
        resp = requests.get(
            self.BASE_URL,
            params=params,
            timeout=20,
            proxies=self._proxy,
        )
        resp.raise_for_status()
        return resp.text.strip()

    def get_balance(self) -> float:
        result = self._request("getBalance")
        if result.startswith("ACCESS_BALANCE:"):
            return float(result.split(":")[1])
        raise RuntimeError(f"SMS-Activate getBalance failed: {result}")

    def get_number(self, *, service: str, country: str = "") -> SmsActivation:
        service_code = SMS_ACTIVATE_SERVICES.get(service, SMS_ACTIVATE_SERVICES["default"])
        country_id = _resolve_sms_activate_country_id(country, self.default_country)

        result = self._request("getNumber", service=service_code, country=country_id)
        if result.startswith("ACCESS_NUMBER:"):
            parts = result.split(":")
            return SmsActivation(
                activation_id=parts[1],
                phone_number=parts[2],
                country=country or self.default_country,
            )

        if "NO_NUMBERS" in result:
            raise RuntimeError(f"SMS-Activate: 当前无可用号码 (service={service_code}, country={country_id})")
        if "NO_BALANCE" in result:
            raise RuntimeError("SMS-Activate: 余额不足")
        raise RuntimeError(f"SMS-Activate getNumber failed: {result}")

    def get_code(self, activation_id: str, *, timeout: int = 120) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._request("getStatus", id=activation_id)
            if result.startswith("STATUS_OK:"):
                code = result.split(":", 1)[1].strip()
                if _is_valid_sms_code(code):
                    return code
                logger.warning("SMS-Activate returned invalid SMS code for %s: %s", activation_id, code)
                time.sleep(3)
                continue
            if result == "STATUS_WAIT_CODE":
                time.sleep(3)
                continue
            if result == "STATUS_WAIT_RETRY":
                self._request("setStatus", id=activation_id, status="6")
                time.sleep(3)
                continue
            if result == "STATUS_CANCEL":
                return ""
            time.sleep(3)

        self.cancel(activation_id)
        return ""

    def cancel(self, activation_id: str) -> bool:
        result = self._request("setStatus", id=activation_id, status="8")
        return "ACCESS" in result

    def report_success(self, activation_id: str) -> bool:
        result = self._request("setStatus", id=activation_id, status="6")
        return "ACCESS" in result


# ---------------------------------------------------------------------------
# HeroSMS implementation (https://hero-sms.com/stubs/handler_api.php)
# ---------------------------------------------------------------------------

HERO_SMS_DEFAULT_SERVICE = "dr"
HERO_SMS_DEFAULT_COUNTRY = "187"
HERO_SMS_PHONE_LIFETIME = 20 * 60
_HERO_SMS_CACHE_LOCK = threading.Lock()
_HERO_SMS_VERIFY_LOCK = threading.RLock()
_HERO_SMS_CACHE: dict | None = None


def _project_data_dir() -> Path:
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def hero_sms_cache_file() -> Path:
    return _project_data_dir() / ".herosms_phone_cache.json"


def _hash_secret(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "å¦"}


def _normalize_hero_proxy(proxy: str | None) -> str | None:
    proxy = str(proxy or "").strip()
    if not proxy or proxy.startswith("singbox://"):
        return None
    return proxy


def _parse_hero_status_text(text: str) -> dict:
    text = str(text or "").strip()
    if text == "STATUS_WAIT_CODE":
        return {"status": "wait_code"}
    if text.startswith("STATUS_WAIT_RETRY"):
        return {"status": "wait_retry", "raw": text}
    if text == "STATUS_WAIT_RESEND":
        return {"status": "wait_resend"}
    if text.startswith("STATUS_OK:"):
        code = text.split(":", 1)[1].strip()
        if _is_valid_sms_code(code):
            return {"status": "ok", "code": code}
        return {"status": "wait_code", "raw": text}
    if text == "STATUS_CANCEL":
        return {"status": "cancel"}
    return {"status": "unknown", "raw": text}


def _is_valid_sms_code(code: Any) -> bool:
    return bool(re.fullmatch(r"\d{4,8}", str(code or "").strip()))


def _canonical_sms_event_fields(event_fields: dict | None) -> dict:
    event_fields = event_fields or {}
    canonical: dict[str, str] = {}
    channel = str(event_fields.get("channel") or "").strip()
    if channel:
        canonical["channel"] = channel
    sms_time = (
        event_fields.get("dateTime")
        or event_fields.get("date")
        or event_fields.get("smsDate")
        or event_fields.get("smsTime")
        or ""
    )
    if sms_time:
        canonical["time"] = str(sms_time)
    text = event_fields.get("text") or event_fields.get("smsText")
    if text:
        canonical["text"] = str(text)
    if channel == "call":
        for key in ("from", "url"):
            if event_fields.get(key):
                canonical[key] = str(event_fields[key])
    if not sms_time:
        for key in ("repeated", "activationStatus", "verificationType"):
            if event_fields.get(key) is not None:
                canonical[key] = str(event_fields[key])
    return canonical


def _has_real_sms_time(event_fields: dict | None) -> bool:
    raw_time = (
        (event_fields or {}).get("dateTime")
        or (event_fields or {}).get("date")
        or (event_fields or {}).get("smsDate")
        or (event_fields or {}).get("smsTime")
        or ""
    )
    raw_time = str(raw_time).strip()
    return bool(raw_time and raw_time not in {"0", "0000-00-00 00:00:00", "0000-00-00T00:00:00"})


def _sms_event_key(activation_id: str, code: str, event_fields: dict | None) -> str:
    identity = {"activation_id": str(activation_id), "code": str(code)}
    identity.update(_canonical_sms_event_fields(event_fields))
    raw = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _make_sms_candidate(activation_id: str, source: str, code, event_fields: dict | None = None) -> dict | None:
    code = str(code or "").strip()
    if not _is_valid_sms_code(code):
        return None
    canonical = _canonical_sms_event_fields(event_fields)
    sms_key = _sms_event_key(activation_id, code, event_fields) if event_fields else ""
    return {
        "status": "ok",
        "code": code,
        "source": source,
        "sms_key": sms_key,
        "sms_time": canonical.get("time", ""),
        "sms_text": canonical.get("text", ""),
        "allow_same_code": _has_real_sms_time(event_fields),
    }


def _candidate_is_attempted(candidate: dict, used_codes: set, attempted_sms_keys: set) -> bool:
    sms_key = str(candidate.get("sms_key") or "")
    code = str(candidate.get("code") or "")
    if sms_key and sms_key in attempted_sms_keys:
        return True
    return bool(code in used_codes and not candidate.get("allow_same_code"))


class HeroSmsProvider(BaseSmsProvider):
    """HeroSMS provider with resend, SMS event dedupe, and short-lived phone reuse."""

    BASE_URL = "https://hero-sms.com/stubs/handler_api.php"
    auto_report_success_on_code = False

    def __init__(
        self,
        api_key: str,
        *,
        default_service: str = HERO_SMS_DEFAULT_SERVICE,
        default_country: str = HERO_SMS_DEFAULT_COUNTRY,
        max_price: float = -1,
        proxy: str | None = None,
        reuse_phone_to_max: bool = True,
        phone_success_max: int = 3,
    ):
        self.api_key = str(api_key or "").strip()
        self.default_service = str(default_service or HERO_SMS_DEFAULT_SERVICE).strip()
        self.default_country = str(default_country or HERO_SMS_DEFAULT_COUNTRY).strip()
        self.max_price = float(max_price or -1)
        self.proxy = _normalize_hero_proxy(proxy)
        self.proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        self.reuse_phone_to_max = bool(reuse_phone_to_max)
        self.phone_success_max = max(0, int(phone_success_max or 0))
        self.openai_resend_callback: Callable[[], None] | None = None
        self.last_code_result: dict | None = None
        self.current_activation: SmsActivation | None = None

    def _request(self, params: dict, *, needs_key: bool = True, timeout: int = 30) -> requests.Response:
        payload = dict(params)
        if needs_key:
            payload["api_key"] = self.api_key
        resp = requests.get(self.BASE_URL, params=payload, timeout=timeout, proxies=self.proxies)
        resp.raise_for_status()
        return resp

    def get_balance(self) -> float:
        text = self._request({"action": "getBalance"}).text.strip()
        if text.startswith("ACCESS_BALANCE:"):
            return float(text.split(":", 1)[1])
        raise RuntimeError(f"HeroSMS getBalance failed: {text}")

    def get_services(self, country: str | int | None = None, lang: str = "cn") -> list:
        params = {"action": "getServicesList", "lang": lang}
        if country not in (None, ""):
            params["country"] = country
        data = self._request(params, needs_key=False).json()
        if isinstance(data, dict) and data.get("status") == "success":
            return list(data.get("services") or [])
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # å¯èƒ½æ˜¯ {"dr": {"name": "OpenAI", ...}, ...} æ ¼å¼
            result = []
            for key, value in data.items():
                if key in ("status", "message", "error"):
                    continue
                if isinstance(value, dict):
                    if "code" not in value:
                        value["code"] = key
                    result.append(value)
                elif isinstance(value, str):
                    result.append({"code": key, "name": value})
            if result:
                return result
        raise RuntimeError("HeroSMS getServicesList returned unexpected response")

    def get_countries(self) -> list:
        data = self._request({"action": "getCountries"}, needs_key=False).json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # æ£€æŸ¥æ˜¯å¦æ˜¯é”™è¯¯å“åº” {"status":0,"message":"No access","data":[]}
            if data.get("status") == 0 or data.get("message") == "No access":
                raise RuntimeError(f"SMS API access denied: {data.get('message', 'unknown')}")
            # HeroSMS å¯èƒ½è¿”å›ž {"0": {"id": 0, "eng": "Russia"}, ...} æ ¼å¼
            result = []
            for key, value in data.items():
                if key in ("status", "message", "data", "error"):
                    continue
                if isinstance(value, dict):
                    if "id" not in value:
                        value["id"] = key
                    result.append(value)
                elif isinstance(value, str):
                    result.append({"id": key, "eng": value, "name": value})
            if result:
                return result
        raise RuntimeError("SMS getCountries returned unexpected response")

    def get_prices(self, service: str | None = None, country: str | int | None = None) -> dict:
        params = {"action": "getPrices"}
        if service:
            params["service"] = service
        if country not in (None, ""):
            params["country"] = country
        data = self._request(params).json()
        if isinstance(data, dict):
            return data
        raise RuntimeError("HeroSMS getPrices returned unexpected response")

    def get_top_countries(self, service: str | None = None) -> list[dict]:
        """èŽ·å–æŒ‡å®šæœåŠ¡æŒ‰ä»·æ ¼æŽ’åºçš„å›½å®¶åˆ—è¡¨ï¼ˆå«ä»·æ ¼å’Œåº“å­˜ï¼‰ã€‚

        ä¼˜å…ˆä½¿ç”¨ getTopCountriesByServiceRank APIï¼Œé™çº§åˆ° getPrices å…¨é‡è§£æžã€‚
        è¿”å›žæ ¼å¼: [{"country": "66", "name": "Thailand", "price": 0.12, "count": 150}, ...]
        """
        service_code = str(service or self.default_service or HERO_SMS_DEFAULT_SERVICE).strip()

        # ç­–ç•¥1: ä½¿ç”¨ getTopCountriesByServiceRankï¼ˆHeroSMS ä¸“ç”¨æŽ’åæŽ¥å£ï¼‰
        for action in ("getTopCountriesByServiceRank", "getTopCountriesByService"):
            try:
                data = self._request({"action": action, "service": service_code}).json()
                rows = self._parse_top_countries_response(data)
                if rows:
                    rows.sort(key=lambda r: (r.get("price") or 999, -(r.get("count") or 0)))
                    return rows
            except Exception:
                continue

        # ç­–ç•¥2: ä»Ž getPrices å…¨é‡æ•°æ®ä¸­è§£æž
        try:
            prices = self.get_prices(service=service_code)
            rows = []
            for country_id, services in prices.items():
                if not isinstance(services, dict):
                    continue
                svc_data = services.get(service_code)
                if not isinstance(svc_data, dict):
                    continue
                price = svc_data.get("cost") or svc_data.get("price")
                count = svc_data.get("count") or svc_data.get("qty") or svc_data.get("available")
                try:
                    price = float(price) if price is not None else None
                except (TypeError, ValueError):
                    price = None
                try:
                    count = int(count) if count is not None else 0
                except (TypeError, ValueError):
                    count = 0
                if price is not None and count > 0:
                    rows.append({"country": str(country_id), "price": price, "count": count})
            rows.sort(key=lambda r: (r.get("price") or 999, -(r.get("count") or 0)))
            return rows
        except Exception:
            return []

    def _parse_top_countries_response(self, data) -> list[dict]:
        """è§£æž getTopCountriesByServiceRank å“åº”ã€‚"""
        rows = []
        items = data
        # å¯èƒ½åµŒå¥—åœ¨ data/result é”®ä¸‹
        if isinstance(data, dict):
            items = data.get("data") or data.get("result") or data.get("response") or data
        if isinstance(items, dict):
            # {country_id: {price, count, ...}} æ ¼å¼
            for key, value in items.items():
                if not isinstance(value, dict):
                    continue
                try:
                    country_id = str(int(key))
                except (TypeError, ValueError):
                    continue
                price = value.get("price") or value.get("cost") or value.get("retail_price")
                count = value.get("count") or value.get("qty") or value.get("available") or value.get("stock")
                name = value.get("name") or value.get("countryName") or value.get("country_name") or ""
                try:
                    price = float(price) if price is not None else None
                except (TypeError, ValueError):
                    price = None
                try:
                    count = int(count) if count is not None else 0
                except (TypeError, ValueError):
                    count = 0
                if price is not None:
                    rows.append({"country": country_id, "name": str(name), "price": price, "count": count})
        elif isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                country_id = item.get("country") or item.get("countryId") or item.get("country_id") or item.get("id")
                if country_id is None:
                    continue
                price = item.get("price") or item.get("cost") or item.get("retail_price") or item.get("retailPrice")
                count = item.get("count") or item.get("qty") or item.get("available") or item.get("stock") or item.get("total")
                name = item.get("name") or item.get("countryName") or item.get("country_name") or item.get("title") or ""
                try:
                    price = float(price) if price is not None else None
                except (TypeError, ValueError):
                    price = None
                try:
                    count = int(count) if count is not None else 0
                except (TypeError, ValueError):
                    count = 0
                if price is not None:
                    rows.append({"country": str(country_id), "name": str(name), "price": price, "count": count})
        return rows

    def get_best_country(self, service: str | None = None, *, min_stock: int = 20, max_price: float = 0) -> str | None:
        """è‡ªåŠ¨é€‰æ‹©æœ€ä¼˜å›½å®¶ï¼šä»·æ ¼æœ€ä½Žä¸”åº“å­˜å……è¶³ã€‚

        Args:
            service: æœåŠ¡ä»£ç ï¼ˆé»˜è®¤ä½¿ç”¨ self.default_serviceï¼‰
            min_stock: æœ€ä½Žåº“å­˜è¦æ±‚ï¼ˆé»˜è®¤ 20ï¼‰
            max_price: æœ€é«˜ä»·æ ¼é™åˆ¶ï¼ˆ0 è¡¨ç¤ºä¸é™ï¼‰

        Returns:
            æœ€ä¼˜å›½å®¶ ID å­—ç¬¦ä¸²ï¼Œæˆ– Noneï¼ˆæ— å¯ç”¨å›½å®¶ï¼‰
        """
        # HeroSMS/SMSBower ä¸­å·²éªŒè¯å¯¹ OpenAI èµ° SMSï¼ˆéž WhatsAppï¼‰çš„å›½å®¶ç™½åå•
        # OpenAI 2025å¹´èµ·å¯¹ç»å¤§å¤šæ•°å›½å®¶æ”¹ç”¨ WhatsApp éªŒè¯
        # ç›®å‰åªæœ‰æ³°å›½ç¡®è®¤èµ° SMS
        ALLOWED_COUNTRIES = {
            "52",   # Thailand (å·²éªŒè¯èµ°SMS)
        }

        try:
            rows = self.get_top_countries(service=service)
        except Exception as exc:
            logger.warning("get_best_country æŸ¥è¯¢å¤±è´¥: %s", exc)
            return None

        if not rows:
            return None

        for row in rows:
            country_id = str(row.get("country") or "")
            if country_id not in ALLOWED_COUNTRIES:
                continue
            price = row.get("price") or 0
            count = row.get("count") or 0
            if count < min_stock:
                continue
            if max_price > 0 and price > max_price:
                continue
            return country_id

        # å¦‚æžœæ²¡æœ‰æ»¡è¶³ min_stock çš„ï¼Œæ”¾å®½åˆ° count > 0
        for row in rows:
            country_id = str(row.get("country") or "")
            if country_id not in ALLOWED_COUNTRIES:
                continue
            price = row.get("price") or 0
            count = row.get("count") or 0
            if count <= 0:
                continue
            if max_price > 0 and price > max_price:
                continue
            return country_id

        return None

    def _cache_identity(self, service: str, country: str) -> dict:
        return {
            "api_key_hash": _hash_secret(self.api_key),
            "service": str(service),
            "country": str(country),
        }

    def _load_cache(self, service: str, country: str) -> dict | None:
        global _HERO_SMS_CACHE
        if _HERO_SMS_CACHE is not None:
            cache = _HERO_SMS_CACHE
        else:
            path = hero_sms_cache_file()
            if not path.exists():
                return None
            try:
                cache = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        identity = self._cache_identity(service, country)
        if any(str(cache.get(key) or "") != str(value) for key, value in identity.items()):
            return None
        elapsed = time.time() - float(cache.get("acquired_at") or 0)
        if elapsed >= HERO_SMS_PHONE_LIFETIME or cache.get("reuse_stopped"):
            self._clear_cache()
            return None
        if self.phone_success_max > 0 and int(cache.get("use_count") or 0) >= self.phone_success_max:
            cache["reuse_stopped"] = True
            cache["stop_reason"] = f"success max reached ({self.phone_success_max})"
            self._save_cache(cache)
            return None
        cache["used_codes"] = set(cache.get("used_codes") or [])
        cache["attempted_sms_keys"] = set(cache.get("attempted_sms_keys") or [])
        _HERO_SMS_CACHE = cache
        return cache

    def _save_cache(self, cache: dict | None) -> None:
        global _HERO_SMS_CACHE
        _HERO_SMS_CACHE = cache
        path = hero_sms_cache_file()
        if cache is None:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            return
        serializable = dict(cache)
        serializable["used_codes"] = sorted(serializable.get("used_codes") or [])
        serializable["attempted_sms_keys"] = sorted(serializable.get("attempted_sms_keys") or [])
        serializable.pop("client", None)
        path.write_text(json.dumps(serializable, ensure_ascii=False), encoding="utf-8")

    def _clear_cache(self) -> None:
        self._save_cache(None)

    def _stop_reuse(self, reason: str) -> None:
        with _HERO_SMS_CACHE_LOCK:
            cache = _HERO_SMS_CACHE
            if not cache:
                return
            cache["reuse_stopped"] = True
            cache["stop_reason"] = reason
            self._save_cache(cache)

    def _request_number_raw(self, service: str, country: str) -> dict:
        common = {"service": service, "country": country}

        effective_max_price = self.max_price if self.max_price > 0 else 1
        if self.max_price > 0:
            try:
                prices = self.get_prices(service=service, country=country)
                country_prices = prices.get(str(country)) or prices.get(country) or {}
                service_prices = country_prices.get(service) or {}
                actual_cost = service_prices.get("cost") or service_prices.get("price")
                if actual_cost is not None:
                    dynamic_max = round(float(actual_cost) * 3, 4)
                    effective_max_price = min(self.max_price, max(dynamic_max, 0.2))
            except Exception:
                pass

        common["maxPrice"] = effective_max_price

        v2_error = ""
        try:
            resp = self._request({"action": "getNumberV2", **common})
            try:
                data = resp.json()
            except ValueError:
                data = None
            if isinstance(data, dict) and data.get("activationId"):
                return data
            v2_error = resp.text.strip()[:200]
        except Exception as exc:
            v2_error = str(exc)

        # å¦‚æžœ NO_NUMBERS ä¸” maxPrice ä½ŽäºŽç”¨æˆ·é…ç½®çš„ä¸Šé™ï¼Œæé«˜ maxPrice é‡è¯•
        if "NO_NUMBERS" in v2_error and self.max_price > 0 and effective_max_price < self.max_price:
            common["maxPrice"] = self.max_price
            try:
                resp = self._request({"action": "getNumberV2", **common})
                try:
                    data = resp.json()
                except ValueError:
                    data = None
                if isinstance(data, dict) and data.get("activationId"):
                    return data
                v2_error = resp.text.strip()[:200]
            except Exception as exc:
                v2_error = str(exc)

        try:
            text = self._request({"action": "getNumber", **common}).text.strip()
            if text.startswith("ACCESS_NUMBER:"):
                parts = text.split(":", 2)
                if len(parts) == 3:
                    return {
                        "activationId": parts[1],
                        "phoneNumber": parts[2],
                        "countryPhoneCode": "",
                        "activationCost": None,
                    }
            raise RuntimeError(text[:200])
        except Exception as exc:
            raise RuntimeError(f"HeroSMS èŽ·å–å·ç å¤±è´¥: V2={v2_error}; V1={exc}") from exc

    @staticmethod
    def _format_phone(number_info: dict) -> str:
        raw = str(number_info.get("phoneNumber") or "").strip()
        country_phone_code = str(number_info.get("countryPhoneCode") or "").strip()
        if raw.startswith("+"):
            return raw
        if country_phone_code and raw.startswith(country_phone_code):
            return f"+{raw}"
        if country_phone_code:
            return f"+{country_phone_code}{raw}"
        return f"+{raw}"

    def get_number(self, *, service: str, country: str = "") -> SmsActivation:
        service_code = str(self.default_service or service or HERO_SMS_DEFAULT_SERVICE).strip()
        country_id = str(country or self.default_country or HERO_SMS_DEFAULT_COUNTRY).strip()
        with _HERO_SMS_VERIFY_LOCK:
            with _HERO_SMS_CACHE_LOCK:
                cache = self._load_cache(service_code, country_id) if self.reuse_phone_to_max else None
                if cache:
                    activation = SmsActivation(
                        activation_id=str(cache["activation_id"]),
                        phone_number=str(cache["phone_number"]),
                        country=country_id,
                        metadata={"reused": True, "use_count": int(cache.get("use_count") or 0)},
                    )
                    self.current_activation = activation
                    return activation

                number_info = self._request_number_raw(service_code, country_id)
                activation_id = str(number_info.get("activationId") or "")
                phone = self._format_phone(number_info)
                if not activation_id or not phone.strip("+"):
                    raise RuntimeError("HeroSMS è¿”å›žçš„å·ç ä¿¡æ¯ä¸å®Œæ•´")
                cache = {
                    **self._cache_identity(service_code, country_id),
                    "activation_id": activation_id,
                    "phone_number": phone,
                    "acquired_at": time.time(),
                    "use_count": 0,
                    "used_codes": set(),
                    "attempted_sms_keys": set(),
                    "reuse_stopped": False,
                    "stop_reason": "",
                }
                self._save_cache(cache)
                activation = SmsActivation(
                    activation_id=activation_id,
                    phone_number=phone,
                    country=country_id,
                    metadata={"reused": False, "number_info": number_info},
                )
                self.current_activation = activation
                return activation

    def get_status(self, activation_id: str) -> dict:
        return _parse_hero_status_text(self._request({"action": "getStatus", "id": activation_id}).text)

    def get_status_v2(self, activation_id: str) -> dict:
        resp = self._request({"action": "getStatusV2", "id": activation_id})
        text = resp.text.strip()
        try:
            data = resp.json()
        except ValueError:
            return _parse_hero_status_text(text)
        if isinstance(data, str):
            return _parse_hero_status_text(data)
        if not isinstance(data, dict):
            return {"status": "unknown", "raw": data}
        raw_status = data.get("status")
        if isinstance(raw_status, str):
            parsed = _parse_hero_status_text(raw_status)
            if parsed.get("status") != "unknown":
                return parsed
        for channel in ("sms", "call"):
            item = data.get(channel)
            if isinstance(item, dict):
                candidate = _make_sms_candidate(
                    activation_id,
                    f"getStatusV2.{channel}",
                    item.get("code"),
                    {
                        "channel": channel,
                        "dateTime": item.get("dateTime"),
                        "text": item.get("text"),
                        "from": item.get("from"),
                        "url": item.get("url"),
                        "verificationType": data.get("verificationType"),
                    },
                )
                if candidate:
                    return candidate
        return {"status": "wait_code", "raw": data}

    def get_active_activations(self, start: int = 0, limit: int = 20) -> list:
        data = self._request({"action": "getActiveActivations", "start": start, "limit": limit}).json()
        if isinstance(data, dict) and "data" in data:
            return list(data.get("data") or [])
        return []

    def set_status(self, activation_id: str, status: int) -> str:
        return self._request({"action": "setStatus", "id": activation_id, "status": status}).text.strip()

    def cancel_activation(self, activation_id: str) -> bool:
        try:
            resp = self._request({"action": "cancelActivation", "id": activation_id})
            if resp.status_code == 204 or "ACCESS_CANCEL" in resp.text:
                return True
        except Exception:
            pass
        try:
            return "ACCESS_CANCEL" in self.set_status(activation_id, 8)
        except Exception:
            return False

    def finish_activation(self, activation_id: str) -> bool:
        try:
            resp = self._request({"action": "finishActivation", "id": activation_id})
            text = resp.text.strip()
            return resp.status_code in (200, 204) or "ACCESS" in text
        except Exception:
            try:
                return "ACCESS" in self.set_status(activation_id, 6)
            except Exception:
                return False

    def request_resend_sms(self, activation_id: str) -> bool:
        try:
            self.set_status(activation_id, 3)
            return True
        except Exception:
            return False

    def wait_for_code(self, activation_id: str, *, timeout: int = 180, poll_interval: int = 3) -> dict | None:
        deadline = time.time() + timeout
        start = time.time()
        last_hero_resend = start
        openai_resent = False
        warned_v2 = False
        while time.time() < deadline:
            with _HERO_SMS_CACHE_LOCK:
                cache = _HERO_SMS_CACHE or {}
                used_codes = set(cache.get("used_codes") or [])
                attempted_sms_keys = set(cache.get("attempted_sms_keys") or [])

            for source in ("v2", "v1", "active"):
                try:
                    candidate = None
                    if source == "v2":
                        result = self.get_status_v2(activation_id)
                        if result.get("status") == "cancel":
                            return None
                        if result.get("status") == "ok":
                            candidate = result
                    elif source == "v1":
                        result = self.get_status(activation_id)
                        if result.get("status") == "cancel":
                            return None
                        if result.get("status") == "ok":
                            candidate = _make_sms_candidate(activation_id, "getStatus", result.get("code"))
                    else:
                        for item in self.get_active_activations():
                            if str(item.get("activationId")) == str(activation_id):
                                candidate = _make_sms_candidate(
                                    activation_id,
                                    "getActiveActivations",
                                    item.get("smsCode"),
                                    {
                                        "channel": "sms",
                                        "smsText": item.get("smsText"),
                                        "activationStatus": item.get("activationStatus"),
                                        "repeated": item.get("repeated"),
                                        "dateTime": item.get("dateTime"),
                                        "date": item.get("date") or item.get("smsDate") or item.get("smsTime"),
                                    },
                                )
                                break
                    if candidate and not _candidate_is_attempted(candidate, used_codes, attempted_sms_keys):
                        return candidate
                except Exception as exc:
                    if source == "v2" and not warned_v2:
                        logger.warning("HeroSMS getStatusV2 failed: %s", exc)
                        warned_v2 = True
                    else:
                        logger.debug("HeroSMS status check failed via %s: %s", source, exc)

            elapsed = time.time() - start
            if not openai_resent and elapsed >= 90 and self.openai_resend_callback:
                try:
                    self.openai_resend_callback()
                except Exception as exc:
                    logger.warning("OpenAI phone resend callback failed: %s", exc)
                self.request_resend_sms(activation_id)
                last_hero_resend = time.time()
                openai_resent = True
            elif time.time() - last_hero_resend >= 30:
                self.request_resend_sms(activation_id)
                last_hero_resend = time.time()

            time.sleep(poll_interval)
        return None

    def get_code(self, activation_id: str, *, timeout: int = 120) -> str:
        wait_timeout = timeout
        with _HERO_SMS_CACHE_LOCK:
            cache = _HERO_SMS_CACHE or {}
            if cache and str(cache.get("activation_id")) == str(activation_id):
                remaining = int(HERO_SMS_PHONE_LIFETIME - (time.time() - float(cache.get("acquired_at") or 0)))
                wait_timeout = max(timeout, remaining, 60)
        candidate = self.wait_for_code(activation_id, timeout=wait_timeout)
        self.last_code_result = candidate
        return str((candidate or {}).get("code") or "")

    def cancel(self, activation_id: str) -> bool:
        try:
            return self.cancel_activation(activation_id)
        finally:
            with _HERO_SMS_CACHE_LOCK:
                cache = _HERO_SMS_CACHE
                if cache and str(cache.get("activation_id")) == str(activation_id):
                    self._clear_cache()

    def report_success(self, activation_id: str) -> bool:
        should_finish = False
        should_clear_cache = False
        handled_cached_activation = False
        with _HERO_SMS_CACHE_LOCK:
            cache = _HERO_SMS_CACHE
            if cache and str(cache.get("activation_id")) == str(activation_id):
                handled_cached_activation = True
                cache["use_count"] = int(cache.get("use_count") or 0) + 1
                self._record_last_attempt(cache, failed=False)
                remaining = HERO_SMS_PHONE_LIFETIME - (time.time() - float(cache.get("acquired_at") or 0))
                if not self.reuse_phone_to_max:
                    cache["reuse_stopped"] = True
                    cache["stop_reason"] = "reuse disabled"
                    should_finish = True
                    should_clear_cache = True
                elif self.phone_success_max > 0 and int(cache["use_count"]) >= self.phone_success_max:
                    cache["reuse_stopped"] = True
                    cache["stop_reason"] = f"success max reached ({self.phone_success_max})"
                    should_finish = True
                elif remaining <= 30:
                    cache["reuse_stopped"] = True
                    cache["stop_reason"] = "phone lifetime nearly expired"
                    should_finish = True
                    should_clear_cache = True
                self._save_cache(cache)
                if should_clear_cache:
                    self._clear_cache()
        if handled_cached_activation:
            if should_finish:
                self.finish_activation(activation_id)
            return True
        return self.finish_activation(activation_id)

    def _record_last_attempt(self, cache: dict, *, failed: bool) -> None:
        candidate = self.last_code_result or {}
        code = str(candidate.get("code") or "")
        sms_key = str(candidate.get("sms_key") or "")
        used_codes = set(cache.get("used_codes") or [])
        attempted_sms_keys = set(cache.get("attempted_sms_keys") or [])
        if code:
            used_codes.add(code)
        if sms_key:
            attempted_sms_keys.add(sms_key)
        cache["used_codes"] = used_codes
        cache["attempted_sms_keys"] = attempted_sms_keys
        if failed:
            cache["last_failed_reason"] = "invalid otp"

    def mark_code_failed(self, activation_id: str, reason: str = "") -> None:
        with _HERO_SMS_CACHE_LOCK:
            cache = _HERO_SMS_CACHE
            if cache and str(cache.get("activation_id")) == str(activation_id):
                self._record_last_attempt(cache, failed=True)
                self._save_cache(cache)
        if self.openai_resend_callback:
            try:
                self.openai_resend_callback()
            except Exception:
                pass
        self.request_resend_sms(activation_id)

    def mark_send_succeeded(self, activation_id: str) -> None:
        try:
            self.set_status(activation_id, 1)
        except Exception:
            pass

    def mark_send_failed(self, activation_id: str, reason: str = "") -> None:
        reason_text = str(reason or "").lower()
        if any(keyword in reason_text for keyword in ("limit", "already", "too many", "exceeded", "maximum", "ä¸Šé™", "å·²è¾¾")):
            self._stop_reuse("phone limit reached")
        else:
            self._stop_reuse(reason or "phone rejected")

    def set_resend_callback(self, callback: Callable[[], None] | None) -> None:
        self.openai_resend_callback = callback

    def get_reuse_info(self) -> dict:
        with _HERO_SMS_CACHE_LOCK:
            cache = _HERO_SMS_CACHE or self._load_cache(self.default_service, self.default_country) or {}
            if not cache:
                return {"alive": False}
            remaining = max(0, int(HERO_SMS_PHONE_LIFETIME - (time.time() - float(cache.get("acquired_at") or 0))))
            return {
                "alive": remaining > 0 and not bool(cache.get("reuse_stopped")),
                "phone_number": cache.get("phone_number", ""),
                "use_count": int(cache.get("use_count") or 0),
                "remaining_seconds": remaining,
                "reuse_stopped": bool(cache.get("reuse_stopped")),
                "stop_reason": cache.get("stop_reason", ""),
            }


class SmsBowerProvider(HeroSmsProvider):
    """SMSBower provider â€” API å…¼å®¹ HeroSMSï¼Œä»… base URL ä¸åŒã€‚"""

    BASE_URL = "https://smsbower.page/stubs/handler_api.php"

    def _request(self, params: dict, *, needs_key: bool = True, timeout: int = 30) -> requests.Response:
        # SMSBower æ‰€æœ‰æŽ¥å£éƒ½éœ€è¦ api_keyï¼ˆåŒ…æ‹¬ getServicesListã€getCountriesï¼‰
        payload = dict(params)
        if needs_key or self.api_key:
            payload["api_key"] = self.api_key
        resp = requests.get(self.BASE_URL, params=payload, timeout=timeout, proxies=self.proxies)
        resp.raise_for_status()
        return resp


UOMSG_DEFAULT_BASE_URL = "http://api.uomsg.com/zc/data.php"
UOMSG_SERVICE_KEYWORDS = {
    "qq": "腾讯",
    "lingya_qq": "腾讯",
    "default": "",
}


def _extract_uomsg_code(text: str) -> str:
    raw = str(text or "").strip()
    for pattern in (
        r"(?:验证码|校验码|动态码|code)\D{0,12}(\d{4,8})",
        r"(?<!\d)(\d{4,8})(?!\d)",
    ):
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


class UOMsgProvider(BaseSmsProvider):
    """UOMsg provider (api.uomsg.com)."""

    BASE_URL = UOMSG_DEFAULT_BASE_URL
    PROVIDER_KEY = "uomsg"

    def __init__(
        self,
        token: str,
        *,
        default_keyword: str = "",
        province: str = "",
        card_type: str = "全部",
        phone: str = "",
        poll_interval: int = 3,
        proxy: str | None = None,
        base_url: str = "",
    ):
        self.token = str(token or "").strip()
        self.default_keyword = str(default_keyword or "").strip()
        self.province = str(province or "").strip()
        self.card_type = str(card_type or "全部").strip() or "全部"
        self.phone = str(phone or "").strip()
        self.poll_interval = max(1, _safe_int(poll_interval, 3))
        self.base_url = str(base_url or self.BASE_URL).strip() or self.BASE_URL
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self._activation_keywords: dict[str, str] = {}

    def _request(self, code: str, **params) -> str:
        payload = {
            "code": code,
            "token": self.token,
        }
        for key, value in params.items():
            if value not in (None, ""):
                payload[key] = value
        resp = requests.get(self.base_url, params=payload, timeout=20, proxies=self.proxies)
        resp.raise_for_status()
        text = resp.text.strip()
        if text.upper().startswith("ERROR:"):
            raise RuntimeError(f"UOMsg {code} failed: {text}")
        return text

    def _keyword_for(self, service: str = "") -> str:
        if self.default_keyword:
            return self.default_keyword
        raw = str(service or "").strip()
        return UOMSG_SERVICE_KEYWORDS.get(raw, raw or UOMSG_SERVICE_KEYWORDS["default"]).strip()

    def get_balance(self):
        text = self._request("leftAmount")
        try:
            return float(text)
        except ValueError:
            return text

    def get_number(self, *, service: str, country: str = "") -> SmsActivation:
        keyword = self._keyword_for(service)
        if not keyword:
            raise RuntimeError("UOMsg 需要配置短信关键词(uomsg_keyword)，否则无法按关键词读取短信")
        phone = ""
        for attempt in range(2):
            phone = self._request(
                "getPhone",
                keyWord=keyword,
                phone=self.phone,
                province=country or self.province,
                cardType=self.card_type,
            ).strip()
            if not phone:
                raise RuntimeError("UOMsg getPhone 未返回手机号")
            if _reserve_sms_number(self.PROVIDER_KEY, phone):
                break
            logger.warning("UOMsg getPhone returned duplicate active phone %s; retrying once", phone)
            phone = ""
        if not phone:
            raise RuntimeError("UOMsg getPhone 连续返回已占用手机号，已放弃本次取号")
        self._activation_keywords[phone] = keyword
        return SmsActivation(
            activation_id=phone,
            phone_number=phone,
            country=country or self.province,
            metadata={"keyword": keyword, "provider": "uomsg"},
        )

    def get_code(self, activation_id: str, *, timeout: int = 120) -> str:
        phone = str(activation_id or "").strip()
        keyword = self._activation_keywords.get(phone) or self._keyword_for("")
        if not phone:
            return ""
        if not keyword:
            raise RuntimeError("UOMsg 需要配置短信关键词(uomsg_keyword)，否则无法按关键词读取短信")
        return self.get_code_after(phone, timeout=timeout)

    def get_message_text(self, activation_id: str) -> str:
        phone = str(activation_id or "").strip()
        keyword = self._activation_keywords.get(phone) or self._keyword_for("")
        if not phone or not keyword:
            return ""
        return self._request("getMsg", phone=phone, keyWord=keyword)

    def get_code_after(self, activation_id: str, *, timeout: int = 120, ignore_text: str = "") -> str:
        phone = str(activation_id or "").strip()
        keyword = self._activation_keywords.get(phone) or self._keyword_for("")
        if not phone:
            return ""
        if not keyword:
            raise RuntimeError("UOMsg 需要配置短信关键词(uomsg_keyword)，否则无法按关键词读取短信")
        ignored = str(ignore_text or "").strip()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                text = self._request("getMsg", phone=phone, keyWord=keyword)
            except requests.RequestException as exc:
                logger.warning("UOMsg getMsg transient request error for %s: %s", phone, exc)
                time.sleep(min(self.poll_interval, max(0, deadline - time.time())))
                continue
            if "[尚未收到]" in text or "尚未收到" in text:
                time.sleep(self.poll_interval)
                continue
            if ignored and text.strip() == ignored:
                time.sleep(self.poll_interval)
                continue
            code = _extract_uomsg_code(text)
            if code:
                return code
            logger.warning("UOMsg received an unparsable SMS for %s; waiting for a newer SMS: %s", phone, text[:200])
            ignored = text.strip()
            time.sleep(min(self.poll_interval, max(0, deadline - time.time())))
        return ""

    def cancel(self, activation_id: str) -> bool:
        phone = str(activation_id or "").strip()
        if not phone:
            return False
        try:
            self._request("release", phone=phone)
            return True
        finally:
            _release_sms_number(self.PROVIDER_KEY, phone)
            self._activation_keywords.pop(phone, None)

    def report_success(self, activation_id: str) -> bool:
        return self.cancel(activation_id)

    def block(self, activation_id: str) -> bool:
        phone = str(activation_id or "").strip()
        if not phone:
            return False
        try:
            self._request("block", phone=phone)
            return True
        finally:
            _release_sms_number(self.PROVIDER_KEY, phone)
            self._activation_keywords.pop(phone, None)

    def mark_send_failed(self, activation_id: str, reason: str = "") -> None:
        try:
            self.block(activation_id)
        except Exception:
            pass

    def send_sms(self, *, phone: str, to_phone: str, content: str, proj_id: str = "") -> str:
        return self._request(
            "send",
            phone=phone,
            toPhone=to_phone,
            projId=proj_id,
            content=content,
        )

    def query_used(self) -> str:
        return self._request("queryUsed")


HAOZHUMA_DEFAULT_BASE_URL = "https://api.haozhuyun.com/sms/"


class HaoZhuMaProvider(BaseSmsProvider):
    """HaoZhuMa provider (api.haozhuyun.com)."""

    BASE_URL = HAOZHUMA_DEFAULT_BASE_URL
    PROVIDER_KEY = "haozhuma"

    def __init__(
        self,
        *,
        user: str = "",
        password: str = "",
        token: str = "",
        sid: str = "",
        phone: str = "",
        isp: str = "",
        province: str = "",
        ascription: str = "",
        paragraph: str = "",
        exclude: str = "",
        uid: str = "",
        author: str = "",
        batch_size: int = 1,
        batch_param: str = "num",
        poll_interval: int = 15,
        proxy: str | None = None,
        base_url: str = "",
        token_store: Callable[[str], None] | None = None,
    ):
        self.token = str(token or "").strip()
        self.user = str(user or "").strip()
        self.password = str(password or "").strip()
        self.sid = str(sid or "").strip()
        self.phone = str(phone or "").strip()
        self.isp = str(isp or "").strip()
        self.province = str(province or "").strip()
        self.ascription = str(ascription or "").strip()
        self.paragraph = str(paragraph or "").strip()
        self.exclude = str(exclude or "").strip()
        self.uid = str(uid or "").strip()
        self.author = str(author or "").strip()
        self.batch_size = max(1, _safe_int(batch_size, 1))
        self.batch_param = str(batch_param or "num").strip() or "num"
        self.poll_interval = max(1, _safe_int(poll_interval, 15))
        self.base_url = str(base_url or self.BASE_URL).strip() or self.BASE_URL
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self._token_store = token_store
        self._activation_sids: dict[str, str] = {}
        self._closed_activations: set[str] = set()

    def _send_request(self, payload: dict[str, str]) -> dict:
        resp = requests.get(self.base_url, params=payload, timeout=20, proxies=self.proxies)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"HaoZhuMa {payload.get('api')} returned invalid JSON: {resp.text[:200]}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"HaoZhuMa {payload.get('api')} returned unexpected response: {data!r}")
        return data

    def _request(self, api: str, *, needs_token: bool = True, **params) -> dict:
        payload = {"api": api}
        used_cached_token = bool(self.token)
        if needs_token:
            payload["token"] = self._token()
        for key, value in params.items():
            if value not in (None, ""):
                payload[key] = value
        data = self._send_request(payload)
        code = str(data.get("code", "")).strip()
        if code not in {"0", "200"} and api == "getMessage" and _haozhuma_message_waiting_data(data):
            raise RuntimeError(f"HaoZhuMa {api} pending: {data.get('msg') or data}")
        if code not in {"0", "200"} and needs_token and used_cached_token and self.user and self.password:
            self.token = ""
            payload["token"] = self._token()
            data = self._send_request(payload)
            code = str(data.get("code", "")).strip()
        if code not in {"0", "200"}:
            raise RuntimeError(f"HaoZhuMa {api} failed: {data.get('msg') or data}")
        return data

    def _token(self) -> str:
        if self.token:
            return self.token
        if not self.user or not self.password:
            raise RuntimeError("HaoZhuMa 未配置 API 账号密码")
        data = self._request("login", needs_token=False, user=self.user, **{"pass": self.password})
        self.token = str(data.get("token") or "").strip()
        if not self.token:
            raise RuntimeError(f"HaoZhuMa login did not return token: {data}")
        if self._token_store:
            try:
                self._token_store(self.token)
            except Exception as exc:
                logger.warning("HaoZhuMa cached token store failed: %s", exc)
        return self.token

    def _sid(self, service: str = "") -> str:
        sid = str(self.sid or service or "").strip()
        if not sid:
            raise RuntimeError("HaoZhuMa 需要配置项目 ID(haozhuma_sid)")
        return sid

    def get_balance(self):
        data = self._request("getSummary")
        value = data.get("money")
        try:
            return float(value)
        except (TypeError, ValueError):
            return value

    def get_number(self, *, service: str, country: str = "") -> SmsActivation:
        sid = self._sid(service)
        data: dict[str, Any] = {}
        phone = ""
        for attempt in range(2):
            extra_params = {}
            if self.batch_size > 1 and not self.phone:
                extra_params[self.batch_param] = str(self.batch_size)
            data = self._request(
                "getPhone",
                sid=sid,
                phone=self.phone,
                isp=self.isp,
                Province=country or self.province,
                ascription=self.ascription,
                paragraph=self.paragraph,
                exclude=self.exclude,
                uid=self.uid,
                author=self.author,
                **extra_params,
            )
            for candidate in self._phone_candidates(data):
                if _reserve_sms_number(self.PROVIDER_KEY, candidate):
                    phone = candidate
                    break
                logger.warning("HaoZhuMa getPhone returned duplicate active phone %s; trying another candidate", candidate)
            if phone:
                break
        if not phone:
            raise RuntimeError(f"HaoZhuMa getPhone 未返回可用手机号: {data}")
        self._activation_sids[phone] = str(data.get("sid") or sid).strip() or sid
        return SmsActivation(
            activation_id=phone,
            phone_number=phone,
            country=country or self.province or str(data.get("country_code") or ""),
            metadata={"sid": self._activation_sids[phone], "provider": "haozhuma", "raw": data},
        )

    def _phone_candidates(self, data: dict[str, Any]) -> list[str]:
        raw_values = [
            data.get("phone"),
            data.get("phones"),
            data.get("phone_list"),
            data.get("data"),
            data.get("list"),
        ]
        candidates: list[str] = []

        def add(value: Any) -> None:
            if value in (None, ""):
                return
            if isinstance(value, dict):
                add(value.get("phone") or value.get("mobile") or value.get("number"))
                return
            if isinstance(value, list):
                for item in value:
                    add(item)
                return
            text = str(value).strip()
            if not text:
                return
            for part in re.split(r"[\s,|;]+", text):
                phone = part.strip()
                if phone and phone not in candidates:
                    candidates.append(phone)

        for value in raw_values:
            add(value)
        return candidates

    def get_code(self, activation_id: str, *, timeout: int = 120) -> str:
        phone = str(activation_id or "").strip()
        if not phone:
            return ""
        sid = self._activation_sids.get(phone) or self._sid("")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data = self._request("getMessage", sid=sid, phone=phone)
            except requests.RequestException as exc:
                logger.warning("HaoZhuMa getMessage transient request error for %s: %s", phone, exc)
                time.sleep(min(self.poll_interval, max(0, deadline - time.time())))
                continue
            except RuntimeError as exc:
                if _haozhuma_message_waiting_error(exc):
                    time.sleep(min(self.poll_interval, max(0, deadline - time.time())))
                    continue
                raise
            code = str(data.get("yzm") or "").strip() or _extract_uomsg_code(str(data.get("sms") or ""))
            if code:
                return code
            time.sleep(min(self.poll_interval, max(0, deadline - time.time())))
        self._release_and_blacklist(phone)
        return ""

    def _release(self, activation_id: str) -> bool:
        phone = str(activation_id or "").strip()
        if not phone:
            return False
        sid = self._activation_sids.get(phone) or self._sid("")
        self._request("cancelRecv", sid=sid, phone=phone)
        return True

    def block(self, activation_id: str) -> bool:
        phone = str(activation_id or "").strip()
        if not phone:
            return False
        sid = self._activation_sids.get(phone) or self._sid("")
        self._request("addBlacklist", sid=sid, phone=phone)
        return True

    def _release_and_blacklist(self, activation_id: str) -> bool:
        phone = str(activation_id or "").strip()
        if not phone or phone in self._closed_activations:
            return False
        ok = True
        try:
            self._release(phone)
        except Exception as exc:
            ok = False
            logger.warning("HaoZhuMa cancelRecv failed for %s: %s", phone, exc)
        try:
            self.block(phone)
        except Exception as exc:
            ok = False
            logger.warning("HaoZhuMa addBlacklist failed for %s: %s", phone, exc)
        self._closed_activations.add(phone)
        _release_sms_number(self.PROVIDER_KEY, phone)
        self._activation_sids.pop(phone, None)
        return ok

    def cancel(self, activation_id: str) -> bool:
        return self._release_and_blacklist(activation_id)

    def report_success(self, activation_id: str) -> bool:
        return self._release_and_blacklist(activation_id)

    def mark_code_failed(self, activation_id: str, reason: str = "") -> None:
        self._release_and_blacklist(activation_id)

    def mark_send_failed(self, activation_id: str, reason: str = "") -> None:
        self._release_and_blacklist(activation_id)


def _haozhuma_message_waiting_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("等待", "尚未", "未收到", "wait", "waiting", "no message"))


def _haozhuma_message_waiting_data(data: dict[str, Any]) -> bool:
    text = str(data.get("msg") or data.get("message") or data).lower()
    return any(token in text for token in ("等待", "尚未", "未收到", "wait", "waiting", "no message"))


def is_herosms_phone_cache_alive(config: dict | None = None) -> tuple[bool, dict]:
    """Return whether the current HeroSMS cache is reusable for scheduling."""
    config = dict(config or {})
    api_key = str(config.get("herosms_api_key") or "").strip()
    if not api_key:
        return False, {"alive": False}
    provider = HeroSmsProvider(
        api_key,
        default_service=str(config.get("sms_service") or HERO_SMS_DEFAULT_SERVICE),
        default_country=str(config.get("sms_country") or config.get("herosms_country") or HERO_SMS_DEFAULT_COUNTRY),
        phone_success_max=max(0, _safe_int(config.get("register_phone_success_max"), 3)),
    )
    info = provider.get_reuse_info()
    return bool(info.get("alive")), info


def _haozhuma_token_store(provider_key: str) -> Callable[[str], None]:
    keys = [provider_key]
    if provider_key != "haozhuma_api":
        keys.append("haozhuma_api")

    def _store(token: str) -> None:
        cached_token = str(token or "").strip()
        if not cached_token:
            return
        try:
            from infrastructure.provider_settings_repository import ProviderSettingsRepository

            repo = ProviderSettingsRepository()
            for key in keys:
                if repo.update_auth_values("sms", key, {"haozhuma_cached_token": cached_token}):
                    return
        except Exception as exc:
            logger.warning("HaoZhuMa cached token persistence failed: %s", exc)

    return _store


# ---------------------------------------------------------------------------
# Factory and browser callback adapter
# ---------------------------------------------------------------------------

def create_sms_provider(provider_key: str, config: dict) -> BaseSmsProvider:
    """Create an SMS provider instance from config."""
    if provider_key in ("sms_activate", "sms_activate_api"):
        api_key = config.get("sms_activate_api_key", "")
        if not api_key:
            raise RuntimeError("SMS-Activate 未配置 API Key")
        return SmsActivateProvider(
            api_key=api_key,
            default_country=config.get("sms_activate_country", config.get("sms_activate_default_country", "")),
            proxy=config.get("sms_proxy") or config.get("proxy") or None,
        )
    if provider_key in ("herosms", "herosms_api"):
        api_key = str(config.get("herosms_api_key", "") or "").strip()
        if not api_key:
            raise RuntimeError("HeroSMS 未配置 API Key")
        return HeroSmsProvider(
            api_key=api_key,
            default_service=str(config.get("sms_service") or config.get("herosms_service") or config.get("herosms_default_service") or HERO_SMS_DEFAULT_SERVICE),
            default_country=str(config.get("sms_country") or config.get("herosms_country") or config.get("herosms_default_country") or HERO_SMS_DEFAULT_COUNTRY),
            max_price=_safe_float(config.get("herosms_max_price"), -1),
            proxy=str(config.get("sms_proxy") or config.get("proxy") or "") or None,
            reuse_phone_to_max=_safe_bool(config.get("register_reuse_phone_to_max"), True),
            phone_success_max=max(0, _safe_int(config.get("register_phone_extra_max") or config.get("register_phone_success_max"), 3)),
        )
    if provider_key in ("smsbower", "smsbower_api"):
        api_key = str(config.get("smsbower_api_key", "") or "").strip()
        if not api_key:
            raise RuntimeError("SMSBower 未配置 API Key")
        return SmsBowerProvider(
            api_key=api_key,
            default_service=str(config.get("sms_service") or config.get("smsbower_service") or config.get("smsbower_default_service") or HERO_SMS_DEFAULT_SERVICE),
            default_country=str(config.get("sms_country") or config.get("smsbower_country") or config.get("smsbower_default_country") or HERO_SMS_DEFAULT_COUNTRY),
            max_price=_safe_float(config.get("smsbower_max_price"), -1),
            proxy=str(config.get("sms_proxy") or config.get("proxy") or "") or None,
            reuse_phone_to_max=_safe_bool(config.get("register_reuse_phone_to_max"), True),
            phone_success_max=max(0, _safe_int(config.get("register_phone_extra_max") or config.get("register_phone_success_max"), 3)),
        )
    if provider_key in ("uomsg", "uomsg_api"):
        token = str(config.get("uomsg_token") or config.get("token") or "").strip()
        if not token:
            raise RuntimeError("UOMsg 未配置 API Token")
        return UOMsgProvider(
            token=token,
            default_keyword=str(config.get("uomsg_keyword") or config.get("sms_keyword") or "").strip(),
            province=str(config.get("uomsg_province") or config.get("sms_country") or "").strip(),
            card_type=str(config.get("uomsg_card_type") or "全部").strip() or "全部",
            phone=str(config.get("uomsg_phone") or "").strip(),
            poll_interval=_safe_int(config.get("uomsg_poll_interval"), 3),
            proxy=str(config.get("sms_proxy") or config.get("proxy") or "") or None,
            base_url=str(config.get("uomsg_base_url") or "").strip(),
        )
    if provider_key in ("haozhuma", "haozhuma_api"):
        user = str(config.get("haozhuma_user") or config.get("haozhuma_username") or "").strip()
        password = str(config.get("haozhuma_password") or "").strip()
        if not user or not password:
            raise RuntimeError("HaoZhuMa 未配置 API 账号密码")
        return HaoZhuMaProvider(
            user=user,
            password=password,
            token=str(config.get("haozhuma_cached_token") or "").strip(),
            sid=str(config.get("haozhuma_sid") or config.get("sms_service") or "").strip(),
            phone=str(config.get("haozhuma_phone") or "").strip(),
            isp=str(config.get("haozhuma_isp") or "").strip(),
            province=str(config.get("haozhuma_province") or config.get("sms_country") or "").strip(),
            ascription=str(config.get("haozhuma_ascription") or "").strip(),
            paragraph=str(config.get("haozhuma_paragraph") or "").strip(),
            exclude=str(config.get("haozhuma_exclude") or "").strip(),
            uid=str(config.get("haozhuma_uid") or "").strip(),
            author=str(config.get("haozhuma_author") or "").strip(),
            batch_size=_safe_int(config.get("haozhuma_batch_size"), 5),
            batch_param=str(config.get("haozhuma_batch_param") or "num").strip(),
            poll_interval=_safe_int(config.get("haozhuma_poll_interval"), 15),
            proxy=str(config.get("sms_proxy") or config.get("proxy") or "") or None,
            base_url=str(config.get("haozhuma_base_url") or "").strip(),
            token_store=_haozhuma_token_store(provider_key),
        )
    raise RuntimeError(f"未知的接码服务: {provider_key}")


class PhoneCallbackController:
    """Callable phone callback with optional lifecycle hooks for advanced providers."""

    def __init__(self, provider_key: str, config: dict, *, service: str, country: str = "", log_fn=None):
        self.provider_key = provider_key
        self.config = dict(config or {})
        self.service = service
        self.country = country
        self.log = log_fn or logger.info
        self.provider: Optional[BaseSmsProvider] = None
        self.activation: Optional[SmsActivation] = None
        self.phase = "need_number"
        self.completed = False
        self._verify_lock_acquired = False
        self.awaiting_external_success = False

    def _provider(self) -> BaseSmsProvider:
        if self.provider is None:
            self.provider = create_sms_provider(self.provider_key, self.config)
        return self.provider

    def __call__(self) -> str:
        provider = self._provider()
        if self.phase == "need_number":
            if self.provider_key == "herosms" and not self._verify_lock_acquired:
                _HERO_SMS_VERIFY_LOCK.acquire()
                self._verify_lock_acquired = True

            # æ™ºèƒ½å›½å®¶é€‰æ‹©ï¼šå¦‚æžœå¯ç”¨äº† auto_select_countryï¼Œè‡ªåŠ¨æŸ¥è¯¢æœ€ä¼˜å›½å®¶
            effective_country = self.country
            auto_select = _safe_bool(self.config.get("herosms_auto_country") or self.config.get("smsbower_auto_country"), False)
            if auto_select and isinstance(provider, HeroSmsProvider):
                self.log("æ­£åœ¨æŸ¥è¯¢æœ€ä¼˜å›½å®¶ï¼ˆä»·æ ¼æœ€ä½Ž + åº“å­˜å……è¶³ï¼‰...")
                try:
                    min_stock = _safe_int(self.config.get("herosms_auto_country_min_stock") or self.config.get("smsbower_auto_country_min_stock"), 20)
                    max_price_limit = _safe_float(self.config.get("herosms_auto_country_max_price") or self.config.get("smsbower_auto_country_max_price"), 0)
                    best = provider.get_best_country(
                        service=self.service,
                        min_stock=min_stock,
                        max_price=max_price_limit,
                    )
                    if best:
                        self.log(f"è‡ªåŠ¨é€‰æ‹©æœ€ä¼˜å›½å®¶: {best}")
                        effective_country = best
                    else:
                        self.log("æœªæ‰¾åˆ°æ»¡è¶³æ¡ä»¶çš„å›½å®¶ï¼Œä½¿ç”¨é»˜è®¤é…ç½®")
                except Exception as exc:
                    self.log(f"æ™ºèƒ½å›½å®¶é€‰æ‹©å¤±è´¥({exc})ï¼Œä½¿ç”¨é»˜è®¤é…ç½®")

            country_label = effective_country or self.config.get("sms_country") or self.config.get("sms_activate_country") or "default"
            self.log(f"已进入 add_phone，准备租用手机号: provider={self.provider_key} service={self.service} country={country_label}")
            self.log(f"正在从 {self.provider_key} 获取手机号...")
            try:
                self.activation = provider.get_number(service=self.service, country=effective_country)
            except Exception as first_exc:
                # å¦‚æžœæ˜¯è‡ªåŠ¨é€‰æ‹©çš„å›½å®¶å¤±è´¥äº†ï¼Œå›žé€€åˆ°é»˜è®¤å›½å®¶é‡è¯•
                fallback_country = self.country or self.config.get("sms_country") or self.config.get("herosms_country") or ""
                if auto_select and effective_country != fallback_country and fallback_country:
                    self.log(f"è‡ªåŠ¨é€‰æ‹©çš„å›½å®¶({effective_country})èŽ·å–å·ç å¤±è´¥ï¼Œå›žé€€åˆ°é»˜è®¤å›½å®¶({fallback_country})...")
                    try:
                        self.activation = provider.get_number(service=self.service, country=fallback_country)
                    except Exception:
                        if self._verify_lock_acquired:
                            _HERO_SMS_VERIFY_LOCK.release()
                            self._verify_lock_acquired = False
                        raise
                else:
                    if self._verify_lock_acquired:
                        _HERO_SMS_VERIFY_LOCK.release()
                        self._verify_lock_acquired = False
                    raise
            self.phase = "need_code"
            reused = bool((self.activation.metadata or {}).get("reused"))
            reuse_label = "复用号码" if reused else "新号码"
            self.log(f"已成功租到号码({reuse_label}): {self.activation.phone_number} (activation_id={self.activation.activation_id})")
            return self.activation.phone_number

        if self.phase == "need_code" and self.activation:
            self.log(f"等待短信验证码... (activation_id={self.activation.activation_id})")
            code = provider.get_code(self.activation.activation_id, timeout=180)
            if code:
                self.log(f"收到验证码: {code}")
                if getattr(provider, "auto_report_success_on_code", True):
                    self.report_success()
                else:
                    self.awaiting_external_success = True
            else:
                self.log(f"未收到验证码: activation_id={self.activation.activation_id}")
            return code
        return ""

    def set_resend_callback(self, callback: Callable[[], None] | None) -> None:
        if self.provider is not None:
            self.provider.set_resend_callback(callback)
        else:
            original_provider = self._provider()
            original_provider.set_resend_callback(callback)

    def mark_code_failed(self, reason: str = "") -> None:
        if self.activation and self.provider:
            hook = getattr(self.provider, "mark_code_failed", None)
            if callable(hook):
                hook(self.activation.activation_id, reason=reason)
            self.phase = "need_code"
            self.awaiting_external_success = False

    def mark_send_failed(self, reason: str = "") -> None:
        if self.activation and self.provider:
            hook = getattr(self.provider, "mark_send_failed", None)
            if callable(hook):
                hook(self.activation.activation_id, reason=reason)
            self.awaiting_external_success = False

    def mark_send_succeeded(self) -> None:
        if self.activation and self.provider:
            hook = getattr(self.provider, "mark_send_succeeded", None)
            if callable(hook):
                hook(self.activation.activation_id)

    def report_success(self) -> None:
        if self.activation and self.provider and not self.completed:
            self.provider.report_success(self.activation.activation_id)
            self.completed = True
            self.phase = "done"
            self.awaiting_external_success = False
            self.log(f"短信验证成功，已标记号码完成使用: activation_id={self.activation.activation_id}")
        if self._verify_lock_acquired:
            _HERO_SMS_VERIFY_LOCK.release()
            self._verify_lock_acquired = False

    def cleanup(self) -> None:
        if self.activation and not self.completed:
            try:
                provider = self._provider()
                if self.awaiting_external_success and not getattr(provider, "auto_report_success_on_code", True):
                    self.report_success()
                else:
                    provider.cancel(self.activation.activation_id)
                    self.log(f"已释放未使用号码: activation_id={self.activation.activation_id}")
            except Exception:
                pass
        if self._verify_lock_acquired:
            _HERO_SMS_VERIFY_LOCK.release()
            self._verify_lock_acquired = False


def create_phone_callbacks(
    provider_key: str,
    config: dict,
    *,
    service: str,
    country: str = "",
    log_fn=None,
) -> tuple:
    """Create (phone_callback, cleanup) tuple for browser registration."""
    controller = PhoneCallbackController(
        provider_key,
        config,
        service=service,
        country=country,
        log_fn=log_fn,
    )
    return controller, controller.cleanup
