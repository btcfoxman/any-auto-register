"""Background daily sign-in loop for Freebeat accounts."""
from __future__ import annotations

import random
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from core.account_graph import load_account_graphs, patch_account_graph
from core.base_platform import AccountStatus
from core.db import AccountModel, engine
from domain.actions import ActionExecutionCommand
from infrastructure.platform_runtime import PlatformRuntime


ACTIVE_LIFECYCLE_STATUSES = {"registered", "trial", "subscribed"}
FREEBEAT_TOKEN_KEYS = {"access_token", "accessToken", "legacy_token", "device_token", "deviceToken"}
DEFAULT_SIGN_IN_MIN_INTERVAL_SECONDS = 1800
DEFAULT_SIGN_IN_MAX_INTERVAL_SECONDS = 7200
DEFAULT_START_DELAY_SECONDS = 15
DEFAULT_RETIRE_CREDIT_THRESHOLD = 1
DEFAULT_RETIRE_AFTER_HOURS = 24


class FreebeatDailySignInWorker:
    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._account_locks: set[int] = set()

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="freebeat-daily-signin")
            self._thread.start()
            print("[FreebeatDailySignIn] 已启动")

    def stop(self) -> None:
        with self._lock:
            self._running = False
        print("[FreebeatDailySignIn] 停止中")

    def _loop(self) -> None:
        self._sleep(DEFAULT_START_DELAY_SECONDS)
        while self._is_running():
            config = self._config()
            if not _as_bool(config.get("freebeat_daily_sign_in_enabled"), True):
                self._sleep(60)
                continue
            self._run_for_accounts()
            self._sleep(_dynamic_interval_seconds(config))

    def _is_running(self) -> bool:
        with self._lock:
            return self._running

    def _sleep(self, seconds: int) -> None:
        deadline = time.time() + max(1, int(seconds))
        while time.time() < deadline:
            if not self._is_running():
                return
            time.sleep(min(1, max(0, deadline - time.time())))

    def _config(self) -> dict[str, Any]:
        try:
            from core.config_store import config_store

            return config_store.get_all()
        except Exception:
            return {}

    def _target_account_ids(self) -> list[int]:
        config = self._config()
        retire_enabled, retire_credit_threshold, retire_after_hours = _retire_settings(config)
        now = datetime.now(timezone.utc)
        retired_ids: list[int] = []
        with Session(engine) as session:
            accounts = session.exec(select(AccountModel).where(AccountModel.platform == "freebeat")).all()
            account_ids = [int(item.id or 0) for item in accounts if item.id]
            graphs = load_account_graphs(session, account_ids)
            ids: list[int] = []
            retired_count = 0
            for account in accounts:
                account_id = int(account.id or 0)
                if account_id <= 0:
                    continue
                graph = graphs.get(account_id, {})
                overview = graph.get("overview") or {}
                lifecycle = str(graph.get("lifecycle_status") or overview.get("lifecycle_status") or "registered")
                if lifecycle not in ACTIVE_LIFECYCLE_STATUSES:
                    continue
                if retire_enabled and self._should_retire_low_credits(
                    account=account,
                    overview=overview,
                    now=now,
                    credit_threshold=retire_credit_threshold,
                    after_hours=retire_after_hours,
                ):
                    self._retire_low_credit_account(
                        session=session,
                        account=account,
                        overview=overview,
                        now=now,
                        credit_threshold=retire_credit_threshold,
                        after_hours=retire_after_hours,
                    )
                    retired_ids.append(account_id)
                    retired_count += 1
                    continue
                validity = str(graph.get("validity_status") or overview.get("validity_status") or "").lower()
                if validity == "invalid" or overview.get("valid") is False:
                    continue
                if _as_bool(overview.get("freebeat_daily_sign_in_disabled"), False):
                    continue
                if not _has_freebeat_token(graph):
                    continue
                if _signed_until_future_refresh(overview, now):
                    continue
                ids.append(account_id)
            if retired_count:
                session.commit()
        for account_id in retired_ids:
            self._sync_retired_remote_auto_maintenance(account_id)
        return ids

    def _should_retire_low_credits(
        self,
        *,
        account: AccountModel,
        overview: dict[str, Any],
        now: datetime,
        credit_threshold: int,
        after_hours: int,
    ) -> bool:
        credit_balance = _credit_balance(overview)
        if credit_balance is None:
            return False
        if credit_balance >= credit_threshold:
            return False
        age_hours = _account_age_hours(account, now)
        return age_hours is not None and age_hours >= after_hours

    def _retire_low_credit_account(
        self,
        *,
        session: Session,
        account: AccountModel,
        overview: dict[str, Any],
        now: datetime,
        credit_threshold: int,
        after_hours: int,
    ) -> None:
        credit_balance = _credit_balance(overview)
        retired_at = _isoformat_z(now)
        account.updated_at = now
        patch_account_graph(
            session,
            account,
            lifecycle_status=AccountStatus.EXPIRED.value,
            summary_updates={
                "freebeat_retired": True,
                "freebeat_retire_reason": "low_credits_after_age",
                "freebeat_retire_credit_balance": credit_balance,
                "freebeat_retire_credit_threshold": credit_threshold,
                "freebeat_retire_after_hours": after_hours,
                "freebeat_retired_at": retired_at,
                "freebeat_daily_sign_in_disabled": True,
                "freebeat_daily_sign_in_state": "disabled",
                "freebeat_daily_sign_in_disabled_reason": "low_credits_after_age",
                "freebeat_daily_sign_in_disabled_at": retired_at,
                "freebeat_daily_sign_in_resumed_at": "",
                "freebeat_keepalive_disabled": True,
                "freebeat_keepalive_state": "disabled",
                "freebeat_keepalive_disabled_reason": "low_credits_after_age",
                "freebeat_keepalive_disabled_at": retired_at,
                "freebeat_keepalive_resumed_at": "",
                "freebeat2api_enable_auto_maintenance": False,
                "status_note": f"low credits below {credit_threshold} after {after_hours}h",
            },
        )
        session.add(account)
        print(
            "[FreebeatDailySignIn] account "
            f"{int(account.id or 0)} retired: credits={credit_balance}, "
            f"threshold={credit_threshold}, age>={after_hours}h"
        )

    def _retire_result_if_needed(self, account_id: int, data: dict[str, Any]) -> bool:
        config = self._config()
        retire_enabled, retire_credit_threshold, retire_after_hours = _retire_settings(config)
        if not retire_enabled:
            return False
        now = datetime.now(timezone.utc)
        retired = False
        with Session(engine) as session:
            account = session.get(AccountModel, account_id)
            if not account or account.platform != "freebeat":
                return False
            if self._should_retire_low_credits(
                account=account,
                overview=data,
                now=now,
                credit_threshold=retire_credit_threshold,
                after_hours=retire_after_hours,
            ):
                self._retire_low_credit_account(
                    session=session,
                    account=account,
                    overview=data,
                    now=now,
                    credit_threshold=retire_credit_threshold,
                    after_hours=retire_after_hours,
                )
                session.commit()
                retired = True
        if retired:
            self._sync_retired_remote_auto_maintenance(account_id)
        return retired

    def _sync_retired_remote_auto_maintenance(self, account_id: int) -> None:
        try:
            from core.freebeat2api_sync import sync_account_to_freebeat2api
            from core.platform_accounts import build_platform_account

            with Session(engine) as session:
                account_model = session.get(AccountModel, account_id)
                if not account_model:
                    return
                account = build_platform_account(session, account_model)
            result = sync_account_to_freebeat2api(
                account,
                log_fn=lambda message: print(f"[FreebeatDailySignIn] {message}"),
                extra_overrides={"freebeat2api_enable_auto_maintenance": False},
            )
            if result:
                print(f"[FreebeatDailySignIn] account {account_id} remote auto maintenance disabled")
        except Exception as exc:
            print(f"[FreebeatDailySignIn] account {account_id} remote auto maintenance sync failed: {exc}")

    def _run_for_accounts(self) -> None:
        runtime = PlatformRuntime()
        for account_id in self._target_account_ids():
            if not self._try_lock_account(account_id):
                continue
            try:
                command = ActionExecutionCommand(
                    platform="freebeat",
                    account_id=account_id,
                    action_id="daily_sign_in",
                    params={},
                )
                result = runtime.execute_action(command, log_fn=lambda message: print(f"[FreebeatDailySignIn] {message}"))
                if getattr(result, "ok", False):
                    data = getattr(result, "data", {}) or {}
                    retired = self._retire_result_if_needed(account_id, data)
                    print(
                        "[FreebeatDailySignIn] account "
                        f"{account_id} status={data.get('daily_sign_in_status') or data.get('last_daily_sign_in_status') or 'ok'} "
                        f"credits={data.get('total_credits', '-')} retired={retired}"
                    )
                else:
                    print(f"[FreebeatDailySignIn] account {account_id} failed: {getattr(result, 'error', '')}")
            except Exception as exc:
                print(f"[FreebeatDailySignIn] account {account_id} failed: {exc}")
            finally:
                self._unlock_account(account_id)

    def _try_lock_account(self, account_id: int) -> bool:
        with self._lock:
            if account_id in self._account_locks:
                return False
            self._account_locks.add(account_id)
            return True

    def _unlock_account(self, account_id: int) -> None:
        with self._lock:
            self._account_locks.discard(account_id)


