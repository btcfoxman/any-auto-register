from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.base_sms import (
    EOMsgProvider,
    FeiHuMsgProvider,
    HERO_SMS_DEFAULT_COUNTRY,
    HERO_SMS_DEFAULT_SERVICE,
    HaoZhuMaProvider,
    HeroSmsProvider,
    SmsBowerProvider,
    UOMsgProvider,
)
from infrastructure.provider_settings_repository import ProviderSettingsRepository

router = APIRouter(prefix="/sms", tags=["sms"])


class HeroSmsQueryRequest(BaseModel):
    api_key: str = ""
    service: str = ""
    country: str = ""
    proxy: str = ""


def _saved_herosms_config() -> dict:
    repo = ProviderSettingsRepository()
    # 兼容旧版 provider_key "herosms" 和新版 "herosms_api"
    config = repo.resolve_runtime_settings("sms", "herosms_api", {})
    if not config.get("herosms_api_key"):
        config = repo.resolve_runtime_settings("sms", "herosms", {})
    return config


def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _provider_from_payload(payload: HeroSmsQueryRequest | None = None) -> HeroSmsProvider:
    payload = payload or HeroSmsQueryRequest()
    saved = _saved_herosms_config()
    api_key = str(payload.api_key or saved.get("herosms_api_key") or "").strip()
    return HeroSmsProvider(
        api_key=api_key,
        default_service=str(payload.service or saved.get("sms_service") or HERO_SMS_DEFAULT_SERVICE),
        default_country=str(payload.country or saved.get("sms_country") or HERO_SMS_DEFAULT_COUNTRY),
        max_price=_safe_float(saved.get("herosms_max_price"), -1),
        proxy=str(payload.proxy or saved.get("sms_proxy") or saved.get("proxy") or "") or None,
    )


@router.get("/herosms/countries")
def herosms_countries():
    try:
        return {"countries": _provider_from_payload().get_countries()}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@router.get("/herosms/services")
def herosms_services(country: str = ""):
    try:
        return {"services": _provider_from_payload(HeroSmsQueryRequest(country=country)).get_services(country=country or None)}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@router.post("/herosms/balance")
def herosms_balance(body: HeroSmsQueryRequest | None = None):
    body = body or HeroSmsQueryRequest()
    provider = _provider_from_payload(body)
    if not provider.api_key:
        raise HTTPException(400, "HeroSMS API Key 未配置")
    try:
        return {"balance": provider.get_balance()}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@router.post("/herosms/prices")
def herosms_prices(body: HeroSmsQueryRequest | None = None):
    body = body or HeroSmsQueryRequest()
    provider = _provider_from_payload(body)
    if not provider.api_key:
        raise HTTPException(400, "HeroSMS API Key 未配置")
    try:
        service = str(body.service or provider.default_service or HERO_SMS_DEFAULT_SERVICE)
        country = str(body.country or provider.default_country or HERO_SMS_DEFAULT_COUNTRY)
        return {"prices": provider.get_prices(service=service, country=country)}
    except Exception as exc:
        raise HTTPException(502, str(exc))


class HeroSmsBestCountryRequest(BaseModel):
    api_key: str = ""
    service: str = ""
    proxy: str = ""
    min_stock: int = 20
    max_price: float = 0
    top_n: int = 10


@router.post("/herosms/top-countries")
def herosms_top_countries(body: HeroSmsBestCountryRequest | None = None):
    """获取按价格排序的国家列表（含价格和库存）。"""
    body = body or HeroSmsBestCountryRequest()
    provider = _provider_from_payload(HeroSmsQueryRequest(
        api_key=body.api_key, service=body.service, proxy=body.proxy,
    ))
    if not provider.api_key:
        raise HTTPException(400, "HeroSMS API Key 未配置")
    try:
        service = str(body.service or provider.default_service or HERO_SMS_DEFAULT_SERVICE)
        rows = provider.get_top_countries(service=service)
        # 只返回有库存的
        rows = [r for r in rows if (r.get("count") or 0) > 0]
        if body.top_n > 0:
            rows = rows[:body.top_n]
        return {"countries": rows, "service": service}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@router.post("/herosms/best-country")
def herosms_best_country(body: HeroSmsBestCountryRequest | None = None):
    """自动选择最优国家（价格最低 + 库存充足）。"""
    body = body or HeroSmsBestCountryRequest()
    provider = _provider_from_payload(HeroSmsQueryRequest(
        api_key=body.api_key, service=body.service, proxy=body.proxy,
    ))
    if not provider.api_key:
        raise HTTPException(400, "HeroSMS API Key 未配置")
    try:
        service = str(body.service or provider.default_service or HERO_SMS_DEFAULT_SERVICE)
        best = provider.get_best_country(
            service=service,
            min_stock=body.min_stock,
            max_price=body.max_price,
        )
        if best:
            # 获取详细信息
            rows = provider.get_top_countries(service=service)
            detail = next((r for r in rows if str(r.get("country")) == str(best)), None)
            return {
                "country": best,
                "detail": detail,
                "service": service,
            }
        return {"country": None, "detail": None, "service": service}
    except Exception as exc:
        raise HTTPException(502, str(exc))


