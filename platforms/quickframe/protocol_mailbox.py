"""QuickFrame protocol mailbox registration worker."""
from __future__ import annotations

from typing import Any, Callable

from platforms.quickframe.core import QuickFrameClient, summarize_quickframe_account_state


class QuickFrameProtocolMailboxWorker:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        log_fn: Callable[[str], None] = print,
    ):
        self.client = QuickFrameClient(proxy=proxy, log_fn=log_fn)
        self.log = log_fn

    def run(self, *, email: str, otp_callback: Callable[[], str] | None) -> dict[str, Any]:
        email = str(email or "").strip()
        if not email:
            raise RuntimeError("QuickFrame registration requires email")
        if not otp_callback:
            raise RuntimeError("QuickFrame email OTP callback is not configured")

        self.log(f"QuickFrame Step1: 发送邮箱验证码 {email}")
        self.client.begin_email_challenge(email)
        self.log("等待 QuickFrame 邮箱验证码...")
        code = str(otp_callback() or "").strip()
        if not code:
            raise RuntimeError("获取 QuickFrame 邮箱验证码超时")
        self.log(f"QuickFrame Step2: 提交邮箱验证码 {code[:2]}****")
        state = self.client.complete_email_challenge(code)
        summary = summarize_quickframe_account_state(state, fallback_email=email)
        overview = dict(summary.get("account_overview") or {})
        token = str(state.get("access_token") or state.get("accessToken") or "")
        result = {
            "success": True,
            "email": summary.get("email") or email,
            "password": "",
            "user_id": str(summary.get("user_id") or ""),
            "token": token,
            "access_token": token,
            "accessToken": token,
            "cookies": str(state.get("cookies") or state.get("cookie_header") or "").strip(),
            "cookie_header": str(state.get("cookie_header") or state.get("cookies") or "").strip(),
            "quickframe_cookies": state.get("quickframe_cookies") or [],
            "workspace_id": str(summary.get("workspace_id") or ""),
            "workspaceId": str(summary.get("workspace_id") or ""),
            "account_overview": overview,
            "session": state.get("session_info") or {},
            "subscription_status": state.get("subscription_status") or {},
            "usage_limits": state.get("usage_limits") or {},
        }
        self.log(
            f"QuickFrame 注册/登录成功: {result['email']} "
            f"user={result['user_id'] or '-'} exports={overview.get('free_exports_remaining', '-')}"
        )
        return result
