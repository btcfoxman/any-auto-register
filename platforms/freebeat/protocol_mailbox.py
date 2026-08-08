"""Freebeat protocol mailbox registration worker."""
from __future__ import annotations

import time
from typing import Any, Callable

from platforms.freebeat.core import (
    FREEBEAT_DEFAULT_VERIFY_SOURCE,
    FreebeatClient,
    _safe_int,
    _total_credits_from_state,
    partial_freebeat_account_state,
    summarize_freebeat_account_state,
)
from platforms.freebeat.browser_email import (
    FREEBEAT_BROWSER_ACCEPT_LANGUAGE,
    FREEBEAT_BROWSER_LOCALE,
    FREEBEAT_BROWSER_TIMEZONE,
    FREEBEAT_BROWSER_USER_AGENT,
    send_email_verify_code_in_browser,
)


FREEBEAT_POST_LOGIN_STATE_ATTEMPTS = 3
FREEBEAT_POST_LOGIN_STATE_INTERVAL_SECONDS = 2.0
FREEBEAT_POST_LOGIN_STATE_TIMEOUT_SECONDS = 4.0
FREEBEAT_REWARD_SETTLE_SECONDS = 0.5


def _fetch_account_state_with_polling(
    client: FreebeatClient,
    token: str,
    *,
    attempts: int,
    interval_seconds: float,
    timeout_seconds: float,
    log_fn: Callable[[str], None],
) -> dict[str, Any]:
    total_attempts = max(1, int(attempts or 1))
    wait_seconds = max(0.0, float(interval_seconds or 0))
    last_error: Exception | None = None
    for attempt in range(1, total_attempts + 1):
        try:
            if attempt > 1:
                log_fn(f"Freebeat login state poll {attempt}/{total_attempts}")
            return client.fetch_account_state(token, timeout_seconds=timeout_seconds)
        except Exception as exc:
            last_error = exc
            if attempt >= total_attempts:
                break
            log_fn(f"Freebeat login state not ready, retry in {wait_seconds:.1f}s ({attempt}/{total_attempts}): {exc}")
            if wait_seconds:
                time.sleep(wait_seconds)
    raise last_error or RuntimeError("Freebeat login state polling failed")


def _signin_payload_from_state(state: dict[str, Any]) -> dict[str, Any] | None:
    payload = state.get("signin_payload")
    if isinstance(payload, dict) and payload:
        return payload
    signin_status = state.get("signin_status")
    if isinstance(signin_status, dict) and signin_status:
        return {"code": 0, "msg": "", "data": signin_status}
    return None


def _fetch_account_state_after_rewards(
    client: FreebeatClient,
    token: str,
    *,
    expected_min_total_credits: int | None,
) -> dict[str, Any]:
    if hasattr(client, "fetch_account_state_after_reward"):
        return client.fetch_account_state_after_reward(
            token,
            expected_min_total_credits=expected_min_total_credits,
            timeout_seconds=FREEBEAT_POST_LOGIN_STATE_TIMEOUT_SECONDS,
        )
    return client.fetch_account_state(token, timeout_seconds=FREEBEAT_POST_LOGIN_STATE_TIMEOUT_SECONDS)


