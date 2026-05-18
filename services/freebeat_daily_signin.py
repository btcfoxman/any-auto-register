"""Background daily sign-in loop for Freebeat accounts."""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from core.account_graph import load_account_graphs
from core.db import AccountModel, engine
from domain.actions import ActionExecutionCommand
from infrastructure.platform_runtime import PlatformRuntime


ACTIVE_LIFECYCLE_STATUSES = {"registered", "trial", "subscribed"}
FREEBEAT_TOKEN_KEYS = {"access_token", "accessToken", "legacy_token", "device_token", "deviceToken"}
DEFAULT_SIGN_IN_MIN_INTERVAL_SECONDS = 1800
DEFAULT_SIGN_IN_MAX_INTERVAL_SECONDS = 7200
DEFAULT_START_DELAY_SECONDS = 15


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
        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            accounts = session.exec(select(AccountModel).where(AccountModel.platform == "freebeat")).all()
            account_ids = [int(item.id or 0) for item in accounts if item.id]
            graphs = load_account_graphs(session, account_ids)
            ids: list[int] = []
            for account in accounts:
                account_id = int(account.id or 0)
                if account_id <= 0:
                    continue
                graph = graphs.get(account_id, {})
                overview = graph.get("overview") or {}
                lifecycle = str(graph.get("lifecycle_status") or overview.get("lifecycle_status") or "registered")
                if lifecycle not in ACTIVE_LIFECYCLE_STATUSES:
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
            return ids

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
                    print(
                        "[FreebeatDailySignIn] account "
                        f"{account_id} status={data.get('daily_sign_in_status') or data.get('last_daily_sign_in_status') or 'ok'} "
                        f"credits={data.get('total_credits', '-')}"
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


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
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


freebeat_daily_signin_worker = FreebeatDailySignInWorker()

