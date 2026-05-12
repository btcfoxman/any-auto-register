from __future__ import annotations

import time
from typing import Any

from core.base_platform import Account, AccountStatus, BasePlatform, RegisterConfig
from core.base_sms import create_sms_provider
from core.lingya2api_sync import sync_account_to_lingya2api
from core.registry import register
from infrastructure.provider_definitions_repository import ProviderDefinitionsRepository
from infrastructure.provider_settings_repository import ProviderSettingsRepository
from platforms.lingya_qq.cookies import (
    build_lingya_qq_account_fields,
    extract_lingya_qq_cookies,
)
from platforms.lingya_qq.core import (
    DEFAULT_VIDEO_UPLOAD_SERVICE_ID,
    LingYaQQClient,
    VIDEO_APPID,
    VVERSION_PLATFORM,
    normalize_area_code,
    normalize_lingya_phone,
)
from platforms.lingya_qq.publish import fetch_lingya_qq_publish_asset


LINGYA_QQ_MAX_SMS_TIMEOUT_SECONDS = 300
LINGYA_QQ_SESSION_RETRY_CODES = {20447, 20409, 20433, 20431, 20411}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _sms_timeout(value: Any, default: int = LINGYA_QQ_MAX_SMS_TIMEOUT_SECONDS) -> int:
    return max(1, min(LINGYA_QQ_MAX_SMS_TIMEOUT_SECONDS, _as_int(value, default)))


def _as_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "是"}


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _session_error_code(value: Any) -> int:
    if isinstance(value, dict):
        for key in ("ret", "code", "error_code"):
            code = _as_int(value.get(key), 0)
            if code:
                return code
        inner = value.get("data")
        if isinstance(inner, dict):
            return _session_error_code(inner)
        return 0
    text = str(value or "")
    for code in LINGYA_QQ_SESSION_RETRY_CODES:
        if str(code) in text:
            return code
    return 0


def _is_session_retry_error(value: Any) -> bool:
    return _session_error_code(value) in LINGYA_QQ_SESSION_RETRY_CODES


def _global_config_value(key: str, default: Any = "") -> Any:
    try:
        from core.config_store import config_store

        return config_store.get(key, default)
    except Exception:
        return default


def _set_global_config_values(values: dict[str, Any]) -> None:
    payload = {
        key: str(value)
        for key, value in values.items()
        if key and value not in (None, "")
    }
    if not payload:
        return
    try:
        from core.config_store import config_store

        config_store.set_many(payload)
    except Exception:
        return


def _first_value(*values: Any, default: Any = "") -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return default


