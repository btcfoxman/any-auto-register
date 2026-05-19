"""Freebeat protocol mailbox registration worker."""
from __future__ import annotations

from typing import Any, Callable

from platforms.freebeat.core import (
    FREEBEAT_DEFAULT_VERIFY_SOURCE,
    FreebeatClient,
    summarize_freebeat_account_state,
)


class FreebeatProtocolMailboxWorker:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        log_fn: Callable[[str], None] = print,
        next_action: str | None = None,
        next_router_state_tree: str | None = None,
        verify_source: str = FREEBEAT_DEFAULT_VERIFY_SOURCE,
    ):
        self.client = FreebeatClient(proxy=proxy, log_fn=log_fn)
        self.log = log_fn
        self.next_action = str(next_action or "").strip() or None
        self.next_router_state_tree = str(next_router_state_tree or "").strip() or None
        self.verify_source = str(verify_source or FREEBEAT_DEFAULT_VERIFY_SOURCE).strip() or FREEBEAT_DEFAULT_VERIFY_SOURCE

    def run(
        self,
        *,
        email: str,
        otp_callback: Callable[[], str] | None,
        auto_questionnaire: bool = True,
        auto_daily_sign_in: bool = True,
        questionnaire_required: bool = False,
        daily_sign_in_required: bool = False,
    ) -> dict[str, Any]:
        email = str(email or "").strip()
        if not email:
            raise RuntimeError("Freebeat 注册需要邮箱地址")
        if not otp_callback:
            raise RuntimeError("Freebeat 邮箱验证码回调未配置")

        self.log(f"Freebeat Step1: 发送邮箱验证码 {email}")
        self.client.send_email_verify_code(email, verify_source=self.verify_source)

        self.log("等待 Freebeat 邮箱验证码...")
        code = str(otp_callback() or "").strip()
        if not code:
            raise RuntimeError("获取 Freebeat 邮箱验证码超时")
        self.log(f"Freebeat Step2: 提交邮箱验证码 {code[:2]}****")

        login = self.client.verify_email_code(
            email,
            code,
            next_action=self.next_action,
            next_router_state_tree=self.next_router_state_tree,
        )
        login_data = dict(login.get("data") or {})
        token = str(login_data.get("token") or login_data.get("accessToken") or login_data.get("deviceToken") or "").strip()
        if not token:
            raise RuntimeError("Freebeat 登录成功响应中缺少 token")

        state = self.client.fetch_account_state(token)
        questionnaire: dict[str, Any] = {"status": "skipped"}
        if auto_questionnaire:
            try:
                questionnaire = self.client.claim_questionnaire(token)
                self.log(f"Freebeat 问卷奖励状态: {questionnaire.get('status')} +{questionnaire.get('credits_granted', 0)}")
            except Exception as exc:
                if questionnaire_required:
                    raise
                questionnaire = {"status": "error", "error": str(exc)}
                self.log(f"Freebeat 问卷奖励失败，忽略并继续: {exc}")

        daily_sign_in: dict[str, Any] = {"status": "skipped"}
        if auto_daily_sign_in:
            try:
                daily_sign_in = self.client.daily_sign_in(token)
                self.log(f"Freebeat 每日签到状态: {daily_sign_in.get('status')} +{daily_sign_in.get('reward_amount', 0)}")
            except Exception as exc:
                if daily_sign_in_required:
                    raise
                daily_sign_in = {"status": "error", "error": str(exc)}
                self.log(f"Freebeat 每日签到失败，忽略并继续: {exc}")

        state = self.client.fetch_account_state(token)
        state.update(
            {
                "email": email,
                "user_id": str(login_data.get("userId") or ""),
                "login": login,
                "expire_time": login_data.get("expireTime") or "",
                "access_token": str(login_data.get("accessToken") or token),
                "device_token": str(login_data.get("deviceToken") or ""),
                "questionnaire": questionnaire,
                "daily_sign_in": daily_sign_in,
                "last_questionnaire_status": str(questionnaire.get("status") or ""),
                "last_daily_sign_in_status": str(daily_sign_in.get("status") or ""),
            }
        )
        summary = summarize_freebeat_account_state(state, fallback_email=email)
        overview = dict(summary.get("account_overview") or {})
        result = {
            "success": True,
            "email": email,
            "password": "",
            "user_id": str(login_data.get("userId") or ""),
            "token": token,
            "access_token": str(login_data.get("accessToken") or token),
            "device_token": str(login_data.get("deviceToken") or ""),
            "expire_time": login_data.get("expireTime") or "",
            "new_user": bool(login_data.get("newUser")),
            "login": login,
            "credits": summary.get("credits", {}),
            "signin": summary.get("signin", {}),
            "questionnaire": questionnaire,
            "daily_sign_in": daily_sign_in,
            "cookies": str(state.get("cookies") or state.get("cookie_header") or "").strip(),
            "cookie_header": str(state.get("cookie_header") or state.get("cookies") or "").strip(),
            "account_overview": overview,
        }
        self.log(
            f"Freebeat 注册/登录成功: {email} "
            f"user={result['user_id'] or '-'} credits={overview.get('total_credits', '-')}"
        )
        return result
