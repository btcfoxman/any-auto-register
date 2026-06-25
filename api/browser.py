from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from application.accounts import AccountsService
from application.browser_assist import browser_assist_registry, normalize_proxy_url
from core.db import AccountModel, engine
from domain.accounts import AccountCreateCommand, AccountUpdateCommand
from platforms.lingya_qq.cookies import (
    LINGYA_QQ_COOKIE_NAMES,
    build_lingya_qq_account_fields,
    extract_lingya_qq_cookies,
)


router = APIRouter(prefix="/browser", tags=["browser"])
accounts_service = AccountsService()


class BrowserAssistClaimRequest(BaseModel):
    extension_id: str = ""
    platform: str = "lingya_qq"
    proxy_url: str = ""
    current_url: str = ""


class BrowserAssistStateRequest(BaseModel):
    extension_id: str = ""
    state: str = Field(default="")
    error: str = ""
    detail: dict = Field(default_factory=dict)


class BrowserCookiePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = ""
    value: str = ""
    domain: str = ""
    path: str = "/"
    secure: bool = False
    http_only: bool = Field(default=False, alias="httpOnly")
    same_site: str = Field(default="", alias="sameSite")
    expiration_date: float | None = Field(default=None, alias="expirationDate")


class BrowserImportAccountRequest(BaseModel):
    platform: str = "lingya_qq"
    name: str = ""
    cookies: list[BrowserCookiePayload] = Field(default_factory=list)
    cookie: str = ""
    user_agent: str = ""
    sec_ch_ua: str = ""
    sec_ch_ua_platform: str = ""
    proxy_url: str = ""
    max_concurrency: int = 1


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _clamp_concurrency(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 1
    return min(max(number, 1), 10)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cookie_payloads(cookies: list[BrowserCookiePayload]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for cookie in cookies:
        payloads.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path or "/",
                "secure": cookie.secure,
                "httpOnly": cookie.http_only,
                "sameSite": cookie.same_site,
                "expirationDate": cookie.expiration_date,
            }
        )
    return payloads


def _find_existing_lingya_account(*, email: str, vuid: str) -> AccountModel | None:
    with Session(engine) as session:
        if vuid:
            existing = session.exec(
                select(AccountModel)
                .where(AccountModel.platform == "lingya_qq")
                .where(AccountModel.user_id == vuid)
                .order_by(AccountModel.updated_at.desc(), AccountModel.id.desc())
            ).first()
            if existing:
                return existing
        if email:
            existing = session.exec(
                select(AccountModel)
                .where(AccountModel.platform == "lingya_qq")
                .where(AccountModel.email == email)
                .order_by(AccountModel.updated_at.desc(), AccountModel.id.desc())
            ).first()
            if existing and (not existing.user_id or not vuid or existing.user_id == vuid):
                return existing
    return None


def _validate_lingya_cookie_fields(fields: dict[str, Any]) -> tuple[str, str, str, str]:
    vuid = _first_text(fields.get("vuid"), fields.get("v_vuserid"), fields.get("vuserid"), fields.get("vqq_vuserid"))
    vusession = _first_text(fields.get("vusession"), fields.get("v_vusession"), fields.get("vqq_vusession"))
    vurefresh = _first_text(fields.get("vurefresh"), fields.get("v_vurefresh"))
    vdevice_guid = _text(fields.get("vdevice_guid"))
    missing = []
    if not vusession:
        missing.append("v_vusession/vusession")
    if not vurefresh:
        missing.append("v_vurefresh")
    if not vuid:
        missing.append("v_vuserid/vuserid")
    if not vdevice_guid:
        missing.append("vdevice_guid")
    if missing:
        raise HTTPException(400, f"Lingya cookies are incomplete; missing {', '.join(missing)}")
    return vuid, vusession, vurefresh, vdevice_guid