def _dynamic_interval_seconds(config: dict[str, Any]) -> int:
    minimum = _bounded_int(
        config.get("freebeat_daily_sign_in_min_interval_seconds"),
        60,
        86400,
        DEFAULT_SIGN_IN_MIN_INTERVAL_SECONDS,
    )
    maximum = _bounded_int(
        config.get("freebeat_daily_sign_in_max_interval_seconds"),
        60,
        86400,
        DEFAULT_SIGN_IN_MAX_INTERVAL_SECONDS,
    )
    if maximum < minimum:
        maximum = minimum
    return random.randint(minimum, maximum)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "是"}


def _bounded_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _retire_settings(config: dict[str, Any]) -> tuple[bool, int, int]:
    enabled = _as_bool(config.get("freebeat_retire_low_credit_enabled"), True)
    threshold = _bounded_int(
        config.get("freebeat_retire_credit_threshold"),
        0,
        1_000_000,
        DEFAULT_RETIRE_CREDIT_THRESHOLD,
    )
    after_hours = _bounded_int(
        config.get("freebeat_retire_after_hours"),
        0,
        24 * 365 * 10,
        DEFAULT_RETIRE_AFTER_HOURS,
    )
    return enabled, threshold, after_hours


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        match = re.search(r"-?\d+", str(value))
        return int(match.group(0)) if match else None


