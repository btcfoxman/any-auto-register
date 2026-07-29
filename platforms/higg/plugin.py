"""Higgsfield platform plugin."""
from __future__ import annotations

from typing import Any

from core.base_platform import Account, AccountStatus, BasePlatform, RegisterConfig
from core.registration import OtpSpec, ProtocolMailboxAdapter, RegistrationResult
from core.registry import register
from core.higg2api_sync import sync_account_to_higg2api
from platforms.higg.core import HiggClient, HiggRiskBlocked, extract_higg_account_context


def _truthy(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _account_with_extra(account: Account, extra: dict[str, Any]) -> Account:
    return Account(
        platform=account.platform,
        email=account.email,
        password=account.password,
        user_id=str(extra.get("user_id") or account.user_id or ""),
        region=account.region,
        token=str(extra.get("clerk_jwt") or extra.get("token") or account.token or ""),
        status=account.status,
        trial_end_time=account.trial_end_time,
        extra=extra,
        created_at=account.created_at,
    )


@register
class HiggPlatform(BasePlatform):
    name = "higg"
    display_name = "Higgsfield"
    version = "1.0.0"
    supported_executors = ["protocol"]
    supported_identity_modes = ["mailbox"]
    protocol_captcha_order = ("local_solver", "twocaptcha_api", "yescaptcha_api")
    capabilities = ["query_state"]

    def __init__(self, config: RegisterConfig = None, mailbox=None):
        super().__init__(config)
        self.mailbox = mailbox
        self._last_check_overview: dict[str, Any] = {}

    def _map_result(self, result: dict[str, Any]) -> RegistrationResult:
        overview = dict(result.get("account_overview") or {})
        token = str(result.get("clerk_jwt") or result.get("token") or "").strip()
        return RegistrationResult(
            email=str(result.get("email") or "").strip(),
            password=str(result.get("password") or ""),
            user_id=str(result.get("user_id") or "").strip(),
            token=token,
            status=AccountStatus.REGISTERED,
            extra={
                **result,
                "token": token,
                "clerk_jwt": token,
                "account_overview": overview,
            },
        )

    def build_protocol_mailbox_adapter(self):
        def build_worker(ctx, _artifacts):
            from platforms.higg.protocol_mailbox import HiggProtocolMailboxWorker

            extra = dict(ctx.extra or {})
            return HiggProtocolMailboxWorker(
                proxy=ctx.proxy,
                log_fn=ctx.log,
                turnstile_solver=ctx.platform.solve_turnstile_with_fallback,
                turnstile_token=str(extra.get("higg_turnstile_token") or extra.get("turnstile_token") or ""),
                user_agent=str(extra.get("user_agent") or ""),
                sec_ch_ua=str(extra.get("sec_ch_ua") or ""),
                sec_ch_ua_platform=str(extra.get("sec_ch_ua_platform") or ""),
            )

        return ProtocolMailboxAdapter(
            result_mapper=lambda _ctx, result: self._map_result(result),
            worker_builder=build_worker,
            register_runner=lambda worker, ctx, artifacts: worker.run(
                email=ctx.identity.email,
                password=ctx.password or "",
                otp_callback=artifacts.otp_callback,
            ),
            otp_spec=OtpSpec(
                keyword="",
                code_pattern=r"\b(\d{6})\b",
                wait_message="等待 Higgsfield 邮箱验证码...",
                success_label="Higgsfield 邮箱验证码",
            ),
        )

    def _client(self, account: Account) -> HiggClient:
        context = extract_higg_account_context(account)
        proxy = str((self.config.proxy if self.config else "") or context.get("proxy_url") or "").strip()
        return HiggClient.from_account(account, proxy=proxy or None, log_fn=self.log)

    def _load_state(self, account: Account) -> dict[str, Any]:
        client = self._client(account)
        state = client.fetch_account_state()
        state["proxy_url"] = extract_higg_account_context(account).get("proxy_url", "")
        return state

    def check_valid(self, account: Account) -> bool:
        try:
            state = self._load_state(account)
            self._last_check_overview = {
                key: value
                for key, value in state.items()
                if key not in {"token", "clerk_jwt", "cookies", "cookie_header", "wallet", "free_gens"}
            }
            return bool(state.get("valid"))
        except HiggRiskBlocked as exc:
            context = extract_higg_account_context(account)
            try:
                client = self._client(account)
                client.refresh_token()
                self._last_check_overview = {
                    "valid": True,
                    "generation_ready": False,
                    "email": context.get("email"),
                    "check_warning": str(exc),
                    "chips": ["Clerk active", "DataDome required"],
                }
                return True
            except Exception:
                self._last_check_overview = {"valid": False, "check_error": str(exc)}
                return False
        except Exception as exc:
            self._last_check_overview = {"valid": False, "check_error": str(exc)}
            return False

    def get_last_check_overview(self) -> dict[str, Any]:
        return dict(self._last_check_overview or {})

    def get_quota(self, account: Account) -> dict:
        state = self._load_state(account)
        return {
            "valid": state.get("valid"),
            "generation_ready": state.get("generation_ready"),
            "credits_balance": state.get("credits_balance"),
            "total_credits": state.get("total_credits"),
            "free_generations": state.get("free_generations"),
            "workspace_id": state.get("workspace_id"),
            "chips": state.get("chips", []),
        }

    def get_platform_actions(self) -> list:
        return [
            {"id": "query_state", "label": "查询 Higgsfield 额度/免费次数", "sync": False, "params": []},
            {"id": "refresh_session", "label": "刷新 Clerk 会话", "sync": False, "params": []},
            {
                "id": "keepalive_sync",
                "label": "刷新状态并同步 Higg2API",
                "sync": False,
                "params": [{"key": "sync_higg2api", "label": "同步下游(true/false)", "type": "text"}],
            },
            {"id": "sync_higg2api", "label": "同步到 Higg2API", "sync": False, "params": []},
            {
                "id": "stop_keepalive",
                "label": "停止 Higg2API 自动维护",
                "sync": True,
                "params": [{"key": "reason", "label": "停止原因", "type": "text"}],
            },
            {"id": "resume_keepalive", "label": "恢复 Higg2API 自动维护", "sync": True, "params": []},
        ]

    def _sync(self, account: Account, extra: dict[str, Any], *, check: bool = False) -> dict[str, Any] | bool:
        merged = {**dict(account.extra or {}), **extra}
        return sync_account_to_higg2api(
            _account_with_extra(account, merged),
            log_fn=self.log,
            check=check,
        )

    def _refresh_and_sync(self, account: Account, *, sync_enabled: bool = True) -> dict[str, Any]:
        state = self._load_state(account)
        data = {**state, "message": "Higgsfield Clerk session and account state refreshed"}
        if sync_enabled:
            result = self._sync(account, data)
            data["higg2api_synced"] = bool(result)
            if result:
                data["higg2api"] = result
        return {"ok": True, "data": data}

    def _maintenance_preference(self, account: Account, *, disabled: bool, reason: str = "") -> dict[str, Any]:
        data = {
            "higg2api_enable_auto_maintenance": not disabled,
            "higg_keepalive_disabled": disabled,
            "higg_keepalive_disabled_reason": reason if disabled else "",
            "message": "Higg2API 自动维护已停止" if disabled else "Higg2API 自动维护已恢复",
        }
        result = self._sync(account, data)
        data["higg2api_synced"] = bool(result)
        if result:
            data["higg2api"] = result
        return {"ok": True, "data": data}

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        params = dict(params or {})
        if action_id in {"query_state", "get_account_state", "get_user_info", "refresh_session"}:
            return self._refresh_and_sync(account, sync_enabled=False)
        if action_id == "keepalive_sync":
            return self._refresh_and_sync(
                account,
                sync_enabled=_truthy(params.get("sync_higg2api"), True),
            )
        if action_id == "sync_higg2api":
            context = extract_higg_account_context(account)
            result = self._sync(account, context)
            if not result:
                return {"ok": False, "error": "Higg2API is not configured or sync failed"}
            return {
                "ok": True,
                "data": {
                    "message": "Higgsfield account synced to Higg2API",
                    "higg2api_synced": True,
                    "higg2api": result,
                },
            }
        if action_id == "stop_keepalive":
            return self._maintenance_preference(
                account,
                disabled=True,
                reason=str(params.get("reason") or "manual"),
            )
        if action_id == "resume_keepalive":
            return self._maintenance_preference(account, disabled=False)
        return super().execute_action(action_id, account, params)