def _browser_import_overview(
    *,
    name: str,
    fields: dict[str, Any],
    vuid: str,
    vdevice_guid: str,
    body: BrowserImportAccountRequest,
    existing_overview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overview = dict(existing_overview or {})
    legacy_extra = dict(overview.get("legacy_extra") or {})
    browser_legacy_extra = {
        "browser_imported": True,
        "browser_imported_at": _utcnow_iso(),
        "lingya2api_name": name,
        "lingya2api_max_concurrency": _clamp_concurrency(body.max_concurrency),
        "proxy_url": normalize_proxy_url(body.proxy_url),
        "user_agent": _text(body.user_agent),
        "sec_ch_ua": _text(body.sec_ch_ua),
        "sec_ch_ua_platform": _text(body.sec_ch_ua_platform),
    }
    legacy_extra.update({key: value for key, value in browser_legacy_extra.items() if value not in (None, "", [], {})})
    chips = [str(item) for item in overview.get("chips") or [] if str(item or "").strip()]
    if "browser import" not in chips:
        chips.append("browser import")
    overview.update(
        {
            "platform": "lingya_qq",
            "source": "browser_extension",
            "imported_at": legacy_extra.get("browser_imported_at") or _utcnow_iso(),
            "vuid": vuid,
            "vdevice_guid": vdevice_guid,
            "nick": _text(fields.get("nick")),
            "chips": chips,
            "legacy_extra": legacy_extra,
        }
    )
    return overview


def _serialize_import_account(account: dict[str, Any], *, name: str) -> dict[str, Any]:
    return {
        "id": account.get("id"),
        "platform": account.get("platform"),
        "name": account.get("email") or name,
        "vuid": account.get("user_id"),
        "display_status": account.get("display_status"),
    }


@router.post("/assist/claim")
def claim_browser_assist(body: BrowserAssistClaimRequest):
    request = browser_assist_registry.claim(
        platform=body.platform,
        proxy_url=body.proxy_url,
        extension_id=body.extension_id,
        current_url=body.current_url,
    )
    return {"request": request, "poll_after_ms": 5000 if not request else 2000}


@router.get("/assist/claim")
def claim_browser_assist_get(
    extension_id: str = "",
    platform: str = "lingya_qq",
    proxy_url: str = "",
    current_url: str = "",
):
    request = browser_assist_registry.claim(
        platform=platform,
        proxy_url=proxy_url,
        extension_id=extension_id,
        current_url=current_url,
    )
    return {"request": request, "poll_after_ms": 5000 if not request else 2000}


@router.post("/assist/{assist_id}/state")
def update_browser_assist_state(assist_id: str, body: BrowserAssistStateRequest):
    request = browser_assist_registry.update_state(
        assist_id,
        extension_id=body.extension_id,
        state=body.state,
        error=body.error,
        detail=body.detail,
    )
    if not request:
        raise HTTPException(404, "browser assist request not found")
    return {"ok": True, "request": request}


@router.post("/import-account")
def import_browser_account(body: BrowserImportAccountRequest):
    platform = _text(body.platform) or "lingya_qq"
    if platform != "lingya_qq":
        raise HTTPException(400, "browser cookie import currently supports platform=lingya_qq only")
    if not body.cookies and not _text(body.cookie):
        raise HTTPException(400, "No Lingya cookies were provided")

    cookie_source = {
        "cookies": _cookie_payloads(body.cookies),
        "cookie": body.cookie,
    }
    cookies = extract_lingya_qq_cookies(cookie_source)
    if not cookies:
        raise HTTPException(400, "No allowlisted Lingya cookies were found in the browser payload")

    fields = build_lingya_qq_account_fields(cookie_source)
    vuid, vusession, _vurefresh, vdevice_guid = _validate_lingya_cookie_fields(fields)
    name = _first_text(body.name, fields.get("nick"), f"browser-{vuid}", f"browser-{vdevice_guid}")

    existing = _find_existing_lingya_account(email=name, vuid=vuid)
    existing_account = accounts_service.get_account(int(existing.id)) if existing and existing.id else None
    overview = _browser_import_overview(
        name=name,
        fields=fields,
        vuid=vuid,
        vdevice_guid=vdevice_guid,
        body=body,
        existing_overview=(existing_account or {}).get("overview"),
    )
    if existing and existing.id:
        account = accounts_service.update_account(
            int(existing.id),
            AccountUpdateCommand(
                user_id=vuid,
                lifecycle_status="registered",
                overview=overview,
                credentials=fields,
                primary_token=vusession,
            ),
        )
        action = "updated"
    else:
        account = accounts_service.create_account(
            AccountCreateCommand(
                platform="lingya_qq",
                email=name,
                password="",
                user_id=vuid,
                lifecycle_status="registered",
                overview=overview,
                credentials=fields,
                primary_token=vusession,
            )
        )
        action = "created"

    if not account:
        raise HTTPException(500, "Lingya account import failed")

    imported_names = [name for name in LINGYA_QQ_COOKIE_NAMES if cookies.get(name)]
    return {
        "ok": True,
        "action": action,
        "account": _serialize_import_account(account, name=name),
        "cookies": {
            "count": len(imported_names),
            "names": imported_names,
        },
    }
