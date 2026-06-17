"""ImgsWeryai protocol mailbox registration worker."""
from __future__ import annotations

from typing import Any, Callable

from platforms.imgs_weryai.core import (
    WeryAIClient,
    fetch_weryai_account_state_with_polling,
    partial_weryai_account_state,
    summarize_weryai_account_state,
    text,
)


WERYAI_EMAIL_CODE_PATTERN = r"(?is)Your\s+verification\s+code\s+is:\s*(\d{6})"
WERYAI_POST_LOGIN_STATE_ATTEMPTS = 3
WERYAI_POST_LOGIN_STATE_INTERVAL_SECONDS = 2.0
WERYAI_POST_LOGIN_STATE_TIMEOUT_SECONDS = 6.0


class WeryAIProtocolMailboxWorker:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        log_fn: Callable[[str], None] = print,
        df_id: str = "",
        client_ip: str = "",
    ) -> None:
        self.client = WeryAIClient(
            proxy=proxy,
            log_fn=log_fn,
            df_id=df_id,
            client_ip=client_ip,
        )
        self.log = log_fn

    def run(
        self,
        *,
        email: str,
        password: str,
        otp_callback: Callable[[], str] | None,
    ) -> dict[str, Any]:
        email = text(email)
        password = text(password)
        if not email:
            raise RuntimeError("WeryAI registration requires an email address")
        if not password:
            raise RuntimeError("WeryAI registration requires a password")
        if not otp_callback:
            raise RuntimeError("WeryAI mailbox OTP callback is not configured")

        self.log(f"WeryAI Step1: send email ticket {email}")
        ticket = self.client.send_email_ticket(email)

        self.log("Waiting for WeryAI email verification code...")
        code = text(otp_callback())
        if not code:
            raise RuntimeError("Timed out waiting for WeryAI email verification code")
        self.log(f"WeryAI Step2: submit verification code {code[:2]}****")

        login = self.client.login_with_email_code(email, password, code)
        login_data = login.get("data") if isinstance(login.get("data"), dict) else {}
        token = text(login_data.get("access_token"))
        if not token:
            raise RuntimeError("WeryAI login response is missing access_token")

        state_partial = False
        try:
            state = fetch_weryai_account_state_with_polling(
                self.client,
                access_token=token,
                attempts=WERYAI_POST_LOGIN_STATE_ATTEMPTS,
                interval_seconds=WERYAI_POST_LOGIN_STATE_INTERVAL_SECONDS,
                timeout_seconds=WERYAI_POST_LOGIN_STATE_TIMEOUT_SECONDS,
                log_fn=self.log,
            )
        except Exception as exc:
            state_partial = True
            state = partial_weryai_account_state(access_token=token, client=self.client, error=exc)
            self.log(f"WeryAI login succeeded but account state refresh failed; saving partial account: {exc}")

        state.update(
            {
                "email": email,
                "password": password,
                "login": login,
                "email_ticket": ticket,
                "access_token": token,
                "authorization": token,
                "new_user": bool(login_data.get("new_user")),
            }
        )
        summary = summarize_weryai_account_state(state, fallback_email=email)
        overview = dict(summary.get("account_overview") or {})
        if state_partial:
            overview["account_state_partial"] = True
            overview["valid"] = True
        result = {
            "success": True,
            "email": email,
            "password": password,
            "user_id": text(overview.get("user_id") or overview.get("uid")),
            "token": token,
            "access_token": token,
            "authorization": token,
            "team_id": text(overview.get("team_id") or state.get("team_id")),
            "teamId": text(overview.get("team_id") or state.get("team_id")),
            "product_id": text(overview.get("product_id") or state.get("product_id")),
            "productId": text(overview.get("product_id") or state.get("product_id")),
            "df_id": text(state.get("df_id") or self.client.df_id),
            "client_ip": text(state.get("client_ip") or self.client.client_ip),
            "new_user": bool(login_data.get("new_user")),
            "login": login,
            "email_ticket": ticket,
            "credits": summary.get("credits", {}),
            "credits_balance": overview.get("credits_balance"),
            "remaining_credits": overview.get("remaining_credits"),
            "cookies": text(state.get("cookies") or state.get("cookie_header")),
            "cookie_header": text(state.get("cookie_header") or state.get("cookies")),
            "weryai_cookies": state.get("weryai_cookies") or [],
            "weryai_cookie_header": text(state.get("weryai_cookie_header") or state.get("cookie_header")),
            "user_info": state.get("user_info") or {},
            "account_summary": state.get("account_summary") or {},
            "current_context": state.get("current_context") or {},
            "account_overview": overview,
        }
        self.log(
            f"WeryAI registration/login succeeded: {email} "
            f"user={result['user_id'] or '-'} credits={overview.get('remaining_credits', '-')}"
        )
        return result
