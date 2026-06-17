"""ImgsWeryai platform plugin."""
from __future__ import annotations

import string
from datetime import datetime, timezone
from typing import Any

from core.base_mailbox import BaseMailbox, MailboxAccount, create_mailbox
from core.base_platform import Account, AccountStatus, BasePlatform, RegisterConfig
from core.imgs2api_sync import sync_account_to_imgs2api
from core.registration import OtpSpec, ProtocolMailboxAdapter, RegistrationResult
from core.registry import register
from platforms.imgs_weryai.core import (
    WeryAIClient,
    extract_weryai_account_context,
    load_weryai_account_state,
    summarize_weryai_account_state,
    text,
)
from platforms.imgs_weryai.protocol_mailbox import WERYAI_EMAIL_CODE_PATTERN


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


def _param_proxy_value(params: dict[str, Any] | None) -> str:
    data = dict(params or {})
    for key in ("imgs_weryai_proxy_url", "weryai_proxy_url", "proxy_url", "proxyUrl", "proxy"):
        value = text(data.get(key))
        if value:
            return value
    return ""


def _account_proxy_value(account: Account) -> str:
    context = extract_weryai_account_context(account)
    return text(context.get("proxy_url"))


def _account_with_extra(account: Account, extra: dict[str, Any]) -> Account:
    token = text(extra.get("access_token") or extra.get("authorization") or extra.get("accessToken") or account.token)
    return Account(
        platform=account.platform,
        email=account.email,
        password=account.password,
        user_id=text(extra.get("user_id") or extra.get("uid") or account.user_id),
        region=account.region,
        token=token,
        status=account.status,
        trial_end_time=account.trial_end_time,
        extra=extra,
        created_at=account.created_at,
    )


