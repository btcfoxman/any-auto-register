"""Higgsfield Clerk email registration worker."""
from __future__ import annotations

from typing import Any, Callable

from platforms.higg.core import (
    CLERK_TURNSTILE_SITE_KEY,
    HIGG_APP_URL,
    HiggClient,
    HiggRiskBlocked,
)


class HiggProtocolMailboxWorker:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        log_fn: Callable[[str], None] = print,
        turnstile_solver: Callable[..., str] | None = None,
        turnstile_token: str = "",
        user_agent: str = "",
        sec_ch_ua: str = "",
        sec_ch_ua_platform: str = "",
    ):
        self.proxy = proxy
        self.log = log_fn
        self.turnstile_solver = turnstile_solver
        self.turnstile_token = str(turnstile_token or "").strip()
        self.user_agent = user_agent
        self.sec_ch_ua = sec_ch_ua
        self.sec_ch_ua_platform = sec_ch_ua_platform

    def _captcha_token(self) -> str:
        if self.turnstile_token:
            return self.turnstile_token
        if not self.turnstile_solver:
            raise RuntimeError("Higgsfield registration requires a Turnstile provider")
        self.log("Higgsfield: solving Clerk invisible Turnstile")
        token = self.turnstile_solver(
            HIGG_APP_URL,
            CLERK_TURNSTILE_SITE_KEY,
            proxy=self.proxy or "",
        )
        token = str(token or "").strip()
        if not token:
            raise RuntimeError("Higgsfield Turnstile provider returned no token")
        return token

    def run(self, *, email: str, password: str, otp_callback: Callable[[], str]) -> dict[str, Any]:
        client = HiggClient(
            proxy=self.proxy,
            user_agent=self.user_agent,
            sec_ch_ua=self.sec_ch_ua,
            sec_ch_ua_platform=self.sec_ch_ua_platform,
            log_fn=self.log,
        )
        signup = client.create_signup(email, password, self._captcha_token())
        response = signup.get("response") if isinstance(signup.get("response"), dict) else {}
        signup_id = str(response.get("id") or "").strip()
        if not signup_id:
            raise RuntimeError("Higgsfield Clerk did not return sign_up_attempt_id")

        client.prepare_email_verification(signup_id)
        self.log("Higgsfield: waiting for the six-digit email verification code")
        code = str(otp_callback() or "").strip()
        if len(code) != 6 or not code.isdigit():
            raise RuntimeError("Higgsfield email verification code must be six digits")

        verified = client.attempt_email_verification(signup_id, code)
        verified_response = verified.get("response") if isinstance(verified.get("response"), dict) else {}
        if verified_response.get("status") != "complete":
            raise RuntimeError(f"Higgsfield Clerk signup is not complete: {verified_response.get('status')}")
        client.touch_session()
        auth = client.auth_state(
            email=email,
            user_id=str(verified_response.get("created_user_id") or ""),
        )

        state: dict[str, Any] = {}
        state_error = ""
        try:
            state = client.fetch_account_state()
            auth = client.auth_state(email=email, user_id=auth.user_id)
        except HiggRiskBlocked as exc:
            state_error = str(exc)
            self.log("Higgsfield: registration succeeded, but FNF initialization requires browser DataDome state")
        except Exception as exc:
            state_error = str(exc)
            self.log(f"Higgsfield: registration succeeded, account-state initialization deferred: {exc}")

        overview = {
            "platform": "higg",
            "valid": True,
            "generation_ready": bool(state.get("generation_ready")),
            "email": email,
            "user_id": auth.user_id,
            "workspace_id": auth.workspace_id,
            "credits_balance": state.get("credits_balance"),
            "free_generations": state.get("free_generations"),
            "state_error": state_error,
            "chips": list(state.get("chips") or ["Clerk active"]),
        }
        return {
            "email": email,
            "password": password,
            "user_id": auth.user_id,
            "session_id": auth.session_id,
            "token": auth.token,
            "clerk_jwt": auth.token,
            "workspace_id": auth.workspace_id,
            "cookies": auth.cookie_header,
            "cookie_header": auth.cookie_header,
            "datadome": auth.datadome,
            "user_agent": client.user_agent,
            "sec_ch_ua": client.sec_ch_ua,
            "sec_ch_ua_platform": client.sec_ch_ua_platform,
            "state_error": state_error,
            "account_overview": overview,
            **{
                key: state.get(key)
                for key in ("credits_balance", "total_credits", "free_generations", "wallet", "free_gens")
                if key in state
            },
        }