class FreebeatProtocolMailboxWorker:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        log_fn: Callable[[str], None] = print,
        turnstile_solver: Callable[..., str] | None = None,
        next_action: str | None = None,
        next_router_state_tree: str | None = None,
        frontend_path: str = "",
        deployment_id: str = "",
        verify_source: str = FREEBEAT_DEFAULT_VERIFY_SOURCE,
        turnstile_token: str = "",
        browser_send_code: bool = False,
        browser_send_code_headless: bool = True,
        browser_send_code_required: bool = False,
        browser_send_code_allow_protocol_fallback: bool = False,
        browser_send_code_engine: str = "auto",
        browser_send_code_channel: str = "",
        browser_send_code_cdp_url: str = "",
        browser_send_code_cdp_launcher_url: str = "",
        browser_send_code_user_data_dir: str = "",
        browser_send_code_stealth: bool = True,
        browser_send_code_humanize: bool = True,
        browser_send_code_turnstile_click: bool = True,
        browser_send_code_turnstile_wait_seconds: float = 75.0,
        browser_send_code_accept_language: str = FREEBEAT_BROWSER_ACCEPT_LANGUAGE,
        browser_send_code_locale: str = FREEBEAT_BROWSER_LOCALE,
        browser_send_code_timezone: str = FREEBEAT_BROWSER_TIMEZONE,
        browser_send_code_user_agent: str = FREEBEAT_BROWSER_USER_AGENT,
        browser_send_code_timeout_seconds: float = 120,
    ):
        self.proxy = proxy
        self.frontend_path = frontend_path
        self.turnstile_solver = turnstile_solver
        self.client = FreebeatClient(
            proxy=proxy,
            log_fn=log_fn,
            frontend_path=frontend_path,
            deployment_id=deployment_id,
        )
        self.log = log_fn
        self.next_action = str(next_action or "").strip() or None
        self.next_router_state_tree = str(next_router_state_tree or "").strip() or None
        self.verify_source = str(verify_source or FREEBEAT_DEFAULT_VERIFY_SOURCE).strip() or FREEBEAT_DEFAULT_VERIFY_SOURCE
        self.turnstile_token = str(turnstile_token or "").strip()
        self.browser_send_code = bool(browser_send_code)
        self.browser_send_code_headless = bool(browser_send_code_headless)
        self.browser_send_code_required = bool(browser_send_code_required)
        self.browser_send_code_allow_protocol_fallback = bool(browser_send_code_allow_protocol_fallback)
        self.browser_send_code_engine = str(browser_send_code_engine or "auto").strip() or "auto"
        self.browser_send_code_channel = str(browser_send_code_channel or "").strip()
        self.browser_send_code_cdp_url = str(browser_send_code_cdp_url or "").strip()
        self.browser_send_code_cdp_launcher_url = str(browser_send_code_cdp_launcher_url or "").strip()
        self.browser_send_code_user_data_dir = str(browser_send_code_user_data_dir or "").strip()
        self.browser_send_code_stealth = bool(browser_send_code_stealth)
        self.browser_send_code_humanize = bool(browser_send_code_humanize)
        self.browser_send_code_turnstile_click = bool(browser_send_code_turnstile_click)
        self.browser_send_code_turnstile_wait_seconds = max(1.0, float(browser_send_code_turnstile_wait_seconds or 75.0))
        self.browser_send_code_accept_language = str(browser_send_code_accept_language or FREEBEAT_BROWSER_ACCEPT_LANGUAGE).strip()
        self.browser_send_code_locale = str(browser_send_code_locale or FREEBEAT_BROWSER_LOCALE).strip()
        self.browser_send_code_timezone = str(browser_send_code_timezone or FREEBEAT_BROWSER_TIMEZONE).strip()
        self.browser_send_code_user_agent = str(browser_send_code_user_agent or FREEBEAT_BROWSER_USER_AGENT).strip()
        self.browser_send_code_timeout_seconds = max(10.0, float(browser_send_code_timeout_seconds or 120))

    def _send_email_verify_code(self, email: str) -> dict[str, Any]:
        if self.browser_send_code and not self.turnstile_token:
            try:
                result = send_email_verify_code_in_browser(
                    email,
                    proxy=self.proxy,
                    log_fn=self.log,
                    turnstile_solver=self.turnstile_solver,
                    frontend_path=self.frontend_path,
                    verify_source=self.verify_source,
                    headless=self.browser_send_code_headless,
                    browser_engine=self.browser_send_code_engine,
                    browser_channel=self.browser_send_code_channel,
                    browser_cdp_url=self.browser_send_code_cdp_url,
                    browser_cdp_launcher_url=self.browser_send_code_cdp_launcher_url,
                    user_data_dir=self.browser_send_code_user_data_dir,
                    stealth_enabled=self.browser_send_code_stealth,
                    humanize=self.browser_send_code_humanize,
                    turnstile_click_enabled=self.browser_send_code_turnstile_click,
                    turnstile_wait_seconds=self.browser_send_code_turnstile_wait_seconds,
                    accept_language=self.browser_send_code_accept_language,
                    locale=self.browser_send_code_locale,
                    timezone_id=self.browser_send_code_timezone,
                    user_agent=self.browser_send_code_user_agent,
                    timeout_seconds=self.browser_send_code_timeout_seconds,
                )
                cookie_header = str(result.get("cookie_header") or "").strip()
                if cookie_header and hasattr(self.client, "merge_cookie_header"):
                    self.client.merge_cookie_header(cookie_header)
                deployment_id = str(result.get("deployment_id") or "").strip()
                if deployment_id:
                    update_deployment_id = getattr(self.client, "update_deployment_id", None)
                    if callable(update_deployment_id):
                        update_deployment_id(deployment_id)
                    else:
                        setattr(self.client, "_deployment_id", deployment_id)
                next_action_id = str(result.get("next_action_id") or "").strip()
                if next_action_id:
                    update_next_action_id = getattr(self.client, "update_next_action_id", None)
                    if callable(update_next_action_id):
                        update_next_action_id(next_action_id)
                    else:
                        setattr(self.client, "_next_action_id", next_action_id)
                token = str(result.get("turnstile_token") or "").strip()
                self.log(f"Freebeat browser sent email code; turnstile_token={'yes' if token else 'unknown'}")
                return result
            except Exception as exc:
                message = f"Freebeat browser send email code failed: {exc}"
                if self.browser_send_code_required or not self.browser_send_code_allow_protocol_fallback:
                    raise RuntimeError(message) from exc
                self.log(f"{message}; fallback to protocol send")

        kwargs = {"verify_source": self.verify_source}
        if self.turnstile_token:
            kwargs["turnstile_token"] = self.turnstile_token
        return self.client.send_email_verify_code(email, **kwargs)

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
        self._send_email_verify_code(email)

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

        state_partial = False
        try:
            state = _fetch_account_state_with_polling(
                self.client,
                token,
                attempts=FREEBEAT_POST_LOGIN_STATE_ATTEMPTS,
                interval_seconds=FREEBEAT_POST_LOGIN_STATE_INTERVAL_SECONDS,
                timeout_seconds=FREEBEAT_POST_LOGIN_STATE_TIMEOUT_SECONDS,
                log_fn=self.log,
            )
        except Exception as exc:
            state_partial = True
            state = partial_freebeat_account_state(token, client=self.client, error=exc)
            self.log(f"Freebeat 登录成功，但查询积分/状态失败，先保存账号: {exc}")
            self.log("Freebeat 登录后状态轮询失败不会跳过奖励，继续尝试问卷和每日签到")

        expected_total = _total_credits_from_state(state)
        questionnaire: dict[str, Any] = {"status": "skipped"}
        if auto_questionnaire:
            try:
                questionnaire = self.client.claim_questionnaire(token)
                credits_granted = _safe_int(questionnaire.get("credits_granted"))
                if questionnaire.get("status") == "claimed" and credits_granted > 0 and expected_total is not None:
                    expected_total += credits_granted
                self.log(f"Freebeat 问卷奖励状态: {questionnaire.get('status')} +{questionnaire.get('credits_granted', 0)}")
            except Exception as exc:
                if questionnaire_required:
                    raise
                questionnaire = {"status": "error", "error": str(exc)}
                self.log(f"Freebeat 问卷奖励失败，忽略并继续: {exc}")
        daily_sign_in: dict[str, Any] = {"status": "skipped"}
        if auto_daily_sign_in:
            try:
                if questionnaire.get("status") != "skipped" and FREEBEAT_REWARD_SETTLE_SECONDS:
                    time.sleep(FREEBEAT_REWARD_SETTLE_SECONDS)
                daily_sign_in = self.client.daily_sign_in(token, before_status=_signin_payload_from_state(state))
                reward_amount = _safe_int(daily_sign_in.get("reward_amount"))
                if daily_sign_in.get("status") == "signed" and reward_amount > 0 and expected_total is not None:
                    expected_total += reward_amount
                self.log(f"Freebeat 每日签到状态: {daily_sign_in.get('status')} +{daily_sign_in.get('reward_amount', 0)}")
            except Exception as exc:
                if daily_sign_in_required:
                    raise
                daily_sign_in = {"status": "error", "error": str(exc)}
                self.log(f"Freebeat 每日签到失败，忽略并继续: {exc}")

        rewards_attempted = questionnaire.get("status") != "skipped" or daily_sign_in.get("status") != "skipped"
        if rewards_attempted and FREEBEAT_REWARD_SETTLE_SECONDS:
            time.sleep(FREEBEAT_REWARD_SETTLE_SECONDS)
        if not state_partial or rewards_attempted:
            try:
                state = _fetch_account_state_after_rewards(
                    self.client,
                    token,
                    expected_min_total_credits=expected_total,
                )
            except Exception as exc:
                previous_state = dict(state or {})
                state = partial_freebeat_account_state(token, client=self.client, error=exc)
                state.update({k: v for k, v in previous_state.items() if k not in {"account_state_error"}})
                state["account_state_partial"] = True
                state["account_state_error"] = str(exc)
                self.log(f"Freebeat 最终状态刷新失败，保留已登录账号: {exc}")
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