def _attach_auth_state(data: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    token = text(state.get("access_token") or state.get("authorization") or state.get("accessToken"))
    if token:
        data["access_token"] = token
        data["authorization"] = token
        data["accessToken"] = token
    for key in (
        "team_id",
        "teamId",
        "product_id",
        "productId",
        "df_id",
        "client_ip",
        "user_agent",
        "sec_ch_ua",
        "sec_ch_ua_platform",
        "credits_balance",
        "remaining_credits",
        "balance",
        "last_keepalive_at",
    ):
        if state.get(key) not in (None, ""):
            data[key] = state.get(key)
    cookie_header = text(state.get("weryai_cookie_header") or state.get("cookie_header") or state.get("cookies"))
    if cookie_header:
        data["cookies"] = cookie_header
        data["cookie_header"] = cookie_header
        data["weryai_cookie_header"] = cookie_header
    if state.get("weryai_cookies"):
        data["weryai_cookies"] = state.get("weryai_cookies")
    return data


def _with_sync_proxy(account: Account, params: dict[str, Any] | None, data: dict[str, Any], proxy: str | None = None) -> dict[str, Any]:
    sync_data = dict(data or {})
    proxy_url = text(proxy or _param_proxy_value(params) or _account_proxy_value(account))
    if proxy_url:
        sync_data["proxy_url"] = proxy_url
    return sync_data


@register
class ImgsWeryaiPlatform(BasePlatform):
    name = "imgs_weryai"
    display_name = "ImgsWeryai"
    version = "1.0.0"
    supported_executors = ["protocol"]
    supported_identity_modes = ["mailbox"]

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox
        self._last_check_overview: dict[str, Any] = {}

    def _prepare_registration_password(self, password: str | None) -> str | None:
        if text(password):
            return text(password)
        return self._make_random_password(14, string.ascii_letters + string.digits + "!@")

    def _map_weryai_result(self, result: dict[str, Any]) -> RegistrationResult:
        overview = dict(result.get("account_overview") or {})
        token = text(result.get("token") or result.get("access_token") or result.get("authorization"))
        user_id = text(result.get("user_id") or overview.get("user_id") or overview.get("uid"))
        email = text(result.get("email") or overview.get("email"))
        password = text(result.get("password"))
        cookie_header = text(result.get("weryai_cookie_header") or result.get("cookie_header") or result.get("cookies"))
        return RegistrationResult(
            email=email,
            password=password,
            user_id=user_id,
            token=token,
            status=_status_from_overview(overview),
            extra={
                "access_token": token,
                "accessToken": token,
                "authorization": token,
                "user_id": user_id,
                "uid": user_id,
                "email": email,
                "team_id": text(result.get("team_id") or overview.get("team_id")),
                "teamId": text(result.get("team_id") or overview.get("team_id")),
                "product_id": text(result.get("product_id") or overview.get("product_id")),
                "productId": text(result.get("product_id") or overview.get("product_id")),
                "df_id": text(result.get("df_id") or overview.get("df_id")),
                "client_ip": text(result.get("client_ip") or overview.get("client_ip")),
                "credits": result.get("credits", {}),
                "credits_balance": result.get("credits_balance"),
                "remaining_credits": result.get("remaining_credits"),
                "cookies": cookie_header,
                "cookie_header": cookie_header,
                "weryai_cookies": result.get("weryai_cookies") or [],
                "weryai_cookie_header": cookie_header,
                "login": result.get("login") or {},
                "user_info": result.get("user_info") or {},
                "account_summary": result.get("account_summary") or {},
                "current_context": result.get("current_context") or {},
                "account_overview": overview,
            },
        )

    def _proxy_for_account(self, account: Account, params: dict[str, Any] | None = None) -> str | None:
        override = _param_proxy_value(params)
        if override:
            return override
        configured = text(getattr(self.config, "proxy", ""))
        return configured or _account_proxy_value(account) or None

    def build_protocol_mailbox_adapter(self):
        def _build_worker(ctx, artifacts):
            from platforms.imgs_weryai.protocol_mailbox import WeryAIProtocolMailboxWorker

            extra = dict(ctx.extra or {})
            return WeryAIProtocolMailboxWorker(
                proxy=ctx.proxy,
                log_fn=ctx.log,
                df_id=text(extra.get("df_id") or extra.get("weryai_df_id")),
                client_ip=text(extra.get("client_ip") or extra.get("weryai_client_ip")),
            )

        def _run_worker(worker, ctx, artifacts):
            return worker.run(
                email=ctx.identity.email,
                password=text(ctx.password),
                otp_callback=artifacts.otp_callback,
            )

        return ProtocolMailboxAdapter(
            result_mapper=lambda ctx, result: self._map_weryai_result(result),
            worker_builder=_build_worker,
            register_runner=_run_worker,
            otp_spec=OtpSpec(
                keyword="WeryAI",
                code_pattern=WERYAI_EMAIL_CODE_PATTERN,
                wait_message="Waiting for WeryAI email verification code...",
                success_label="WeryAI email verification code",
            ),
        )

    def _load_state(self, account: Account, *, force_refresh: bool = False) -> dict[str, Any]:
        return load_weryai_account_state(
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
            {"id": "get_account_state", "label": "Query account state", "params": []},
            {"id": "query_state", "label": "Query account state", "params": []},
            {
                "id": "keepalive_sync",
                "label": "Refresh balance and sync",
                "params": [
                    {"key": "force_refresh", "label": "force refresh(true/false)", "type": "text"},
                ],
            },
            {
                "id": "stop_keepalive",
                "label": "Stop auto keepalive",
                "params": [{"key": "reason", "label": "reason", "type": "text"}],
            },
            {"id": "resume_keepalive", "label": "Resume auto keepalive", "params": []},
            {
                "id": "send_login_code",
                "label": "Send email verification code",
                "params": [
                    {"key": "email", "label": "email(default account email)", "type": "text"},
                    {"key": "proxy", "label": "proxy(optional)", "type": "text"},
                ],
            },
            {
                "id": "relogin_email_code",
                "label": "Relogin by email code and sync",
                "params": [
                    {"key": "code", "label": "email code(empty to read mailbox)", "type": "text"},
                    {"key": "proxy", "label": "proxy(optional, defaults to account proxy)", "type": "text"},
                ],
            },
            {
                "id": "sync_imgs2api",
                "label": "Sync to Imgs2API",
                "params": [
                    {"key": "heartbeat", "label": "heartbeat after sync(true/false)", "type": "text"},
                    {"key": "balance", "label": "balance after sync(true/false)", "type": "text"},
                    {"key": "check", "label": "check after sync(true/false)", "type": "text"},
                ],
            },
        ]

    def _mailbox_for_account(self, account: Account, params: dict[str, Any] | None = None) -> tuple[BaseMailbox, MailboxAccount]:
        extra = dict(account.extra or {})
        resources = [
            item
            for item in list(extra.get("provider_resources") or [])
            if isinstance(item, dict)
            and text(item.get("provider_type")) in {"", "mailbox"}
            and text(item.get("resource_type")) in {"", "mailbox"}
        ]
        account_email = text(account.email).lower()
        preferred = next(
            (
                item
                for item in resources
                if account_email
                and text(item.get("handle") or item.get("display_name") or item.get("metadata", {}).get("email")).lower() == account_email
            ),
            resources[0] if resources else None,
        )
        if not preferred:
            raise RuntimeError("Account is missing mailbox provider resource; pass code manually in action params")
        provider = text(preferred.get("provider_name") or preferred.get("provider"))
        if not provider:
            raise RuntimeError("Mailbox provider resource is missing provider_name; pass code manually in action params")
        metadata = dict(preferred.get("metadata") or {})
        email = text(preferred.get("handle") or metadata.get("email") or account.email)
        account_id = text(preferred.get("resource_identifier") or metadata.get("account_id") or email)
        if not email:
            raise RuntimeError("Mailbox provider resource is missing email; pass code manually in action params")
        mailbox = create_mailbox(provider=provider, extra=extra, proxy=self._proxy_for_account(account, params))
        mailbox_account = MailboxAccount(
            email=email,
            account_id=account_id,
            extra={"provider_resource": preferred, "mailbox_provider_key": provider},
        )
        return mailbox, mailbox_account

    def _client_for_account(self, account: Account, params: dict[str, Any], *, proxy: str | None) -> WeryAIClient:
        context = extract_weryai_account_context(account)
        return WeryAIClient(
            proxy=proxy,
            log_fn=self.log,
            access_token=context.get("access_token", ""),
            cookies=context.get("cookies"),
            cookie_header=context.get("cookie_header", ""),
            df_id=context.get("df_id", ""),
            client_ip=context.get("client_ip", ""),
            impersonate=context.get("impersonate", ""),
            user_agent=context.get("user_agent", ""),
            sec_ch_ua=context.get("sec_ch_ua", ""),
        )

    def _resolve_relogin_code(self, account: Account, params: dict[str, Any], client: WeryAIClient, *, email: str) -> str:
        manual_code = text(params.get("code"))
        if manual_code:
            return manual_code
        mailbox, mailbox_account = self._mailbox_for_account(account, params)
        try:
            timeout = int(text(params.get("otp_timeout") or "120") or 120)
        except ValueError:
            timeout = 120
        before_ids = mailbox.get_current_ids(mailbox_account)
        self.log(f"WeryAI relogin: send email verification code {email}")
        client.send_email_ticket(email)
        self.log("Waiting for WeryAI email verification code...")
        code = mailbox.wait_for_code(
            mailbox_account,
            keyword=text(params.get("keyword") or "WeryAI"),
            timeout=timeout,
            before_ids=before_ids,
            code_pattern=WERYAI_EMAIL_CODE_PATTERN,
        )
        if not code:
            raise RuntimeError("WeryAI email verification code was not found")
        self.log(f"WeryAI email verification code: {str(code)[:2]}****")
        return text(code)

    def _relogin_with_email_code(self, account: Account, params: dict[str, Any], *, message: str) -> dict[str, Any]:
        email = text(params.get("email") or account.email)
        if not email:
            return {"ok": False, "error": "Missing WeryAI email address"}
        password = text(params.get("password") or account.password)
        if not password:
            return {"ok": False, "error": "Missing WeryAI password; cannot relogin by email code"}
        proxy = self._proxy_for_account(account, params)
        if proxy:
            self.log("WeryAI relogin using account proxy")
        client = self._client_for_account(account, params, proxy=proxy)
        code = self._resolve_relogin_code(account, params, client, email=email)
        login = client.login_with_email_code(email, password, code)
        login_data = login.get("data") if isinstance(login.get("data"), dict) else {}
        token = text(login_data.get("access_token"))
        state = client.fetch_account_state(access_token=token)
        data = dict((state.get("summary") or {}).get("account_overview") or state.get("summary") or {})
        _attach_auth_state(data, state)
        data.update({"session_refreshed": True, "message": message})
        sync_result = sync_account_to_imgs2api(
            _account_with_extra(account, {**dict(account.extra or {}), **_with_sync_proxy(account, params, data, proxy)}),
            log_fn=self.log,
            heartbeat=True,
            balance=True,
            check=True,
        )
        data["imgs2api_synced"] = bool(sync_result)
        if sync_result:
            data["imgs2api"] = sync_result
        return {"ok": True, "data": data}

    def _handle_keepalive_preference(self, account: Account, params: dict | None = None, *, disabled: bool) -> dict:
        params = dict(params or {})
        now = _utcnow_iso()
        reason = text(params.get("reason") or "manual")
        data: dict[str, Any] = {
            "valid": True,
            "email": account.email,
            "imgs_weryai_keepalive_disabled": bool(disabled),
            "imgs_weryai_keepalive_state": "disabled" if disabled else "enabled",
            "imgs_weryai_keepalive_disabled_reason": reason if disabled else "",
            "imgs_weryai_keepalive_disabled_at": now if disabled else "",
            "imgs_weryai_keepalive_resumed_at": "" if disabled else now,
            "imgs2api_enable_auto_maintenance": not disabled,
            "message": "ImgsWeryai auto keepalive stopped" if disabled else "ImgsWeryai auto keepalive resumed",
        }
        sync_result = sync_account_to_imgs2api(
            _account_with_extra(account, {**dict(account.extra or {}), **_with_sync_proxy(account, params, data)}),
            log_fn=self.log,
        )
        data["imgs2api_synced"] = bool(sync_result)
        if sync_result:
            data["imgs2api"] = sync_result
        return {"ok": True, "data": data}

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        params = dict(params or {})

        if action_id in {"get_user_info", "get_account_state", "query_state"}:
            state = self._load_state(account, force_refresh=_truthy(params.get("force_refresh"), False))
            return {"ok": True, "data": _attach_auth_state(dict(state.get("summary") or {}), state)}

        if action_id == "keepalive_sync":
            overview = dict((account.extra or {}).get("account_overview") or {})
            if _truthy(overview.get("imgs_weryai_keepalive_disabled"), False) and not _truthy(params.get("force"), False):
                return {
                    "ok": True,
                    "data": {
                        "valid": True,
                        "email": account.email,
                        "imgs_weryai_keepalive_disabled": True,
                        "imgs_weryai_keepalive_state": "disabled",
                        "imgs_weryai_keepalive_disabled_reason": text(overview.get("imgs_weryai_keepalive_disabled_reason")),
                        "imgs2api_enable_auto_maintenance": False,
                        "message": "ImgsWeryai auto keepalive stopped",
                    },
                }
            state = self._load_state(account, force_refresh=_truthy(params.get("force_refresh"), False))
            data = dict(state.get("summary") or {})
            _attach_auth_state(data, state)
            data["session_refreshed"] = bool(data.get("access_token"))
            data["message"] = "ImgsWeryai account state refreshed via WeryAI auth account APIs"
            sync_result = sync_account_to_imgs2api(
                _account_with_extra(account, {**dict(account.extra or {}), **_with_sync_proxy(account, params, {**state, **data})}),
                log_fn=self.log,
                heartbeat=True,
                balance=True,
                check=True,
            )
            data["imgs2api_synced"] = bool(sync_result)
            if sync_result:
                data["imgs2api"] = sync_result
            return {"ok": True, "data": data}

        if action_id == "stop_keepalive":
            return self._handle_keepalive_preference(account, params, disabled=True)

        if action_id == "resume_keepalive":
            return self._handle_keepalive_preference(account, params, disabled=False)

        if action_id == "send_login_code":
            email = text(params.get("email") or account.email)
            if not email:
                return {"ok": False, "error": "Missing WeryAI email address"}
            client = WeryAIClient(proxy=self._proxy_for_account(account, params), log_fn=self.log)
            client.send_email_ticket(email)
            return {"ok": True, "data": {"email": email, "sent": True, "message": "WeryAI email verification code sent"}}

        if action_id in {"refresh_session", "relogin_email_code"}:
            return self._relogin_with_email_code(
                account,
                params,
                message="WeryAI email code relogin succeeded and account state synced",
            )

        if action_id == "sync_imgs2api":
            state = self._load_state(account)
            data = dict(state.get("summary") or {})
            _attach_auth_state(data, state)
            sync_result = sync_account_to_imgs2api(
                _account_with_extra(account, {**dict(account.extra or {}), **_with_sync_proxy(account, params, {**state, **data})}),
                log_fn=self.log,
                heartbeat=_truthy(params.get("heartbeat"), False),
                balance=_truthy(params.get("balance"), False),
                check=_truthy(params.get("check"), False),
            )
            if not sync_result:
                return {"ok": False, "error": "Imgs2API is not configured or sync failed"}
            return {
                "ok": True,
                "data": {
                    **data,
                    "message": "ImgsWeryai account synced to Imgs2API",
                    "imgs2api_synced": True,
                    "imgs2api": sync_result,
                },
            }

        raise NotImplementedError(f"Unknown ImgsWeryai action: {action_id}")
