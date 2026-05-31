"""QuickFrame platform plugin."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.base_mailbox import BaseMailbox, MailboxAccount, create_mailbox
from core.base_platform import Account, AccountStatus, BasePlatform, RegisterConfig
from core.quickframe2api_sync import sync_account_to_quickframe2api
from core.registration import OtpSpec, ProtocolMailboxAdapter, RegistrationResult
from core.registry import register
from platforms.quickframe.core import (
    QuickFrameClient,
    load_quickframe_account_state,
    summarize_quickframe_account_state,
)


QUICKFRAME_EMAIL_CODE_PATTERN = (
    r"(?is)(?:Enter\s+the\s+following\s+verification\s+code\s+when\s+prompted:"
    r"|verification\s+code(?:\s+is)?[:\s])"
    r"(?:(?:\s|&nbsp;|&#160;)|<!--.*?-->|<[^>]+>)*(\d{6})"
)


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    if state.get("quickframe_cookies"):
        data["quickframe_cookies"] = state.get("quickframe_cookies")
    token = str(state.get("access_token") or state.get("accessToken") or "").strip()
    if token:
        data["access_token"] = token
        data["accessToken"] = token
    return data


def _param_proxy_value(params: dict[str, Any] | None) -> str:
    data = dict(params or {})
    for key in ("quickframe_proxy_url", "proxy_url", "proxyUrl", "proxy"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def _account_proxy_value(account: Account) -> str:
    extra = dict(getattr(account, "extra", {}) or {})
    overview = extra.get("account_overview") if isinstance(extra.get("account_overview"), dict) else {}
    legacy_extra = overview.get("legacy_extra") if isinstance(overview.get("legacy_extra"), dict) else {}
    for source in (extra, legacy_extra, overview):
        for key in ("quickframe_proxy_url", "proxy_url", "proxyUrl", "resolved_proxy", "proxy"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _with_sync_proxy(account: Account, params: dict[str, Any] | None, data: dict[str, Any], proxy: str | None = None) -> dict[str, Any]:
    sync_data = dict(data or {})
    proxy_url = str(proxy or _param_proxy_value(params) or _account_proxy_value(account) or "").strip()
    if proxy_url:
        sync_data["proxy_url"] = proxy_url
    return sync_data


@register
class QuickFramePlatform(BasePlatform):
    name = "quickframe"
    display_name = "QuickFrame"
    version = "1.0.0"
    supported_executors = ["protocol"]
    supported_identity_modes = ["mailbox"]

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox
        self._last_check_overview: dict[str, Any] = {}

    def _prepare_registration_password(self, password: str | None) -> str | None:
        return ""

    def _map_quickframe_result(self, result: dict[str, Any]) -> RegistrationResult:
        overview = dict(result.get("account_overview") or {})
        token = str(result.get("token") or result.get("access_token") or "").strip()
        return RegistrationResult(
            email=str(result.get("email") or "").strip(),
            password="",
            user_id=str(result.get("user_id") or "").strip(),
            token=token,
            status=_status_from_overview(overview),
            extra={
                "access_token": token,
                "accessToken": token,
                "user_id": str(result.get("user_id") or "").strip(),
                "account_id": str(result.get("user_id") or "").strip(),
                "email": str(result.get("email") or "").strip(),
                "workspace_id": str(result.get("workspace_id") or result.get("workspaceId") or "").strip(),
                "workspaceId": str(result.get("workspace_id") or result.get("workspaceId") or "").strip(),
                "cookies": str(result.get("cookies") or result.get("cookie_header") or "").strip(),
                "cookie_header": str(result.get("cookie_header") or result.get("cookies") or "").strip(),
                "quickframe_cookies": result.get("quickframe_cookies") or [],
                "subscription_status": result.get("subscription_status") or {},
                "usage_limits": result.get("usage_limits") or {},
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
            from platforms.quickframe.protocol_mailbox import QuickFrameProtocolMailboxWorker

            return QuickFrameProtocolMailboxWorker(proxy=ctx.proxy, log_fn=ctx.log)

        def _run_worker(worker, ctx, artifacts):
            return worker.run(email=ctx.identity.email, otp_callback=artifacts.otp_callback)

        return ProtocolMailboxAdapter(
            result_mapper=lambda ctx, result: self._map_quickframe_result(result),
            worker_builder=_build_worker,
            register_runner=_run_worker,
            otp_spec=OtpSpec(
                keyword="",
                code_pattern=QUICKFRAME_EMAIL_CODE_PATTERN,
                wait_message="等待 QuickFrame 邮箱验证码...",
                success_label="QuickFrame 邮箱验证码",
            ),
        )

    def _load_state(self, account: Account, *, force_refresh: bool = False) -> dict[str, Any]:
        return load_quickframe_account_state(
            account,
            proxy=self._proxy_for_account(account),
            log_fn=self.log,
            force_refresh=force_refresh,
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
            {"id": "get_account_state", "label": "查询账号状态", "params": []},
            {
                "id": "keepalive_sync",
                "label": "心跳保活并同步",
                "params": [
                    {"key": "force_refresh", "label": "强制刷新 token(true/false)", "type": "text"},
                ],
            },
            {
                "id": "stop_keepalive",
                "label": "停止自动心跳保活",
                "params": [
                    {"key": "reason", "label": "原因", "type": "text"},
                ],
            },
            {"id": "resume_keepalive", "label": "恢复自动心跳保活", "params": []},
            {
                "id": "send_login_code",
                "label": "发送重新登录邮箱验证码",
                "params": [
                    {"key": "email", "label": "邮箱(默认账号邮箱)", "type": "text"},
                    {"key": "proxy", "label": "代理(可选)", "type": "text"},
                ],
            },
            {
                "id": "relogin_email_code",
                "label": "邮箱验证码重新登录并保活同步",
                "params": [
                    {"key": "code", "label": "邮箱验证码(留空自动读取)", "type": "text"},
                    {"key": "proxy", "label": "代理(可选，默认账号保存代理)", "type": "text"},
                ],
            },
            {
                "id": "sync_quickframe2api",
                "label": "同步到 QuickFrame2API",
                "params": [
                    {"key": "heartbeat", "label": "同步后心跳(true/false)", "type": "text"},
                    {"key": "check", "label": "同步后检查(true/false)", "type": "text"},
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

    def _client_for_relogin(self, account: Account, params: dict[str, Any], *, proxy: str | None) -> QuickFrameClient:
        extra = dict(account.extra or {})
        cookies = extra.get("quickframe_pending_cookies") or extra.get("quickframe_cookies") or extra.get("cookies") or extra.get("cookie_header")
        return QuickFrameClient(
            proxy=proxy,
            log_fn=self.log,
            cookie_header=str(extra.get("quickframe_pending_cookie_header") or extra.get("cookie_header") or ""),
            cookies=cookies,
            access_token=str(extra.get("access_token") or extra.get("accessToken") or account.token or ""),
            login_state=str(extra.get("quickframe_login_state") or ""),
            login_identifier_url=str(extra.get("quickframe_login_identifier_url") or ""),
            challenge_url=str(extra.get("quickframe_challenge_url") or ""),
        )

    def _resolve_relogin_code(self, account: Account, params: dict[str, Any], client: QuickFrameClient, *, email: str) -> str:
        manual_code = str(params.get("code") or "").strip()
        if manual_code:
            if not client.challenge_url:
                self.log("QuickFrame 未找到已保存 challenge，重新发送验证码后使用手动 code")
                client.begin_email_challenge(email)
            return manual_code
        mailbox, mailbox_account = self._mailbox_for_account(account, params)
        try:
            timeout = int(str(params.get("otp_timeout") or "120").strip() or 120)
        except ValueError:
            timeout = 120
        before_ids = mailbox.get_current_ids(mailbox_account)
        self.log(f"QuickFrame 重新登录: 发送邮箱验证码 {email}")
        client.begin_email_challenge(email)
        self.log("等待 QuickFrame 邮箱验证码...")
        code = mailbox.wait_for_code(
            mailbox_account,
            keyword=str(params.get("keyword") or "").strip(),
            timeout=timeout,
            before_ids=before_ids,
            code_pattern=QUICKFRAME_EMAIL_CODE_PATTERN,
        )
        if not code:
            raise RuntimeError("未读取到 QuickFrame 邮箱验证码")
        self.log(f"QuickFrame 邮箱验证码: {str(code)[:2]}****")
        return str(code).strip()

    def _relogin_with_email_code(self, account: Account, params: dict[str, Any], *, message: str) -> dict[str, Any]:
        email = str(params.get("email") or account.email or "").strip()
        if not email:
            return {"ok": False, "error": "缺少 QuickFrame 邮箱地址"}
        proxy = self._proxy_for_account(account, params)
        if proxy:
            self.log("QuickFrame action using account proxy")
        client = self._client_for_relogin(account, params, proxy=proxy)
        code = self._resolve_relogin_code(account, params, client, email=email)
        state = client.complete_email_challenge(code)
        data = dict((state.get("summary") or {}).get("account_overview") or state.get("summary") or {})
        _attach_auth_state(data, state)
        data.update(
            {
                "session_refreshed": True,
                "quickframe_login_state": "",
                "quickframe_login_identifier_url": "",
                "quickframe_challenge_url": "",
                "quickframe_pending_cookies": [],
                "quickframe_pending_cookie_header": "",
                "message": message,
            }
        )
        sync_result = sync_account_to_quickframe2api(
            _account_with_extra(account, {**dict(account.extra or {}), **_with_sync_proxy(account, params, data, proxy)}),
            log_fn=self.log,
            heartbeat=True,
            check=True,
        )
        data["quickframe2api_synced"] = bool(sync_result)
        if sync_result:
            data["quickframe2api"] = sync_result
        return {"ok": True, "data": data}

    def _handle_keepalive_preference(self, account: Account, params: dict | None = None, *, disabled: bool) -> dict:
        params = dict(params or {})
        now = _utcnow_iso()
        reason = str(params.get("reason") or "manual").strip()
        data: dict[str, Any] = {
            "valid": True,
            "email": account.email,
            "quickframe_keepalive_disabled": bool(disabled),
            "quickframe_keepalive_state": "disabled" if disabled else "enabled",
            "quickframe_keepalive_disabled_reason": reason if disabled else "",
            "quickframe_keepalive_disabled_at": now if disabled else "",
            "quickframe_keepalive_resumed_at": "" if disabled else now,
            "quickframe2api_enable_auto_maintenance": not disabled,
            "message": "QuickFrame 自动心跳保活已停止" if disabled else "QuickFrame 自动心跳保活已恢复",
        }
        sync_result = sync_account_to_quickframe2api(
            _account_with_extra(account, {**dict(account.extra or {}), **_with_sync_proxy(account, params, data)}),
            log_fn=self.log,
        )
        data["quickframe2api_synced"] = bool(sync_result)
        if sync_result:
            data["quickframe2api"] = sync_result
        return {"ok": True, "data": data}

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        params = dict(params or {})

        if action_id in {"get_user_info", "get_account_state", "query_state"}:
            state = self._load_state(account)
            return {"ok": True, "data": _attach_auth_state(dict(state.get("summary") or {}), state)}

        if action_id == "keepalive_sync":
            overview = dict((account.extra or {}).get("account_overview") or {})
            if _truthy(overview.get("quickframe_keepalive_disabled"), False) and not _truthy(params.get("force"), False):
                return {
                    "ok": True,
                    "data": {
                        "valid": True,
                        "email": account.email,
                        "quickframe_keepalive_disabled": True,
                        "quickframe_keepalive_state": "disabled",
                        "quickframe_keepalive_disabled_reason": str(overview.get("quickframe_keepalive_disabled_reason") or ""),
                        "quickframe2api_enable_auto_maintenance": False,
                        "message": "QuickFrame 自动心跳保活已停止",
                    },
                }
            state = self._load_state(account, force_refresh=_truthy(params.get("force_refresh"), True))
            data = dict(state.get("summary") or {})
            _attach_auth_state(data, state)
            data["session_refreshed"] = bool(data.get("access_token"))
            data["message"] = "QuickFrame 已完成 /session、/token 与 auth.checkSession 心跳保活"
            sync_result = sync_account_to_quickframe2api(
                _account_with_extra(account, {**dict(account.extra or {}), **_with_sync_proxy(account, params, {**state, **data})}),
                log_fn=self.log,
                heartbeat=True,
                check=True,
            )
            data["quickframe2api_synced"] = bool(sync_result)
            if sync_result:
                data["quickframe2api"] = sync_result
            return {"ok": True, "data": data}

        if action_id == "stop_keepalive":
            return self._handle_keepalive_preference(account, params, disabled=True)

        if action_id == "resume_keepalive":
            return self._handle_keepalive_preference(account, params, disabled=False)

        if action_id == "send_login_code":
            email = str(params.get("email") or account.email or "").strip()
            if not email:
                return {"ok": False, "error": "缺少 QuickFrame 邮箱地址"}
            client = QuickFrameClient(proxy=self._proxy_for_account(account, params), log_fn=self.log)
            pending = client.begin_email_challenge(email)
            return {
                "ok": True,
                "data": {
                    **pending,
                    "email": email,
                    "sent": True,
                    "message": "QuickFrame 登录验证码已发送",
                },
            }

        if action_id in {"refresh_session", "relogin_email_code"}:
            return self._relogin_with_email_code(
                account,
                params,
                message="QuickFrame 邮箱验证码重新登录成功，并已执行心跳保活同步",
            )

        if action_id == "sync_quickframe2api":
            state = self._load_state(account)
            data = dict(state.get("summary") or {})
            _attach_auth_state(data, state)
            sync_result = sync_account_to_quickframe2api(
                _account_with_extra(account, {**dict(account.extra or {}), **_with_sync_proxy(account, params, {**state, **data})}),
                log_fn=self.log,
                heartbeat=_truthy(params.get("heartbeat"), False),
                check=_truthy(params.get("check"), False),
            )
            if not sync_result:
                return {"ok": False, "error": "QuickFrame2API is not configured or sync failed"}
            return {
                "ok": True,
                "data": {
                    **data,
                    "message": "QuickFrame account synced to QuickFrame2API",
                    "quickframe2api_synced": True,
                    "quickframe2api": sync_result,
                },
            }

        raise NotImplementedError(f"未知 QuickFrame 操作: {action_id}")