def _credit_balance(data: dict[str, Any]) -> int | None:
    for key in ("total_credits", "remaining_credits", "free_credits"):
        value = _optional_int(data.get(key))
        if value is not None:
            return value
    credits = data.get("credits")
    if isinstance(credits, dict):
        for key in ("totalCredits", "total_credits", "free"):
            value = _optional_int(credits.get(key))
            if value is not None:
                return value
    return None


def _has_freebeat_token(graph: dict[str, Any]) -> bool:
    for item in graph.get("credentials") or []:
        if not isinstance(item, dict):
            continue
        if item.get("scope") != "platform":
            continue
        if item.get("key") in FREEBEAT_TOKEN_KEYS and item.get("value") not in (None, ""):
            return True
    return False


def _signed_until_future_refresh(overview: dict[str, Any], now: datetime) -> bool:
    if not _as_bool(overview.get("signed_today"), False):
        return False
    next_refresh_at = _optional_int(overview.get("next_refresh_at"))
    if next_refresh_at is None:
        return False
    now_ms = int(now.timestamp() * 1000)
    return next_refresh_at > now_ms + 60_000


def _account_age_hours(account: AccountModel, now: datetime) -> float | None:
    created_at = getattr(account, "created_at", None)
    if not isinstance(created_at, datetime):
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - created_at.astimezone(timezone.utc)).total_seconds() / 3600)


def _isoformat_z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


freebeat_daily_signin_worker = FreebeatDailySignInWorker()
