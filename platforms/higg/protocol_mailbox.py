"""Higgsfield Clerk email registration worker."""
from __future__ import annotations

from typing import Any, Callable

from platforms.higg.core import (
    CLERK_TURNSTILE_SITE_KEY,
    HIGG_APP_URL,
    HiggClient,
    HiggRiskBlocked,
)


def _is_browser_risk_error(error: Any) -> bool:
    text = str(error or "").strip().lower()
    return any(
        marker in text
        for marker in (
            "captcha_invalid",
            "error loading captcha",
            "turnstile",
            "cloudflare",
            "datadome",
            "risk context",
        )
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
        browser_enabled: bool = True,
        browser_required: bool = True,
        browser_options: dict[str, Any] | None = None,
        browser_session_factory: Callable[..., Any] | None = None,
    ):
        self.proxy = proxy
        self.log = log_fn
        self.turnstile_solver = turnstile_solver
        self.turnstile_token = str(turnstile_token or "").strip()
        self.user_agent = user_agent
        self.sec_ch_ua = sec_ch_ua
        self.sec_ch_ua_platform = sec_ch_ua_platform
        self.browser_enabled = bool(browser_enabled)
        self.browser_required = bool(browser_required)
        self.browser_options = dict(browser_options or {})
        self.browser_session_factory = browser_session_factory

    def _captcha_token(self, browser_token: str = "") -> str:
        if self.turnstile_token:
            return self.turnstile_token
        if browser_token:
            return browser_token
        if not self.turnstile_solver:
            raise RuntimeError("Higgsfield registration requires a browser Turnstile context")
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

    def _new_browser_session(self, browser_mode: str = ""):
        options = dict(self.browser_options)
        if browser_mode:
            options["browser_mode"] = browser_mode
        if self.browser_session_factory:
            return self.browser_session_factory(
                proxy=self.proxy,
                log_fn=self.log,
                **options,
            )
        from platforms.higg.browser_context import HiggBrowserSession

        return HiggBrowserSession(
            proxy=self.proxy,
            log_fn=self.log,
            **options,
        )

    def _browser_modes(self) -> list[str]:
        primary = str(
            self.browser_options.get("browser_mode") or "native_chrome"
        ).strip()
        fallback_value = self.browser_options.get("browser_fallback_mode")
        fallback = str(
            "bitbrowser" if fallback_value is None else fallback_value
        ).strip()
        return list(dict.fromkeys(item for item in (primary, fallback) if item))

    def run(self, *, email: str, password: str, otp_callback: Callable[[], str]) -> dict[str, Any]:
        browser = None
        browser_error = ""
        browser_context: dict[str, Any] = {}
        effective_proxy = str(self.proxy or "").strip()
        client: HiggClient | None = None
        try:
            signup: dict[str, Any] = {}
            if self.browser_enabled:
                errors: list[str] = []
                for browser_mode in self._browser_modes():
                    browser = self._new_browser_session(browser_mode)
                    try:
                        self.log(f"Higgsfield: trying browser mode {browser_mode}")
                        browser.start()
                        signup = browser.create_signup(
                            email=email,
                            password=password,
                            captcha_token=self.turnstile_token,
                        )
                        effective_proxy = str(
                            getattr(browser, "proxy_url", "") or effective_proxy
                        ).strip()
                        browser_error = ""
                        break
                    except Exception as exc:
                        errors.append(f"{browser_mode}: {exc}")
                        self.log(
                            f"Higgsfield: browser mode {browser_mode} failed before signup: {exc}"
                        )
                        browser.close()
                        browser = None
                        if _is_browser_risk_error(exc):
                            self.log(
                                "Higgsfield: current proxy exhausted its browser challenge budget; "
                                "skip same-proxy browser fallback"
                            )
                            break
                if not signup:
                    browser_error = "; ".join(errors)
                    if self.browser_required:
                        raise RuntimeError(
                            "Higgsfield browser risk context is required; "
                            f"all configured browser modes failed: {browser_error}"
                        )

            response = signup.get("response") if isinstance(signup.get("response"), dict) else {}
            signup_id = str(response.get("id") or "").strip()
            if signup and not signup_id:
                raise RuntimeError("Higgsfield Clerk did not return sign_up_attempt_id")

            if signup and browser is not None and not browser_error:
                self.log("Higgsfield: waiting for the six-digit email verification code")
                code = str(otp_callback() or "").strip()
                if len(code) != 6 or not code.isdigit():
                    raise RuntimeError("Higgsfield email verification code must be six digits")
                browser_context = browser.complete_email_verification(code)
                token = str(browser_context.get("clerk_jwt") or "").strip()
                if not token:
                    raise RuntimeError(
                        "Higgsfield BitBrowser completed verification without a Clerk session JWT"
                    )
                client = HiggClient(
                    proxy=effective_proxy or None,
                    token=token,
                    cookies=browser_context.get("cookies"),
                    datadome=str(browser_context.get("datadome") or ""),
                    user_agent=str(browser_context.get("user_agent") or self.user_agent),
                    sec_ch_ua=str(browser_context.get("sec_ch_ua") or self.sec_ch_ua),
                    sec_ch_ua_platform=str(
                        browser_context.get("sec_ch_ua_platform")
                        or self.sec_ch_ua_platform
                    ),
                    log_fn=self.log,
                )
                auth = client.auth_state(email=email)
            else:
                client = HiggClient(
                    proxy=effective_proxy or None,
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
                verified_response = (
                    verified.get("response")
                    if isinstance(verified.get("response"), dict)
                    else {}
                )
                if verified_response.get("status") != "complete":
                    raise RuntimeError(
                        "Higgsfield Clerk signup is not complete: "
                        f"{verified_response.get('status')}"
                    )
                client.touch_session()
                auth = client.auth_state(
                    email=email,
                    user_id=str(verified_response.get("created_user_id") or ""),
                )

            state: dict[str, Any] = {}
            state_error = browser_error
            try:
                self.log("Higgsfield: initializing Seedance Academy access")
                client.ensure_seedance_onboarding()
                self.log("Higgsfield: confirming the Seedance media upload agreement")
                client.ensure_upload_agreements()
                state = client.fetch_account_state()
                auth = client.auth_state(email=email, user_id=auth.user_id)
                state_error = ""
            except HiggRiskBlocked as exc:
                state_error = str(exc)
                self.log("Higgsfield: registration succeeded, but FNF is still blocked by DataDome")
            except Exception as exc:
                state_error = str(exc)
                self.log(f"Higgsfield: registration succeeded, account-state initialization deferred: {exc}")
        finally:
            if browser is not None:
                browser.close()

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
            "proxy_url": effective_proxy,
            "browser_profile_id": browser_context.get("browser_profile_id"),
            "browser_webdriver": browser_context.get("browser_webdriver"),
            "risk_probe": browser_context.get("risk_probe"),
            "state_error": state_error,
            "account_overview": overview,
            **{
                key: state.get(key)
                for key in ("credits_balance", "total_credits", "free_generations", "wallet", "free_gens")
                if key in state
            },
        }