def _resolve_sms_runtime(extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    settings_repo = ProviderSettingsRepository()
    definitions_repo = ProviderDefinitionsRepository()
    provider_key = str(
        extra.get("sms_provider")
        or extra.get("phone_provider")
        or settings_repo.get_default_provider_key("sms")
        or ""
    ).strip()
    if not provider_key:
        if extra.get("uomsg_token") or extra.get("token"):
            provider_key = "uomsg_api"
        elif extra.get("sms_activate_api_key"):
            provider_key = "sms_activate_api"
        elif extra.get("herosms_api_key"):
            provider_key = "herosms_api"
        elif extra.get("smsbower_api_key"):
            provider_key = "smsbower_api"
        elif extra.get("haozhuma_user") and extra.get("haozhuma_password"):
            provider_key = "haozhuma_api"
    if not provider_key:
        raise RuntimeError("LingYaQQ requires an SMS provider. Configure a default SMS provider in Settings, or pass sms_provider and its API key in task parameters.")
    definition = definitions_repo.get_by_key("sms", provider_key)
    settings = settings_repo.resolve_runtime_settings("sms", provider_key, extra) if definition else dict(extra)
    return provider_key, settings


def _resolve_sms_service(settings: dict[str, Any], extra: dict[str, Any]) -> str:
    for key in (
        "lingya_qq_sms_service",
        "sms_service",
        "herosms_service",
        "herosms_default_service",
        "smsbower_service",
        "smsbower_default_service",
        "haozhuma_sid",
        "sms_activate_service",
        "sms_activate_default_service",
    ):
        value = str(extra.get(key) or settings.get(key) or "").strip()
        if value:
            return value
    return "qq"


def _resolve_sms_country(settings: dict[str, Any], extra: dict[str, Any]) -> str:
    for key in (
        "sms_country",
        "phone_country",
        "uomsg_province",
        "haozhuma_province",
        "herosms_country",
        "herosms_default_country",
        "smsbower_country",
        "smsbower_default_country",
        "sms_activate_country",
        "sms_activate_default_country",
    ):
        value = str(extra.get(key) or settings.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_login_response(data: dict[str, Any]) -> dict[str, Any]:
    return (((data.get("data") or {}).get("rsp") or {}).get("login_response") or {})


def _extract_refresh_response(data: dict[str, Any]) -> dict[str, Any]:
    rsp = ((data.get("data") or {}).get("rsp") or {})
    return rsp.get("refresh_response") or rsp.get("login_response") or {}


def _extract_user_profile(data: dict[str, Any]) -> dict[str, Any]:
    return (
        (((data.get("data") or {}).get("user_item") or {}).get("profile_info") or {}).get("user_info")
        or {}
    )


def _quota_summary(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "quota_balance": data.get("quota_balance"),
        "quota_sum": data.get("quota_sum"),
    }


def _account_extra_value(account: Account, key: str, default: Any = "") -> Any:
    extra = dict(account.extra or {})
    if extra.get(key) not in (None, ""):
        return extra.get(key)
    overview = extra.get("account_overview") if isinstance(extra.get("account_overview"), dict) else {}
    if overview.get(key) not in (None, ""):
        return overview.get(key)
    legacy = overview.get("legacy_extra") if isinstance(overview.get("legacy_extra"), dict) else {}
    if legacy.get(key) not in (None, ""):
        return legacy.get(key)
    return default


def _account_value_source(account: Account) -> dict[str, Any]:
    extra = dict(account.extra or {})
    overview = extra.get("account_overview") if isinstance(extra.get("account_overview"), dict) else {}
    legacy = overview.get("legacy_extra") if isinstance(overview.get("legacy_extra"), dict) else {}
    source = dict(legacy)
    source.update({key: value for key, value in overview.items() if key != "legacy_extra"})
    source.update(extra)
    return source


def _timestamp(value: Any) -> int | None:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        return None
    if result > 10_000_000_000:
        result = result // 1000
    return result if result > 0 else None


def _session_refresh_due(source: dict[str, Any], *, now: int | None = None, buffer_seconds: int = 900) -> bool:
    cookies = extract_lingya_qq_cookies(source)
    merged = {**source, **cookies}
    if not (merged.get("v_vurefresh") or merged.get("vurefresh")):
        return False
    current = int(now or time.time())

    next_refresh = _timestamp(merged.get("_new_next_refresh_time"))
    if next_refresh is not None:
        return current >= next_refresh

    expires_at = _timestamp(merged.get("vusession_expire_timestamp"))
    if expires_at is not None:
        return current >= max(0, expires_at - buffer_seconds)

    last_refresh = _timestamp(merged.get("last_refresh_second") or merged.get("v_login_time_init"))
    refresh_after = _as_int(merged.get("v_next_refresh_time") or merged.get("min_expire_time"), 0)
    if last_refresh and refresh_after > 0:
        return current >= last_refresh + max(60, refresh_after - buffer_seconds)
    return False


def _account_with_extra(account: Account, extra: dict[str, Any]) -> Account:
    return Account(
        platform=account.platform,
        email=account.email,
        password=account.password,
        user_id=account.user_id,
        region=account.region,
        token=account.token,
        status=account.status,
        trial_end_time=account.trial_end_time,
        extra=extra,
        created_at=account.created_at,
    )


@register
class LingYaQQPlatform(BasePlatform):
    name = "lingya_qq"
    display_name = "LingYaQQ"
    version = "1.0.0"
    supported_executors = ["protocol", "manual_assisted"]
    supported_identity_modes = ["manual_phone"]
    capabilities = ["query_state"]

    def __init__(self, config: RegisterConfig = None, mailbox=None):
        super().__init__(config)
        self.mailbox = mailbox
        self._last_check_overview: dict[str, Any] = {}

    def _prepare_registration_password(self, password: str | None) -> str | None:
        return ""

    def _client(
        self,
        *,
        vdevice_guid: str | None = None,
        cookies: dict[str, Any] | None = None,
        proxy: str | None = None,
    ) -> LingYaQQClient:
        extra = dict(self.config.extra or {}) if self.config else {}
        cookie_map = cookies if cookies is not None else extract_lingya_qq_cookies(extra)
        return LingYaQQClient(
            proxy=proxy or (self.config.proxy if self.config else None) or str(extra.get("proxy_url") or extra.get("proxy") or "").strip() or None,
            vdevice_guid=vdevice_guid or str(extra.get("vdevice_guid") or "").strip() or None,
            cookies=cookie_map,
            timeout=_as_int(extra.get("lingya_qq_http_timeout"), 20),
        )

    def get_platform_actions(self) -> list:
        actions = super().get_platform_actions()
        actions.append(
            {
                "id": "relogin_sms",
                "label": "LingYaQQ relogin by SMS",
                "sync": False,
                "params": [
                    {"key": "sms_timeout", "label": "SMS timeout seconds", "type": "number"},
                    {"key": "uomsg_keyword", "label": "SMS keyword (optional)", "type": "text"},
                    {"key": "uomsg_province", "label": "Province (optional)", "type": "text"},
                ],
            }
        )
        actions.append(
            {
                "id": "keepalive_sync",
                "label": "LingYaQQ keepalive + sync",
                "sync": False,
                "params": [
                    {"key": "force_refresh", "label": "Force WebRefresh (true/false)", "type": "text"},
                ],
            }
        )
        actions.append(
            {
                "id": "sync_lingya2api",
                "label": "Sync to lingya2api",
                "sync": False,
                "params": [],
            }
        )
        actions.append(
            {
                "id": "daily_sign_in",
                "label": "LingYaQQ daily sign-in",
                "sync": False,
                "params": [
                    {"key": "force", "label": "Force sign-in (true/false)", "type": "text"},
                ],
            }
        )
        actions.append(
            {
                "id": "publish_work",
                "label": "LingYaQQ publish work",
                "sync": False,
                "params": [
                    {"key": "source_url", "label": "Third-party GET source URL", "type": "text"},
                    {"key": "source_timeout", "label": "Source request timeout seconds", "type": "number"},
                    {"key": "source_retries", "label": "Source request retries", "type": "number"},
                    {"key": "upload_service_id", "label": "Video upload serviceId", "type": "text"},
                    {"key": "initial_delay", "label": "Audit initial delay seconds", "type": "number"},
                    {"key": "poll_interval", "label": "Audit poll interval seconds", "type": "number"},
                    {"key": "timeout", "label": "Audit timeout seconds", "type": "number"},
                    {"key": "force", "label": "Force publish even if work exists", "type": "text"},
                ],
            }
        )
        return actions

    def register(self, email: str = None, password: str = None) -> Account:
        extra = dict(self.config.extra or {}) if self.config else {}
        provider_key, sms_settings = _resolve_sms_runtime(extra)
        if self.config and self.config.proxy and not str(sms_settings.get("sms_proxy") or sms_settings.get("proxy") or "").strip():
            sms_settings["sms_proxy"] = self.config.proxy
        service = _resolve_sms_service(sms_settings, extra)
        country = _resolve_sms_country(sms_settings, extra)
        area_code = normalize_area_code(extra.get("lingya_qq_area_code") or extra.get("phone_area_code") or "+86")
        timeout = _sms_timeout(extra.get("lingya_qq_sms_timeout") or extra.get("sms_code_timeout"))

        provider = create_sms_provider(provider_key, sms_settings)
        activation = None
        completed = False
        try:
            self.log(f"Renting LingYaQQ phone number from {provider_key}: service={service} country={country or 'default'}")
            activation = provider.get_number(service=service, country=country)
            raw_phone = str(activation.phone_number or "").strip()
            phone = normalize_lingya_phone(raw_phone, area_code)
            if not phone:
                raise RuntimeError("SMS provider did not return a usable phone number")
            self.log(f"Phone number rented: {phone}")
            manual_proxy = str((self.config.proxy if self.config else "") or "").strip()
            if manual_proxy:
                self.log(f"LingYaQQ manual browser proxy: {manual_proxy}")
                self.log("Open Chrome/Edge with the same proxy before sending the SMS on lingya.qq.com.")
            else:
                self.log("LingYaQQ proxy mode is off for this task; use a direct browser connection for manual SMS sending.")
            self.log("Open https://lingya.qq.com in normal Chrome/Edge, enter this phone number, complete the graphic CAPTCHA, then send SMS.")
            self.log(f"Waiting for SMS verification code, timeout {timeout} seconds.")

            if _as_bool(extra.get("lingya_qq_auto_send_sms"), False):
                self._client().send_sms(phone=phone, area_code=area_code)
                self.log("Tried protocol SMS sending. Disable lingya_qq_auto_send_sms and send manually if risk control appears.")

            code = provider.get_code(activation.activation_id, timeout=timeout)
            if not code:
                raise RuntimeError("LingYaQQ SMS verification code was not received")

            client = self._client()
            login = client.login_with_phone_code(phone=phone, code=code, area_code=area_code)
            login_response = _extract_login_response(login)
            vuid = str(login_response.get("vuid") or "").strip()
            vusession = str(login_response.get("vusession") or "").strip()
            vurefresh = str(login_response.get("vurefresh") or "").strip()
            if not vuid or not vusession:
                raise RuntimeError("LingYaQQ login response is missing vuid/vusession")

            profile_data = client.get_user_profile(vuid)
            profile = _extract_user_profile(profile_data)
            quota = client.get_user_quota()
            quota_overview = _quota_summary(quota)
            account_label = f"{area_code}{phone}"
            nick = str(profile.get("nickname") or ((login_response.get("user_info") or {}).get("user_nick")) or "")
            cookie_fields = build_lingya_qq_account_fields(
                extra,
                login_response=login_response,
                profile=profile,
                vdevice_guid=client.vdevice_guid,
                video_appid=VIDEO_APPID,
                video_platform=VVERSION_PLATFORM,
            )
            overview = {
                "platform": self.name,
                "valid": True,
                "remote_email": account_label,
                "phone": account_label,
                "vuid": vuid,
                "nick": nick,
                **quota_overview,
                "chips": [
                    "手机号登录",
                    f"额度 {quota_overview.get('quota_balance', '-')}/{quota_overview.get('quota_sum', '-')}",
                ],
            }
            try:
                provider.report_success(activation.activation_id)
            except Exception as exc:
                self.log(f"SMS provider success report failed; LingYaQQ login is still usable: {exc}")
            completed = True
            return Account(
                platform=self.name,
                email=account_label,
                password="",
                user_id=vuid,
                token=vusession,
                status=AccountStatus.REGISTERED,
                extra={
                    **cookie_fields,
                    "phone": account_label,
                    "area_code": area_code,
                    "local_phone": phone,
                    "vuid": vuid,
                    "vusession": vusession,
                    "vurefresh": vurefresh,
                    "vusession_expire_timestamp": str(login_response.get("vusession_expire_timestamp") or ""),
                    "vusession_expire_in": int(login_response.get("vusession_expire_in") or 0),
                    "vdevice_guid": client.vdevice_guid,
                    "video_appid": VIDEO_APPID,
                    "vversion_platform": VVERSION_PLATFORM,
                    "nick": nick,
                    "avatar": str(profile.get("avatar") or ((login_response.get("user_info") or {}).get("user_head")) or ""),
                    "quota": quota_overview,
                    "account_overview": overview,
                },
            )
        finally:
            if activation and not completed:
                try:
                    provider.cancel(activation.activation_id)
                    self.log(f"Released unfinished phone number: activation_id={activation.activation_id}")
                except Exception:
                    pass

    def _handle_relogin_sms(self, account: Account, params: dict) -> dict:
        account_source = _account_value_source(account)
        area_code = normalize_area_code(
            params.get("area_code")
            or account_source.get("area_code")
            or "+86"
        )
        raw_phone = (
            params.get("phone")
            or account_source.get("local_phone")
            or account_source.get("phone")
            or account.email
        )
        phone = normalize_lingya_phone(str(raw_phone or ""), area_code)
        if not phone:
            return {"ok": False, "error": "Account has no phone number available for SMS relogin"}

        sms_extra = dict(params or {})
        sms_extra["sms_provider"] = str(params.get("sms_provider") or "uomsg_api").strip()
        if params.get("uomsg_keyword"):
            sms_extra["uomsg_keyword"] = params.get("uomsg_keyword")
        if params.get("uomsg_province"):
            sms_extra["uomsg_province"] = params.get("uomsg_province")

        provider_key, sms_settings = _resolve_sms_runtime(sms_extra)
        if provider_key not in {"uomsg", "uomsg_api"}:
            return {"ok": False, "error": "SMS relogin currently supports only UOMsg. Configure UOMsg as the SMS provider first."}
        sms_settings["uomsg_phone"] = phone
        if self.config and self.config.proxy and not str(sms_settings.get("sms_proxy") or sms_settings.get("proxy") or "").strip():
            sms_settings["sms_proxy"] = self.config.proxy

        service = _resolve_sms_service(sms_settings, sms_extra)
        country = _resolve_sms_country(sms_settings, sms_extra)
        timeout = _sms_timeout(params.get("sms_timeout") or params.get("lingya_qq_sms_timeout"))
        provider = create_sms_provider(provider_key, sms_settings)
        activation = None
        completed = False
        try:
            self.log(f"Using UOMsg for SMS relogin on existing phone: {area_code}{phone}")
            activation = provider.get_number(service=service, country=country)
            next_phone = normalize_lingya_phone(str(activation.phone_number or phone), area_code) or phone
            baseline_text = ""
            get_message_text = getattr(provider, "get_message_text", None)
            if callable(get_message_text):
                try:
                    old_text = str(get_message_text(activation.activation_id) or "").strip()
                    if old_text and "尚未收到" not in old_text:
                        baseline_text = old_text
                        self.log("Recorded current old SMS; waiting for a newer SMS message.")
                except Exception as exc:
                    self.log(f"Failed to read old SMS baseline; continuing to wait for a new code: {exc}")

            self.log("Open https://lingya.qq.com in normal Chrome/Edge, enter this phone number, complete the graphic CAPTCHA, then send SMS.")
            self.log(f"Waiting for relogin SMS, timeout {timeout} seconds.")

            get_code_after = getattr(provider, "get_code_after", None)
            if callable(get_code_after):
                code = get_code_after(activation.activation_id, timeout=timeout, ignore_text=baseline_text)
            else:
                code = provider.get_code(activation.activation_id, timeout=timeout)
            if not code:
                raise RuntimeError("LingYaQQ relogin SMS verification code was not received")

            account_cookies = extract_lingya_qq_cookies(account_source)
            vdevice_guid = str(account_source.get("vdevice_guid") or "").strip() or None
            account_proxy = str(account_source.get("proxy_url") or account_source.get("proxy") or "").strip() or None
            client = self._client(vdevice_guid=vdevice_guid, cookies=account_cookies, proxy=account_proxy)
            login = client.login_with_phone_code(phone=next_phone, code=code, area_code=area_code)
            login_response = _extract_login_response(login)
            vuid = str(login_response.get("vuid") or "").strip()
            vusession = str(login_response.get("vusession") or "").strip()
            vurefresh = str(login_response.get("vurefresh") or "").strip()
            if not vuid or not vusession:
                raise RuntimeError("LingYaQQ relogin response is missing vuid/vusession")

            profile_data = client.get_user_profile(vuid)
            profile = _extract_user_profile(profile_data)
            quota = client.get_user_quota()
            quota_overview = _quota_summary(quota)
            nick = str(profile.get("nickname") or ((login_response.get("user_info") or {}).get("user_nick")) or "")
            cookie_fields = build_lingya_qq_account_fields(
                account_source,
                login_response=login_response,
                profile=profile,
                vdevice_guid=client.vdevice_guid,
                video_appid=VIDEO_APPID,
                video_platform=VVERSION_PLATFORM,
            )
            try:
                provider.report_success(activation.activation_id)
            except Exception as exc:
                self.log(f"UOMsg release failed; LingYaQQ relogin is still usable: {exc}")
            completed = True
            account_label = f"{area_code}{next_phone}"
            return {
                "ok": True,
                "data": {
                    **cookie_fields,
                    "message": "LingYaQQ relogin succeeded",
                    "session_refreshed": True,
                    "valid": True,
                    "phone": account_label,
                    "local_phone": next_phone,
                    "area_code": area_code,
                    "vuid": vuid,
                    "vusession": vusession,
                    "vurefresh": vurefresh,
                    "vusession_expire_timestamp": str(login_response.get("vusession_expire_timestamp") or ""),
                    "vusession_expire_in": int(login_response.get("vusession_expire_in") or 0),
                    "vdevice_guid": client.vdevice_guid,
                    "nick": nick,
                    "avatar": str(profile.get("avatar") or ((login_response.get("user_info") or {}).get("user_head")) or ""),
                    **quota_overview,
                },
            }
        finally:
            if activation and not completed:
                try:
                    provider.cancel(activation.activation_id)
                    self.log(f"Released unfinished relogin phone number: activation_id={activation.activation_id}")
                except Exception:
                    pass

    def _client_from_account(self, account: Account) -> tuple[dict[str, Any], dict[str, Any], LingYaQQClient]:
        source = _account_value_source(account)
        cookie_fields = build_lingya_qq_account_fields(
            source,
            vdevice_guid=str(source.get("vdevice_guid") or "").strip() or None,
            video_appid=VIDEO_APPID,
            video_platform=VVERSION_PLATFORM,
        )
        vdevice_guid = str(cookie_fields.get("vdevice_guid") or source.get("vdevice_guid") or "").strip()
        if not vdevice_guid:
            raise RuntimeError("LingYaQQ account is missing vdevice_guid")
        client = self._client(
            vdevice_guid=vdevice_guid,
            cookies=extract_lingya_qq_cookies(cookie_fields or source),
            proxy=str(source.get("proxy_url") or source.get("proxy") or "").strip() or None,
        )
        return source, cookie_fields, client

    def _runtime_value(self, source: dict[str, Any], params: dict[str, Any], key: str, default: Any = "") -> Any:
        config_extra = dict(self.config.extra or {}) if self.config else {}
        return _first_value(
            (params or {}).get(key),
            config_extra.get(key),
            source.get(key),
            _global_config_value(key, ""),
            default=default,
        )

    def _sleep_with_cancel(self, seconds: float, cancel_check=None) -> None:
        end_at = time.time() + max(float(seconds or 0), 0)
        while time.time() < end_at:
            if callable(cancel_check) and cancel_check():
                raise RuntimeError("LingYaQQ follow-up cancelled")
            time.sleep(min(5.0, max(0.0, end_at - time.time())))

    def _daily_sign_item(self, panel: dict[str, Any]) -> dict[str, Any]:
        data = panel.get("data") if isinstance(panel.get("data"), dict) else panel
        items = data.get("items") if isinstance(data, dict) else []
        if not isinstance(items, list):
            return {}
        for item in items:
            if isinstance(item, dict) and _as_int(item.get("type"), 0) == 1:
                return item
        return next((item for item in items if isinstance(item, dict)), {})

    def _daily_sign_state(self, panel: dict[str, Any], *, force: bool = False) -> tuple[bool, bool, dict[str, Any]]:
        item = self._daily_sign_item(panel)
        button = item.get("button_info") if isinstance(item.get("button_info"), dict) else {}
        status = _as_int(button.get("status"), 0)
        text = str(button.get("button_text") or "")
        if force:
            return True, False, item
        if status == 2 or "\u5df2\u9886\u53d6" in text:
            return False, True, item
        if status == 1 or "\u9886\u53d6" in text:
            return True, False, item
        return False, False, item

    def _handle_daily_sign_in(self, account: Account, params: dict | None = None) -> dict:
        params = params or {}
        source, cookie_fields, client = self._client_from_account(account)
        force = _as_bool(params.get("force"), False)
        try:
            panel = client.get_credits_panel(False)
        except Exception as exc:
            self.log(f"LingYaQQ credits panel unavailable; daily sign-in skipped: {exc}")
            quota: dict[str, Any] = {}
            try:
                quota = client.get_user_quota()
            except Exception as quota_exc:
                self.log(f"LingYaQQ quota refresh skipped after credits panel error: {quota_exc}")
            quota_overview = _quota_summary(quota)
            return {
                "ok": True,
                "data": {
                    **cookie_fields,
                    "message": "LingYaQQ daily sign-in skipped because the credits panel is unavailable",
                    "daily_sign_in_status": "panel_unavailable",
                    "daily_sign_in_at": int(time.time()),
                    "daily_sign_signed": False,
                    "daily_sign_already_signed": False,
                    "daily_sign_error": str(exc),
                    **quota_overview,
                },
            }
        need_sign, already_signed, _ = self._daily_sign_state(panel, force=force)
        sign_response: dict[str, Any] = {}
        signed = False
        if need_sign:
            sign_response = client.credits_panel_sign_in()
            if _as_int(sign_response.get("ret"), 0) != 0:
                return {"ok": False, "error": f"LingYaQQ daily sign-in failed: {sign_response.get('msg') or sign_response}"}
            sign_data = sign_response.get("data") if isinstance(sign_response.get("data"), dict) else {}
            signed = bool(sign_data.get("isSignInSuccess") or sign_data.get("is_sign_in_success"))
            if not signed:
                return {"ok": False, "error": f"LingYaQQ daily sign-in was not accepted: {sign_response}"}
            panel = client.get_credits_panel(False)

        quota: dict[str, Any] = {}
        try:
            quota = client.get_user_quota()
        except Exception as exc:
            self.log(f"LingYaQQ quota refresh skipped after daily sign-in: {exc}")
        quota_overview = _quota_summary(quota)
        status = "signed" if signed else ("already_signed" if already_signed else "not_available")
        return {
            "ok": True,
            "data": {
                **cookie_fields,
                "message": "LingYaQQ daily sign-in completed",
                "daily_sign_in_status": status,
                "daily_sign_in_at": int(time.time()),
                "daily_sign_signed": signed,
                "daily_sign_already_signed": already_signed,
                **quota_overview,
            },
        }

    def _work_generation_statuses(self, payload: dict[str, Any]) -> list[int]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        statuses: list[int] = []
        if not isinstance(data, dict):
            return statuses
        for key in (
            "transcoding_status",
            "highlight_scene_status",
            "sequence_frames_status",
            "highlight_scene_frames_status",
        ):
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("status")
            if value not in (None, ""):
                statuses.append(_as_int(value, 0))
        return statuses

    def _wait_work_generation(
        self,
        client: LingYaQQClient,
        vid: str,
        *,
        poll_interval: int,
        timeout: int,
        cancel_check=None,
    ) -> dict[str, Any]:
        deadline = time.time() + max(timeout, 0)
        last_payload: dict[str, Any] = {}
        while True:
            if callable(cancel_check) and cancel_check():
                raise RuntimeError("LingYaQQ follow-up cancelled")
            last_payload = client.get_work_generation_status(vid)
            statuses = self._work_generation_statuses(last_payload)
            if statuses and all(status == 1 for status in statuses):
                return last_payload
            if any(status == 2 for status in statuses):
                raise RuntimeError(f"LingYaQQ work generation failed: {last_payload}")
            if time.time() >= deadline:
                raise TimeoutError(f"LingYaQQ work generation timed out: {last_payload}")
            self._sleep_with_cancel(min(max(poll_interval, 1), max(0, deadline - time.time())), cancel_check)

    def _work_items(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict):
            return []
        for key in ("work_list", "works", "items", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        for key in ("page_context", "page", "result"):
            value = data.get(key)
            if isinstance(value, dict):
                nested = self._work_items(value)
                if nested:
                    return nested
        return []

    def _work_vid(self, work: dict[str, Any]) -> str:
        base = work.get("base_info") if isinstance(work.get("base_info"), dict) else {}
        return str(
            work.get("vid")
            or base.get("vid")
            or work.get("playback_medium_id")
            or base.get("playback_medium_id")
            or ""
        ).strip()

    def _find_work_by_vid(self, payload: dict[str, Any], vid: str) -> dict[str, Any]:
        for item in self._work_items(payload):
            if self._work_vid(item) == str(vid):
                return item
        return {}

    def _work_title(self, work: dict[str, Any]) -> str:
        base = work.get("base_info") if isinstance(work.get("base_info"), dict) else {}
        return str(
            work.get("title")
            or base.get("title")
            or work.get("work_title")
            or base.get("work_title")
            or ""
        ).strip()

    def _published_work_status(self, work: dict[str, Any], *, filter_status: int = 0) -> int:
        status = _as_int(work.get("work_status"), 0)
        if status == 1 or (filter_status == 3 and status == 0):
            return 1
        return status

    def _find_existing_published_work(self, client: LingYaQQClient) -> dict[str, Any]:
        for filter_status in (3, 1):
            payload = client.get_my_work_list(filter_by_status=filter_status)
            for work in self._work_items(payload):
                if self._published_work_status(work, filter_status=filter_status) == 1:
                    return work
        return {}

    def _quota_overview_or_empty(self, client: LingYaQQClient, *, context: str) -> dict[str, Any]:
        try:
            return _quota_summary(client.get_user_quota())
        except Exception as exc:
            self.log(f"LingYaQQ quota refresh skipped {context}: {exc}")
            return {}

    def _wait_publish_success(
        self,
        client: LingYaQQClient,
        vid: str,
        *,
        initial_delay: int,
        poll_interval: int,
        timeout: int,
        cancel_check=None,
    ) -> dict[str, Any]:
        self._sleep_with_cancel(initial_delay, cancel_check)
        deadline = time.time() + max(timeout, 0)
        last_work: dict[str, Any] = {}
        while True:
            if callable(cancel_check) and cancel_check():
                raise RuntimeError("LingYaQQ follow-up cancelled")
            for filter_status in (3, 1):
                payload = client.get_my_work_list(filter_by_status=filter_status)
                work = self._find_work_by_vid(payload, vid)
                if not work:
                    continue
                last_work = work
                work_status = _as_int(work.get("work_status"), 0)
                if work_status == 1 or (filter_status == 3 and work_status == 0):
                    return work
                if work_status in {4, 5}:
                    raise RuntimeError(f"LingYaQQ published work did not pass audit: {work}")
            if time.time() >= deadline:
                raise TimeoutError(f"LingYaQQ publish audit timed out: {last_work}")
            self._sleep_with_cancel(min(max(poll_interval, 1), max(0, deadline - time.time())), cancel_check)

    def _build_upload_work_payload(
        self,
        *,
        request_type: int,
        vid: str,
        title: str,
        description: str,
        cover_url: str,
        duration: int,
        cover_ratio: float,
        file_name: str,
        background_color: str = "",
        title_color: str = "",
        ai_content_types: list[int] | None = None,
    ) -> dict[str, Any]:
        return {
            "request_type": int(request_type),
            "vid": vid,
            "base_info": {
                "title": title,
                "description": description,
                "cover_url": cover_url,
                "duration": int(duration),
                "cover_ratio": float(cover_ratio),
                "file_name": file_name,
                "background_color": background_color,
                "title_color": title_color,
            },
            "related_info": {"tag_infos": [], "activity_infos": []},
            "creation_tools": [
                {"id": "", "category": 1, "tools": []},
                {"id": "", "category": 2, "tools": []},
                {"id": "", "category": 3, "tools": []},
                {"id": "", "category": 4, "tools": []},
            ],
            "highlight_scenes": [],
            "creation_processes": [],
            "biz_params": {},
            "playback_medium_id": vid,
            "ai_content_types": list(ai_content_types or []),
            "external_account_types": [],
            "is_scheduled_publish": False,
            "scheduled_publish_time": 0,
            "is_campus_zone": False,
        }

    def _handle_publish_work(self, account: Account, params: dict | None = None) -> dict:
        params = params or {}
        source, cookie_fields, client = self._client_from_account(account)
        force = _as_bool(params.get("force"), False)
        if not force and str(source.get("last_publish_status") or "").strip().lower() == "released":
            quota_overview = self._quota_overview_or_empty(client, context="after publish skip")
            return {
                "ok": True,
                "data": {
                    **cookie_fields,
                    "message": "LingYaQQ publish skipped because this account already has a released work",
                    "publish_skipped": True,
                    "publish_skip_reason": "local_released",
                    "last_publish_status": "released",
                    **quota_overview,
                },
            }
        if not force:
            try:
                existing_work = self._find_existing_published_work(client)
            except Exception as exc:
                self.log(f"LingYaQQ publish: existing work lookup skipped: {exc}")
                existing_work = {}
            if existing_work:
                quota_overview = self._quota_overview_or_empty(client, context="after existing work lookup")
                return {
                    "ok": True,
                    "data": {
                        **cookie_fields,
                        "message": "LingYaQQ publish skipped because a released work already exists",
                        "publish_skipped": True,
                        "publish_skip_reason": "remote_released",
                        "last_publish_vid": self._work_vid(existing_work),
                        "last_publish_title": self._work_title(existing_work),
                        "last_publish_status": "released",
                        "last_publish_work_status": self._published_work_status(existing_work, filter_status=3),
                        **quota_overview,
                    },
                }
        source_url = str(
            _first_value(
                params.get("source_url"),
                self._runtime_value(source, params, "lingya_qq_publish_source_url", ""),
            )
            or ""
        ).strip()
        if not source_url:
            return {"ok": False, "error": "LingYaQQ publish source URL is not configured"}

        source_timeout = _as_int(
            _first_value(params.get("source_timeout"), self._runtime_value(source, params, "lingya_qq_publish_source_timeout", 60)),
            60,
        )
        source_retries = _as_int(
            _first_value(params.get("source_retries"), self._runtime_value(source, params, "lingya_qq_publish_source_retries", 3)),
            3,
        )
        upload_service_id = str(
            _first_value(
                params.get("upload_service_id"),
                self._runtime_value(source, params, "lingya_qq_video_upload_service_id", DEFAULT_VIDEO_UPLOAD_SERVICE_ID),
            )
            or DEFAULT_VIDEO_UPLOAD_SERVICE_ID
        ).strip() or DEFAULT_VIDEO_UPLOAD_SERVICE_ID
        generation_timeout = _as_int(
            _first_value(params.get("generation_timeout"), self._runtime_value(source, params, "lingya_qq_publish_generation_timeout", 600)),
            600,
        )
        generation_poll_interval = _as_int(
            _first_value(params.get("generation_poll_interval"), self._runtime_value(source, params, "lingya_qq_publish_generation_poll_interval", 5)),
            5,
        )
        initial_delay = _as_int(
            _first_value(params.get("initial_delay"), self._runtime_value(source, params, "lingya_qq_publish_initial_delay", 600)),
            600,
        )
        poll_interval = _as_int(
            _first_value(params.get("poll_interval"), self._runtime_value(source, params, "lingya_qq_publish_poll_interval", 60)),
            60,
        )
        publish_timeout = _as_int(
            _first_value(params.get("timeout"), self._runtime_value(source, params, "lingya_qq_publish_timeout", 7200)),
            7200,
        )
        cancel_check = params.get("_cancel_check") if callable(params.get("_cancel_check")) else None
        defaults = {
            "cover_url": self._runtime_value(source, params, "lingya_qq_publish_cover_url", ""),
            "title": self._runtime_value(source, params, "lingya_qq_publish_title", ""),
            "description": self._runtime_value(source, params, "lingya_qq_publish_description", ""),
            "duration": self._runtime_value(source, params, "lingya_qq_publish_duration", 10),
            "cover_ratio": self._runtime_value(source, params, "lingya_qq_publish_cover_ratio", 0.75),
        }
        publish_defaults = {
            "lingya_qq_publish_source_url": source_url,
            "lingya_qq_publish_source_timeout": source_timeout,
            "lingya_qq_publish_source_retries": source_retries,
            "lingya_qq_publish_initial_delay": initial_delay,
            "lingya_qq_publish_poll_interval": poll_interval,
            "lingya_qq_publish_timeout": publish_timeout,
            "lingya_qq_publish_generation_timeout": generation_timeout,
            "lingya_qq_publish_generation_poll_interval": generation_poll_interval,
            "lingya_qq_video_upload_service_id": upload_service_id,
        }
        if defaults.get("cover_url"):
            publish_defaults["lingya_qq_publish_cover_url"] = defaults["cover_url"]
        _set_global_config_values(publish_defaults)

        self.log("LingYaQQ publish: fetching work asset from source URL by direct connection")
        asset = fetch_lingya_qq_publish_asset(
            source_url,
            timeout=source_timeout,
            proxy=None,
            retries=source_retries,
            defaults=defaults,
        )
        vuid = str(source.get("vuid") or cookie_fields.get("vuid") or account.user_id or "").strip()
        if not vuid:
            return {"ok": False, "error": "LingYaQQ account is missing vuid"}

        self.log("LingYaQQ publish: uploading cover image")
        cover_url = client.upload_image_bytes(
            asset.cover_bytes,
            filename=asset.cover_filename,
            content_type=asset.cover_content_type,
        )
        self.log("LingYaQQ publish: uploading video")
        video_info = client.upload_video_bytes(
            asset.video_bytes,
            filename=asset.video_filename,
            vuid=vuid,
            service_id=upload_service_id,
        )
        vid = str(video_info.get("vid") or "").strip()
        if not vid:
            return {"ok": False, "error": f"LingYaQQ video upload did not return vid: {video_info}"}

        self.log("LingYaQQ publish: waiting for work generation")
        generation = self._wait_work_generation(
            client,
            vid,
            poll_interval=generation_poll_interval,
            timeout=generation_timeout,
            cancel_check=cancel_check,
        )

        review = client.content_security_review(asset.title)
        review_data = review.get("data") if isinstance(review.get("data"), dict) else {}
        review_result = _as_int(review_data.get("result"), 0)
        if review_result != 1:
            return {"ok": False, "error": f"LingYaQQ title security review failed: {review}"}

        background_color = ""
        title_color = ""
        try:
            color_resp = client.get_cover_color_info(vid=vid, cover_url=cover_url)
            color_data = color_resp.get("data") if isinstance(color_resp.get("data"), dict) else {}
            if isinstance(color_data.get("color_info"), dict):
                color_data = {**color_data, **color_data["color_info"]}
            background_color = str(color_data.get("background_color") or color_data.get("backgroundColor") or "")
            title_color = str(color_data.get("title_color") or color_data.get("titleColor") or "")
        except Exception as exc:
            self.log(f"LingYaQQ publish: cover color lookup skipped: {exc}")

        draft_payload = self._build_upload_work_payload(
            request_type=2,
            vid=vid,
            title=asset.title,
            description=asset.description,
            cover_url=cover_url,
            duration=asset.duration,
            cover_ratio=asset.cover_ratio,
            file_name=asset.video_filename,
        )
        final_payload = self._build_upload_work_payload(
            request_type=1,
            vid=vid,
            title=asset.title,
            description=asset.description,
            cover_url=cover_url,
            duration=asset.duration,
            cover_ratio=asset.cover_ratio,
            file_name=asset.video_filename,
            background_color=background_color,
            title_color=title_color,
            ai_content_types=[1, 2, 3],
        )
        self.log("LingYaQQ publish: saving draft")
        client.upload_work(draft_payload)
        self.log("LingYaQQ publish: submitting work for audit")
        client.upload_work(final_payload)
        self.log("LingYaQQ publish: waiting for released status")
        work = self._wait_publish_success(
            client,
            vid,
            initial_delay=initial_delay,
            poll_interval=poll_interval,
            timeout=publish_timeout,
            cancel_check=cancel_check,
        )

        quota_overview = self._quota_overview_or_empty(client, context="after publish")
        return {
            "ok": True,
            "data": {
                **cookie_fields,
                "message": "LingYaQQ publish completed",
                "last_publish_vid": vid,
                "last_publish_title": asset.title,
                "last_publish_status": "released",
                "last_publish_work_status": _as_int(work.get("work_status"), 1),
                "last_publish_at": int(time.time()),
                "last_publish_review_result": review_result,
                "last_publish_generation_statuses": self._work_generation_statuses(generation),
                **publish_defaults,
                **quota_overview,
            },
        }

    def _handle_sync_lingya2api(self, account: Account, params: dict | None = None) -> dict:
        source, cookie_fields, _ = self._client_from_account(account)
        merged_extra = {**source, **cookie_fields}
        sync_result = sync_account_to_lingya2api(
            _account_with_extra(account, merged_extra),
            log_fn=self.log,
            heartbeat=_as_bool((params or {}).get("heartbeat"), False),
        )
        if not sync_result:
            return {"ok": False, "error": "Lingya2API is not configured or sync failed"}
        return {
            "ok": True,
            "data": {
                **cookie_fields,
                "message": "LingYaQQ cookie synced to lingya2api",
                "lingya2api_synced": True,
                "lingya2api": sync_result,
            },
        }

    def _handle_keepalive_sync(self, account: Account, params: dict | None = None) -> dict:
        params = params or {}
        source, cookie_fields, client = self._client_from_account(account)
        refresh_quota = _as_bool(params.get("refresh_quota"), True)
        main_login = str(
            source.get("v_main_login")
            or source.get("main_login")
            or cookie_fields.get("v_main_login")
            or "wx"
        ).strip() or "wx"

        force_refresh = _as_bool(params.get("force_refresh"), False)
        should_refresh = force_refresh or _session_refresh_due({**source, **cookie_fields})
        refresh_payload: dict[str, Any] = {}
        refresh_response: dict[str, Any] = {}
        session_refreshed = False

        def refresh_session(reason: str) -> None:
            nonlocal cookie_fields, refresh_payload, refresh_response, session_refreshed
            self.log(f"LingYaQQ keepalive refreshing session: {reason}")
            refresh_payload = client.refresh_session(main_login=main_login)
            refresh_response = _extract_refresh_response(refresh_payload)
            latest_cookies = {}
            cookie_dict = getattr(client, "cookie_dict", None)
            if callable(cookie_dict):
                latest_cookies = cookie_dict()
            cookie_fields = build_lingya_qq_account_fields(
                {**source, **cookie_fields, **latest_cookies},
                login_response=refresh_response,
                vdevice_guid=client.vdevice_guid,
                video_appid=VIDEO_APPID,
                video_platform=VVERSION_PLATFORM,
            )
            session_refreshed = bool(refresh_response.get("vusession") or latest_cookies.get("v_vusession"))

        if should_refresh:
            refresh_session("due")

        hello: dict[str, Any] = {}
        for attempt in range(2):
            try:
                hello = client.hello()
            except Exception as exc:
                if attempt == 0 and _is_session_retry_error(exc):
                    refresh_session(f"hello retryable session error { _session_error_code(exc) }")
                    continue
                raise
            if _is_session_retry_error(hello):
                if attempt == 0:
                    refresh_session(f"hello retryable session response { _session_error_code(hello) }")
                    continue
                raise RuntimeError(f"LingYaQQ Hello failed after session refresh: {hello.get('msg') or hello}")
            break

        quota: dict[str, Any] = {}
        if refresh_quota:
            try:
                quota = client.get_user_quota()
            except Exception as exc:
                self.log(f"LingYaQQ quota refresh skipped: {exc}")
        quota_overview = _quota_summary(quota)

        data: dict[str, Any] = {
            **cookie_fields,
            "message": "LingYaQQ keepalive completed",
            "valid": True,
            "heartbeat_ok": True,
            "session_refreshed": session_refreshed,
            "hello_timestamp": hello.get("timestamp"),
            "hello_token_ok": bool(hello.get("token")),
            "v_main_login": main_login,
            **quota_overview,
        }
        if refresh_response:
            data["vusession_expire_timestamp"] = str(refresh_response.get("vusession_expire_timestamp") or "")
            data["vusession_expire_in"] = int(refresh_response.get("vusession_expire_in") or 0)

        sync_extra = {**source, **data}
        sync_result = sync_account_to_lingya2api(
            _account_with_extra(account, sync_extra),
            log_fn=self.log,
            heartbeat=False,
        )
        data["lingya2api_synced"] = bool(sync_result)
        if sync_result:
            data["lingya2api"] = sync_result
        return {"ok": True, "data": data}

    def _load_state(self, account: Account) -> dict[str, Any]:
        result = self._handle_keepalive_sync(account, {"refresh_quota": "true"})
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "LingYaQQ keepalive check failed"))
        data = dict(result.get("data") or {})
        summary = {
            "valid": bool(data.get("heartbeat_ok")) and bool(data.get("hello_token_ok")),
            "vuid": data.get("vuid") or account.user_id,
            "phone": data.get("phone") or account.email,
            "nick": data.get("nick", ""),
            **_quota_summary(data),
            "hello_timestamp": data.get("hello_timestamp"),
            "chips": [
                f"额度 {data.get('quota_balance', '-')}/{data.get('quota_sum', '-')}",
            ],
        }
        return {"summary": summary, "quota": data, "hello": data, "profile": {}}

    def check_valid(self, account: Account) -> bool:
        try:
            state = self._load_state(account)
        except Exception:
            self._last_check_overview = {"valid": False}
            return False
        self._last_check_overview = dict(state.get("summary") or {})
        return bool(self._last_check_overview.get("valid"))

    def get_last_check_overview(self) -> dict[str, Any]:
        return dict(self._last_check_overview or {})

    def get_quota(self, account: Account) -> dict:
        return self._load_state(account).get("summary", {})

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        if action_id == "relogin_sms":
            return self._handle_relogin_sms(account, params or {})
        if action_id == "keepalive_sync":
            return self._handle_keepalive_sync(account, params or {})
        if action_id == "sync_lingya2api":
            return self._handle_sync_lingya2api(account, params or {})
        if action_id == "daily_sign_in":
            return self._handle_daily_sign_in(account, params or {})
        if action_id == "publish_work":
            return self._handle_publish_work(account, params or {})
        if action_id in {"query_state", "get_account_state", "get_user_info"}:
            return {"ok": True, "data": self._load_state(account).get("summary", {})}
        return super().execute_action(action_id, account, params)
