"""Background keepalive loop for LingYaQQ accounts."""
from __future__ import annotations

import threading
import time
from typing import Any

from sqlmodel import Session, select

from core.account_graph import load_account_graphs
from core.db import AccountModel, engine
from domain.actions import ActionExecutionCommand
from infrastructure.platform_runtime import PlatformRuntime


ACTIVE_LIFECYCLE_STATUSES = {"registered", "trial", "subscribed"}
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 300
DEFAULT_BALANCE_INTERVAL_SECONDS = 600


class LingYaKeepaliveWorker:
    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_heartbeat = 0.0
        self._last_balance = 0.0
        self._account_locks: set[int] = set()

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="lingya-keepalive")
            self._thread.start()
            print("[LingYaKeepalive] 已启动")

    def stop(self) -> None:
        with self._lock:
            self._running = False
        print("[LingYaKeepalive] 停止中")

    def _loop(self) -> None:
        time.sleep(10)
        while self._is_running():
            config = self._config()
            if not _as_bool(config.get("lingya_qq_keepalive_enabled"), True):
                self._sleep(60)
                continue

            now = time.time()
            heartbeat_interval = _bounded_int(
                config.get("lingya_qq_heartbeat_interval_seconds"),
                30,
                86400,
                DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
            )
            balance_interval = _bounded_int(
                config.get("lingya_qq_balance_interval_seconds"),
                60,
                86400,
                DEFAULT_BALANCE_INTERVAL_SECONDS,
            )
            heartbeat_due = now - self._last_heartbeat >= heartbeat_interval
            balance_due = now - self._last_balance >= balance_interval
            if heartbeat_due or balance_due:
                self._run_for_accounts(refresh_quota=balance_due)
            if heartbeat_due:
                self._last_heartbeat = now
            if balance_due:
                self._last_balance = now
            self._sleep(5)

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
        with Session(engine) as session:
            accounts = session.exec(select(AccountModel).where(AccountModel.platform == "lingya_qq")).all()
            graphs = load_account_graphs(session, [int(item.id or 0) for item in accounts if item.id])
            ids: list[int] = []
            for account in accounts:
                account_id = int(account.id or 0)
                if account_id <= 0:
                    continue
                lifecycle = str(graphs.get(account_id, {}).get("lifecycle_status") or "registered")
                if lifecycle in ACTIVE_LIFECYCLE_STATUSES:
                    ids.append(account_id)
            return ids

    def _run_for_accounts(self, *, refresh_quota: bool) -> None:
        runtime = PlatformRuntime()
        for account_id in self._target_account_ids():
            if not self._try_lock_account(account_id):
                continue
            try:
                command = ActionExecutionCommand(
                    platform="lingya_qq",
                    account_id=account_id,
                    action_id="keepalive_sync",
                    params={"refresh_quota": "true" if refresh_quota else "false"},
                )
                result = runtime.execute_action(command, log_fn=lambda message: print(f"[LingYaKeepalive] {message}"))
                if not getattr(result, "ok", False):
                    print(f"[LingYaKeepalive] account {account_id} failed: {getattr(result, 'error', '')}")
            except Exception as exc:
                print(f"[LingYaKeepalive] account {account_id} failed: {exc}")
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


lingya_keepalive_worker = LingYaKeepaliveWorker()