# ── SMSBower endpoints ──────────────────────────────────────────────────────

def _saved_smsbower_config() -> dict:
    return ProviderSettingsRepository().resolve_runtime_settings("sms", "smsbower_api", {})


def _smsbower_from_payload(payload: HeroSmsQueryRequest | None = None) -> SmsBowerProvider:
    payload = payload or HeroSmsQueryRequest()
    saved = _saved_smsbower_config()
    api_key = str(payload.api_key or saved.get("smsbower_api_key") or "").strip()
    return SmsBowerProvider(
        api_key=api_key,
        default_service=str(payload.service or saved.get("sms_service") or saved.get("smsbower_service") or HERO_SMS_DEFAULT_SERVICE),
        default_country=str(payload.country or saved.get("sms_country") or saved.get("smsbower_country") or HERO_SMS_DEFAULT_COUNTRY),
        max_price=_safe_float(saved.get("smsbower_max_price"), -1),
        proxy=str(payload.proxy or saved.get("sms_proxy") or saved.get("proxy") or "") or None,
    )


@router.get("/smsbower/countries")
def smsbower_countries():
    try:
        provider = _smsbower_from_payload()
        if not provider.api_key:
            return {"countries": []}
        return {"countries": provider.get_countries()}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@router.get("/smsbower/services")
def smsbower_services(country: str = ""):
    try:
        provider = _smsbower_from_payload(HeroSmsQueryRequest(country=country))
        if not provider.api_key:
            return {"services": []}
        return {"services": provider.get_services(country=country or None)}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@router.post("/smsbower/balance")
def smsbower_balance(body: HeroSmsQueryRequest | None = None):
    body = body or HeroSmsQueryRequest()
    provider = _smsbower_from_payload(body)
    if not provider.api_key:
        raise HTTPException(400, "SMSBower API Key 未配置")
    try:
        return {"balance": provider.get_balance()}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@router.post("/smsbower/prices")
def smsbower_prices(body: HeroSmsQueryRequest | None = None):
    body = body or HeroSmsQueryRequest()
    provider = _smsbower_from_payload(body)
    if not provider.api_key:
        raise HTTPException(400, "SMSBower API Key 未配置")
    try:
        service = str(body.service or provider.default_service or HERO_SMS_DEFAULT_SERVICE)
        country = str(body.country or provider.default_country or HERO_SMS_DEFAULT_COUNTRY)
        return {"prices": provider.get_prices(service=service, country=country)}
    except Exception as exc:
        raise HTTPException(502, str(exc))


# ── UOMsg endpoints ─────────────────────────────────────────────────────────

class UOMsgQueryRequest(BaseModel):
    token: str = ""
    keyword: str = ""
    province: str = ""
    card_type: str = "全部"
    phone: str = ""
    proxy: str = ""


def _saved_uomsg_config() -> dict:
    return ProviderSettingsRepository().resolve_runtime_settings("sms", "uomsg_api", {})


def _uomsg_from_payload(payload: UOMsgQueryRequest | None = None) -> UOMsgProvider:
    payload = payload or UOMsgQueryRequest()
    saved = _saved_uomsg_config()
    token = str(payload.token or saved.get("uomsg_token") or saved.get("token") or "").strip()
    return UOMsgProvider(
        token=token,
        default_keyword=str(payload.keyword or saved.get("uomsg_keyword") or saved.get("sms_keyword") or "").strip(),
        province=str(payload.province or saved.get("uomsg_province") or "").strip(),
        card_type=str(payload.card_type or saved.get("uomsg_card_type") or "全部").strip() or "全部",
        phone=str(payload.phone or saved.get("uomsg_phone") or "").strip(),
        proxy=str(payload.proxy or saved.get("sms_proxy") or saved.get("proxy") or "") or None,
    )


@router.post("/uomsg/balance")
def uomsg_balance(body: UOMsgQueryRequest | None = None):
    body = body or UOMsgQueryRequest()
    provider = _uomsg_from_payload(body)
    if not provider.token:
        raise HTTPException(400, "UOMsg API Token 未配置")
    try:
        return {"balance": provider.get_balance()}
    except Exception as exc:
        raise HTTPException(502, str(exc))


# ── EOMsg endpoints ─────────────────────────────────────────────────────────

class EOMsgQueryRequest(BaseModel):
    token: str = ""
    keyword: str = ""
    province: str = ""
    card_type: str = "全部"
    phone: str = ""
    proxy: str = ""


def _saved_eomsg_config() -> dict:
    return ProviderSettingsRepository().resolve_runtime_settings("sms", "eomsg_api", {})


