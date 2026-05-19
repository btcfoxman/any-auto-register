"""Freebeat platform plugin."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.base_mailbox import BaseMailbox, MailboxAccount, create_mailbox
from core.base_platform import Account, AccountStatus, BasePlatform, RegisterConfig
from core.freebeat2api_sync import sync_account_to_freebeat2api
from core.registration import OtpSpec, ProtocolMailboxAdapter, RegistrationResult
from core.registry import register
from platforms.freebeat.core import (
    FREEBEAT_DEFAULT_VERIFY_SOURCE,
    FREEBEAT_ONBOARDING_CODE,
    FreebeatClient,
    load_freebeat_account_state,
    summarize_freebeat_account_state,
)


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _runtime_value(extra: dict[str, Any], key: str, default: Any = "") -> Any:
    if extra.get(key) not in (None, ""):
        return extra.get(key)
    try:
        from core.config_store import config_store

        return config_store.get(key, default)
    except Exception:
        return default


def _status_from_overview(overview: dict[str, Any]) -> AccountStatus:
    if overview.get("valid") is False:
        return AccountStatus.INVALID
    if str(overview.get("plan_state") or "").strip().lower() == "subscribed":
        return AccountStatus.SUBSCRIBED
    return AccountStatus.REGISTERED


def _account_with_extra(account: Account, extra: dict[str, Any]) -> Account:
    token = str(extra.get("access_token") or extra.get("accessToken") or account.token or "")
    return Account(
        platform=account.platform,
        email=account.email,
        password=account.password,
        user_id=str(extra.get("user_id") or extra.get("account_id") or account.user_id or ""),
        region=account.region,
        token=token,
        status=account.status,
        trial_end_time=account.trial_end_time,
        extra=extra,
        created_at=account.created_at,
    )


def _attach_auth_state(data: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    cookie_header = str(state.get("cookie_header") or state.get("cookies") or "").strip()
    if cookie_header:
        data["cookies"] = cookie_header
        data["cookie_header"] = cookie_header
    if state.get("freebeat_cookies"):
        data["freebeat_cookies"] = state.get("freebeat_cookies")
    return data


def _param_proxy_value(params: dict[str, Any] | None) -> str:
    data = dict(params or {})
    for key in ("freebeat_proxy_url", "proxy_url", "proxyUrl", "proxy"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def _account_proxy_value(account: Account) -> str:
    extra = dict(getattr(account, "extra", {}) or {})
    overview = extra.get("account_overview") if isinstance(extra.get("account_overview"), dict) else {}
    legacy_extra = overview.get("legacy_extra") if isinstance(overview.get("legacy_extra"), dict) else {}
    for source in (extra, legacy_extra, overview):
        for key in ("freebeat_proxy_url", "proxy_url", "proxyUrl", "resolved_proxy", "proxy"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


@register
class FreebeatPlatform(BasePlatform):
    name = "freebeat"
    display_name = "Freebeat"
    version = "1.0.0"
    supported_executors = ["protocol"]
    supported_identity_modes = ["mailbox"]

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox
        self._last_check_overview: dict[str, Any] = {}

    def _prepare_registration_password(self, password: str | None) -> str | None:
        return ""

    def _map_freebeat_result(self, result: dict[str, Any]) -> RegistrationResult:
        overview = dict(result.get("account_overview") or {})
        token = str(result.get("token") or result.get("access_token") or "").strip()
        device_token = str(result.get("device_token") or "").strip()
        return RegistrationResult(
            email=str(result.get("email") or "").strip(),
            password="",
            user_id=str(result.get("user_id") or "").strip(),
            token=token,
            status=_status_from_overview(overview),
            extra={
                "access_token": token,
                "accessToken": token,
                "device_token": device_token,
                "deviceToken": device_token,
                "user_id": str(result.get("user_id") or "").strip(),
                "email": str(result.get("email") or "").strip(),
                "expire_time": result.get("expire_time") or "",
                "new_user": bool(result.get("new_user")),
                "credits": result.get("credits", {}),
                "signin": result.get("signin", {}),
                "questionnaire": result.get("questionnaire", {}),
                "daily_sign_in": result.get("daily_sign_in", {}),
                "cookies": str(result.get("cookies") or result.get("cookie_header") or "").strip(),
                "cookie_header": str(result.get("cookie_header") or result.get("cookies") or "").strip(),
                "account_overview": overview,
            },
        )

    def _proxy_for_account(self, account: Account, params: dict[str, Any] | None = None) -> str | None:
        override = _param_proxy_value(params)
        if override:
            return override
        configured = str(getattr(self.config, "proxy", "") or "").strip()
        return configured or _account_proxy_value(account) or None

    def build_protocol_mailbox_adapter(self):
        def _build_worker(ctx, artifacts):
            from platforms.freebeat.protocol_mailbox import FreebeatProtocolMailboxWorker

            extra = dict(ctx.extra or {})
            return FreebeatProtocolMailboxWorker(
                proxy=ctx.proxy,
                log_fn=ctx.log,
                next_action=extra.get("freebeat_next_action"),
                next_router_state_tree=extra.get("freebeat_next_router_state_tree"),
                verify_source=str(extra.get("freebeat_verify_source") or FREEBEAT_DEFAULT_VERIFY_SOURCE),
            )

        def _run_worker(worker, ctx, artifacts):
            extra = dict(ctx.extra or {})
            daily_sign_enabled = _truthy(_runtime_value(extra, "freebeat_daily_sign_in_enabled", True), True)
            return worker.run(
                email=ctx.identity.email,
                otp_callback=artifacts.otp_callback,
                auto_questionnaire=_truthy(_runtime_value(extra, "freebeat_auto_questionnaire", True), True),
                auto_daily_sign_in=daily_sign_enabled and _truthy(_runtime_value(extra, "freebeat_auto_daily_sign_in", True), True),
                questionnaire_required=_truthy(_runtime_value(extra, "freebeat_questionnaire_required", False), False),
                daily_sign_in_required=_truthy(_runtime_value(extra, "freebeat_daily_sign_in_required", False), False),
            )

        return ProtocolMailboxAdapter(
            result_mapper=lambda ctx, result: self._map_freebeat_result(result),
            worker_builder=_build_worker,
            register_runner=_run_worker,
            otp_spec=OtpSpec(
                keyword="",
                code_pattern=r"\b(\d{6})\b",
                wait_message="等待 Freebeat 邮箱验证码...",
                success_label="Freebeat 邮箱验证码",
            ),
        )

    def _load_state(
        self,
        account: Account,
        *,
        force_refresh: bool = False,
        auto_sign_in: bool = False,
    ) -> dict[str, Any]:
        return load_freebeat_account_state(
            account,
            proxy=self._proxy_for_account(account),
            log_fn=self.log,
            force_refresh=force_refresh,
            auto_sign_in=auto_sign_in,
        )

    def check_valid(self, account: Account) -> bool:
        try:
            state = self._load_state(account)
        except Exception:
            self._last_check_overview = {"valid": False}
            return False
        summary = dict(state.get("summary") or {})
        self._last_check_overview = dict(summary.get("account_overview") or summary)
        return bool(summary.get("valid"))

    def get_platform_actions(self) -> list:
        return [
            {"id": "get_account_state", "label": "查询积分/账号状态", "params": []},
            {"id": "daily_sign_in", "label": "每日签到领积分", "params": []},
            {"id": "claim_questionnaire", "label": "回答问题领积分", "params": []},
            {
                "id": "keepalive_sync",
                "label": "保活并刷新积分",
                "params": [
                    {"key": "auto_daily_sign_in", "label": "顺便签到(true/false)", "type": "text"},
                ],
            },
            {
                "id": "sync_freebeat2api",
                "label": "同步到 Freebeat2API",
                "params": [
                    {"key": "balance", "label": "同步后刷新积分(true/false)", "type": "text"},
                    {"key": "heartbeat", "label": "同步后保活(true/false)", "type": "text"},
                    {"key": "sign_in", "label": "同步后签到(true/false)", "type": "text"},
                ],
            },
            {
                "id": "relogin_email_code",
                "label": "验证码重新自动登录并保活同步",
                "params": [
                    {"key": "code", "label": "邮箱验证码(留空自动读取)", "type": "text"},
                    {"key": "next_action", "label": "Next Action ID(可选)", "type": "text"},
                    {"key": "proxy", "label": "代理(可选，默认账号保存代理)", "type": "text"},
                ],
            },
            {
                "id": "stop_daily_sign_in",
                "label": "停止自动签到",
                "params": [
                    {"key": "reason", "label": "原因", "type": "text"},
                ],
            },
            {"id": "resume_daily_sign_in", "label": "恢复自动签到", "params": []},
            {
                "id": "get_model_rule_config",
                "label": "查询模型积分要求",
                "params": [
                    {"key": "model_id", "label": "模型ID", "type": "text"},
                    {"key": "business_type", "label": "业务类型", "type": "text"},
                ],
            },
        ]

    def _mailbox_for_account(self, account: Account, params: dict[str, Any] | None = None) -> tuple[BaseMailbox, MailboxAccount]:
        extra = dict(account.extra or {})
        resources = [
            item
            for item in list(extra.get("provider_resources") or [])
            if isinstance(item, dict)
            and str(item.get("provider_type") or "").strip() in {"", "mailbox"}
            and str(item.get("resource_type") or "").strip() in {"", "mailbox"}
        ]
        account_email = str(account.email or "").strip().lower()
        preferred = next(
            (
                item
                for item in resources
                if account_email
                and str(item.get("handle") or item.get("display_name") or item.get("metadata", {}).get("email") or "").strip().lower() == account_email
            ),
            resources[0] if resources else None,
        )
        if not preferred:
            raise RuntimeError("账号缺少注册邮箱 provider 记录，无法自动读取验证码；请在动作参数中手动填写 code")
        provider = str(preferred.get("provider_name") or preferred.get("provider") or "").strip()
        if not provider:
            raise RuntimeError("账号邮箱 provider 记录缺少 provider_name，无法自动读取验证码；请在动作参数中手动填写 code")
        metadata = dict(preferred.get("metadata") or {})
        email = str(preferred.get("handle") or metadata.get("email") or account.email or "").strip()
        account_id = str(preferred.get("resource_identifier") or metadata.get("account_id") or email).strip()
        if not email:
            raise RuntimeError("账号邮箱 provider 记录缺少邮箱地址，无法自动读取验证码；请在动作参数中手动填写 code")
        mailbox = create_mailbox(provider=provider, extra=extra, proxy=self._proxy_for_account(account, params))
        mailbox_account = MailboxAccount(
            email=email,
            account_id=account_id,
            extra={"provider_resource": preferred, "mailbox_provider_key": provider},
        )
        return mailbox, mailbox_account

    def _resolve_relogin_code(self, account: Account, params: dict, client: FreebeatClient, *, email: str) -> str:
        manual_code = str(params.get("code") or "").strip()
        if manual_code:
            return manual_code
        mailbox, mailbox_account = self._mailbox_for_account(account, params)
        try:
            timeout = int(str(params.get("otp_timeout") or "120").strip() or 120)
        except ValueError:
            timeout = 120
        before_ids = mailbox.get_current_ids(mailbox_account)
        self.log(f"Freebeat 重新登录: 发送邮箱验证码 {email}")
        client.send_email_verify_code(
            email,
            verify_source=str(params.get("verify_source") or FREEBEAT_DEFAULT_VERIFY_SOURCE),
        )
        self.log("等待 Freebeat 邮箱验证码...")
        code = mailbox.wait_for_code(
            mailbox_account,
            keyword=str(params.get("keyword") or "").strip(),
            timeout=timeout,
            before_ids=before_ids,
            code_pattern=r"\b(\d{6})\b",
        )
        if not code:
            raise RuntimeError("未读取到 Freebeat 邮箱验证码")
        self.log(f"Freebeat 邮箱验证码: {str(code)[:2]}****")
        return str(code).strip()

    def _relogin_with_email_code(self, account: Account, params: dict, *, message: str) -> dict:
        email = str(params.get("email") or account.email or "").strip()
        if not email:
            return {"ok": False, "error": "缺少 Freebeat 邮箱地址"}

        proxy = self._proxy_for_account(account, params)
        if proxy:
            self.log("Freebeat action using account proxy")
        client = FreebeatClient(proxy=proxy, log_fn=self.log)
        code = self._resolve_relogin_code(account, params, client, email=email)
        login = client.verify_email_code(
            email,
            code,
            next_action=str(params.get("next_action") or "").strip() or None,
            next_router_state_tree=str(params.get("next_router_state_tree") or "").strip() or None,
        )
        login_data = dict(login.get("data") or {})
        token = str(login_data.get("token") or login_data.get("accessToken") or login_data.get("deviceToken") or "").strip()
        state = client.fetch_account_state(token)
        state.update(
            {
                "email": email,
                "user_id": str(login_data.get("userId") or ""),
                "login": login,
                "expire_time": login_data.get("expireTime") or "",
                "access_token": str(login_data.get("accessToken") or token),
                "device_token": str(login_data.get("deviceToken") or ""),
            }
        )
        data = summarize_freebeat_account_state(state, fallback_email=email)
        auth_state = client.auth_state() if hasattr(client, "auth_state") else {}
        data.update(
            {
                "access_token": str(login_data.get("accessToken") or token),
                "accessToken": str(login_data.get("accessToken") or token),
                "device_token": str(login_data.get("deviceToken") or ""),
                "deviceToken": str(login_data.get("deviceToken") or ""),
                "cookies": auth_state.get("cookies", ""),
                "cookie_header": auth_state.get("cookie_header", ""),
                "user_id": str(login_data.get("userId") or ""),
                "account_id": str(login_data.get("userId") or ""),
                "expire_time": login_data.get("expireTime") or "",
                "session_refreshed": True,
                "message": message,
            }
        )
        sync_result = sync_account_to_freebeat2api(
            _account_with_extra(account, {**dict(account.extra or {}), **data}),
            log_fn=self.log,
            heartbeat=True,
            balance=True,
        )
        data["freebeat2api_synced"] = bool(sync_result)
        if sync_result:
            data["freebeat2api"] = sync_result
        return {"ok": True, "data": data}

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        params = dict(params or {})

        if action_id in {"get_user_info", "get_account_state", "query_state"}:
            state = self._load_state(account)
            return {"ok": True, "data": _attach_auth_state(dict(state.get("summary") or {}), state)}

        if action_id == "keepalive_sync":
            state = self._load_state(
                account,
                force_refresh=_truthy(params.get("force_refresh"), False),
                auto_sign_in=_truthy(params.get("auto_daily_sign_in"), False),
            )
            data = dict(state.get("summary") or {})
            _attach_auth_state(data, state)
            data["session_refreshed"] = False
            data["message"] = "Freebeat 已完成保活请求；当前抓包未发现静默刷新 token 接口"
            sync_result = sync_account_to_freebeat2api(
                _account_with_extra(account, {**dict(account.extra or {}), **state, **data}),
                log_fn=self.log,
                heartbeat=True,
                balance=True,
                sign_in=bool(data.get("last_daily_sign_in_status")),
            )
            data["freebeat2api_synced"] = bool(sync_result)
            if sync_result:
                data["freebeat2api"] = sync_result
            return {"ok": True, "data": data}

        if action_id == "daily_sign_in":
            context_state = self._load_state(account)
            token = str(context_state.get("token") or context_state.get("access_token") or account.token or "").strip()
            client = FreebeatClient(
                proxy=self._proxy_for_account(account),
                log_fn=self.log,
                cookie_header=str(context_state.get("cookie_header") or context_state.get("cookies") or ""),
            )
            daily = client.daily_sign_in(token)
            state = self._load_state(account)
            data = dict(state.get("summary") or {})
            _attach_auth_state(data, state)
            data.update(
                {
                    "daily_sign_in_status": daily.get("status", ""),
                    "last_daily_sign_in_status": daily.get("status", ""),
                    "daily_sign_in_at": _utcnow_iso(),
                    "reward_amount": daily.get("reward_amount", 0),
                    "daily_sign_in": daily,
                }
            )
            sync_result = sync_account_to_freebeat2api(
                _account_with_extra(account, {**dict(account.extra or {}), **state, **data}),
                log_fn=self.log,
                balance=True,
                sign_in=True,
            )
            data["freebeat2api_synced"] = bool(sync_result)
            if sync_result:
                data["freebeat2api"] = sync_result
            return {"ok": True, "data": data}

        if action_id == "claim_questionnaire":
            context_state = self._load_state(account)
            token = str(context_state.get("token") or context_state.get("access_token") or account.token or "").strip()
            client = FreebeatClient(
                proxy=self._proxy_for_account(account),
                log_fn=self.log,
                cookie_header=str(context_state.get("cookie_header") or context_state.get("cookies") or ""),
            )
            questionnaire = client.claim_questionnaire(token, questionnaire_code=FREEBEAT_ONBOARDING_CODE)
            state = self._load_state(account)
            data = dict(state.get("summary") or {})
            _attach_auth_state(data, state)
            data.update(
                {
                    "questionnaire_status": questionnaire.get("status", ""),
                    "last_questionnaire_status": questionnaire.get("status", ""),
                    "questionnaire_credits_granted": questionnaire.get("credits_granted", 0),
                    "questionnaire": questionnaire,
                }
            )
            sync_result = sync_account_to_freebeat2api(
                _account_with_extra(account, {**dict(account.extra or {}), **state, **data}),
                log_fn=self.log,
                heartbeat=True,
                balance=True,
            )
            data["freebeat2api_synced"] = bool(sync_result)
            if sync_result:
                data["freebeat2api"] = sync_result
            return {"ok": True, "data": data}

        if action_id == "sync_freebeat2api":
            state = self._load_state(account)
            data = dict(state.get("summary") or {})
            _attach_auth_state(data, state)
            sync_result = sync_account_to_freebeat2api(
                _account_with_extra(account, {**dict(account.extra or {}), **state, **data}),
                log_fn=self.log,
                balance=_truthy(params.get("balance"), True),
                heartbeat=_truthy(params.get("heartbeat"), False),
                sign_in=_truthy(params.get("sign_in"), False),
            )
            if not sync_result:
                return {"ok": False, "error": "Freebeat2API is not configured or sync failed"}
            return {
                "ok": True,
                "data": {
                    **data,
                    "message": "Freebeat account synced to Freebeat2API",
                    "freebeat2api_synced": True,
                    "freebeat2api": sync_result,
                },
            }

        if action_id == "stop_daily_sign_in":
            now = _utcnow_iso()
            reason = str(params.get("reason") or "manual").strip()
            return {
                "ok": True,
                "data": {
                    "valid": True,
                    "email": account.email,
                    "freebeat_daily_sign_in_disabled": True,
                    "freebeat_daily_sign_in_state": "disabled",
                    "freebeat_daily_sign_in_disabled_reason": reason,
                    "freebeat_daily_sign_in_disabled_at": now,
                    "freebeat_daily_sign_in_resumed_at": "",
                    "message": "Freebeat 自动签到已停止",
                },
            }

        if action_id == "resume_daily_sign_in":
            now = _utcnow_iso()
            return {
                "ok": True,
                "data": {
                    "valid": True,
                    "email": account.email,
                    "freebeat_daily_sign_in_disabled": False,
                    "freebeat_daily_sign_in_state": "enabled",
                    "freebeat_daily_sign_in_disabled_reason": "",
                    "freebeat_daily_sign_in_disabled_at": "",
                    "freebeat_daily_sign_in_resumed_at": now,
                    "message": "Freebeat 自动签到已恢复",
                },
            }

        if action_id == "send_login_code":
            email = str(params.get("email") or account.email or "").strip()
            if not email:
                return {"ok": False, "error": "缺少 Freebeat 邮箱地址"}
            client = FreebeatClient(proxy=self._proxy_for_account(account), log_fn=self.log)
            result = client.send_email_verify_code(
                email,
                verify_source=str(params.get("verify_source") or FREEBEAT_DEFAULT_VERIFY_SOURCE),
            )
            return {
                "ok": True,
                "data": {
                    "email": email,
                    "sent": True,
                    "send_code_result": result,
                    "message": "Freebeat 登录验证码已发送",
                },
            }

        if action_id == "refresh_session":
            return self._relogin_with_email_code(
                account,
                params,
                message="Freebeat 验证码登录续期成功，并已执行保活同步",
            )

        if action_id == "relogin_email_code":
            return self._relogin_with_email_code(
                account,
                params,
                message="Freebeat 邮箱验证码重新登录成功，并已执行保活同步",
            )

        if action_id == "get_model_rule_config":
            state = self._load_state(account)
            token = str(state.get("token") or state.get("access_token") or account.token or "").strip()
            model_id = int(str(params.get("model_id") or "101").strip())
            business_type = int(str(params.get("business_type") or "3").strip())
            client = FreebeatClient(
                proxy=self._proxy_for_account(account),
                log_fn=self.log,
                cookie_header=str(state.get("cookie_header") or state.get("cookies") or ""),
            )
            rule = client.get_model_rule_config(token, model_id=model_id, business_type=business_type)
            return {
                "ok": True,
                "data": {
                    "model_id": model_id,
                    "business_type": business_type,
                    "rule_config": rule.get("data"),
                    "raw": rule,
                },
            }

        raise NotImplementedError(f"未知 Freebeat 操作: {action_id}")