def _eomsg_from_payload(payload: EOMsgQueryRequest | None = None) -> EOMsgProvider:
    payload = payload or EOMsgQueryRequest()
    saved = _saved_eomsg_config()
    token = str(payload.token or saved.get("eomsg_token") or saved.get("token") or "").strip()
    return EOMsgProvider(
        token=token,
        default_keyword=str(payload.keyword or saved.get("eomsg_keyword") or saved.get("sms_keyword") or "").strip(),
        province=str(payload.province or saved.get("eomsg_province") or "").strip(),
        card_type=str(payload.card_type or saved.get("eomsg_card_type") or "全部").strip() or "全部",
        phone=str(payload.phone or saved.get("eomsg_phone") or "").strip(),
        proxy=str(payload.proxy or saved.get("sms_proxy") or saved.get("proxy") or "") or None,
    )


@router.post("/eomsg/balance")
def eomsg_balance(body: EOMsgQueryRequest | None = None):
    body = body or EOMsgQueryRequest()
    provider = _eomsg_from_payload(body)
    if not provider.token:
        raise HTTPException(400, "EOMsg API Token 未配置")
    try:
        return {"balance": provider.get_balance()}
    except Exception as exc:
        raise HTTPException(502, str(exc))


# ── FeiHuMsg endpoints ──────────────────────────────────────────────────────

class FeiHuMsgQueryRequest(BaseModel):
    user: str = ""
    password: str = ""
    pid: str = ""
    proxy: str = ""


def _saved_feihumsg_config() -> dict:
    return ProviderSettingsRepository().resolve_runtime_settings("sms", "feihumsg_api", {})


def _feihumsg_from_payload(payload: FeiHuMsgQueryRequest | None = None) -> FeiHuMsgProvider:
    payload = payload or FeiHuMsgQueryRequest()
    saved = _saved_feihumsg_config()
    inline_auth = bool(str(payload.user or "").strip() or str(payload.password or "").strip())

    def _store_token(token: str) -> None:
        if inline_auth:
            return
        ProviderSettingsRepository().update_auth_values("sms", "feihumsg_api", {"feihumsg_cached_token": token})

    return FeiHuMsgProvider(
        token=str(saved.get("feihumsg_cached_token") or saved.get("feihumsg_token") or "").strip(),
        user=str(payload.user or saved.get("feihumsg_user") or saved.get("feihumsg_username") or "").strip(),
        password=str(payload.password or saved.get("feihumsg_password") or "").strip(),
        pid=str(payload.pid or saved.get("feihumsg_pid") or saved.get("sms_service") or "").strip(),
        proxy=str(payload.proxy or saved.get("sms_proxy") or saved.get("proxy") or "") or None,
        base_url=str(saved.get("feihumsg_base_url") or "").strip(),
        poll_interval=_safe_int(saved.get("feihumsg_poll_interval"), 10),
        token_store=_store_token,
    )


@router.post("/feihumsg/balance")
def feihumsg_balance(body: FeiHuMsgQueryRequest | None = None):
    body = body or FeiHuMsgQueryRequest()
    provider = _feihumsg_from_payload(body)
    if (not provider.user or not provider.password) and not provider.token:
        raise HTTPException(400, "FeiHuMsg API 账号密码未配置")
    try:
        return {"balance": provider.get_balance()}
    except Exception as exc:
        raise HTTPException(502, str(exc))


# ── HaoZhuMa endpoints ──────────────────────────────────────────────────────

class HaoZhuMaQueryRequest(BaseModel):
    user: str = ""
    password: str = ""
    sid: str = ""
    proxy: str = ""


def _saved_haozhuma_config() -> dict:
    return ProviderSettingsRepository().resolve_runtime_settings("sms", "haozhuma_api", {})


def _haozhuma_from_payload(payload: HaoZhuMaQueryRequest | None = None) -> HaoZhuMaProvider:
    payload = payload or HaoZhuMaQueryRequest()
    saved = _saved_haozhuma_config()
    inline_auth = bool(str(payload.user or "").strip() or str(payload.password or "").strip())

    def _store_token(token: str) -> None:
        if inline_auth:
            return
        ProviderSettingsRepository().update_auth_values("sms", "haozhuma_api", {"haozhuma_cached_token": token})

    return HaoZhuMaProvider(
        token=str(saved.get("haozhuma_cached_token") or "").strip(),
        user=str(payload.user or saved.get("haozhuma_user") or saved.get("haozhuma_username") or "").strip(),
        password=str(payload.password or saved.get("haozhuma_password") or "").strip(),
        sid=str(payload.sid or saved.get("haozhuma_sid") or saved.get("sms_service") or "").strip(),
        proxy=str(payload.proxy or saved.get("sms_proxy") or saved.get("proxy") or "") or None,
        base_url=str(saved.get("haozhuma_base_url") or "").strip(),
        batch_size=_safe_int(saved.get("haozhuma_batch_size"), 5),
        batch_param=str(saved.get("haozhuma_batch_param") or "num").strip(),
        token_store=_store_token,
    )


@router.post("/haozhuma/balance")
def haozhuma_balance(body: HaoZhuMaQueryRequest | None = None):
    body = body or HaoZhuMaQueryRequest()
    provider = _haozhuma_from_payload(body)
    if not provider.user or not provider.password:
        raise HTTPException(400, "HaoZhuMa API 账号密码未配置")
    try:
        return {"balance": provider.get_balance()}
    except Exception as exc:
        raise HTTPException(502, str(exc))
