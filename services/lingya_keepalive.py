"""Background keepalive loop for LingYaQQ accounts."""
from __future__ import annotations

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
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 300
DEFAULT_BALANCE_INTERVAL_SECONDS = 60
DEFAULT_RETIRE_QUOTA_THRESHOLD = 57
DEFAULT_RETIRE_AFTER_HOURS = 24


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
                self._run_for_accounts(refresh_quota=balance_due, run_hello=heartbeat_due)
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

    def _target_account_ids(self, *, refresh_quota: bool = False) -> list[int]:
        config = self._config()
        retire_enabled = _as_bool(config.get("lingya_qq_keepalive_retire_enabled"), True)
        retire_quota_threshold = _bounded_int(
            config.get("lingya_qq_keepalive_retire_quota_threshold"),
            0,
            1_000_000,
            DEFAULT_RETIRE_QUOTA_THRESHOLD,
        )
        retire_after_hours = _bounded_int(
            config.get("lingya_qq_keepalive_retire_after_hours"),
            0,
            24 * 365 * 10,
            DEFAULT_RETIRE_AFTER_HOURS,
        )
        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            accounts = session.exec(select(AccountModel).where(AccountModel.platform == "lingya_qq")).all()
            graphs = load_account_graphs(session, [int(item.id or 0) for item in accounts if item.id])
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
                if _as_bool(overview.get("lingya_keepalive_disabled"), False):
                    continue
                if retire_enabled and self._should_retire_low_quota(
                    account=account,
                    overview=overview,
                    now=now,
                    quota_threshold=retire_quota_threshold,
                    after_hours=retire_after_hours,
                ):
                    self._retire_low_quota_account(
                        session=session,
                        account=account,
                        overview=overview,
                        now=now,
                        quota_threshold=retire_quota_threshold,
                        after_hours=retire_after_hours,
                    )
                    retired_count += 1
                    continue
                if refresh_quota:
                    validity = str(graph.get("validity_status") or overview.get("validity_status") or "").lower()
                    if validity == "invalid" or overview.get("valid") is False:
                        continue
                ids.append(account_id)
            if retired_count:
                session.commit()
            return ids

    def _should_retire_low_quota(
        self,
        *,
        account: AccountModel,
        overview: dict[str, Any],
        now: datetime,
        quota_threshold: int,
        after_hours: int,
    ) -> bool:
        quota_balance = _optional_int(overview.get("quota_balance"))
        if quota_balance is None:
            return False
        if quota_balance >= quota_threshold:
            return False
        age_hours = _account_age_hours(account, now)
        return age_hours is not None and age_hours >= after_hours

    def _retire_low_quota_account(
        self,
        *,
        session: Session,
        account: AccountModel,
        overview: dict[str, Any],
        now: datetime,
        quota_threshold: int,
        after_hours: int,
    ) -> None:
        quota_balance = _optional_int(overview.get("quota_balance"))
        account.updated_at = now
        patch_account_graph(
            session,
            account,
            lifecycle_status=AccountStatus.EXPIRED.value,
            summary_updates={
                "lingya_keepalive_retired": True,
                "lingya_keepalive_retire_reason": "low_quota_after_age",
                "lingya_keepalive_retire_quota_balance": quota_balance,
                "lingya_keepalive_retire_quota_threshold": quota_threshold,
                "lingya_keepalive_retire_after_hours": after_hours,
                "lingya_keepalive_retired_at": _isoformat_z(now),
                "status_note": f"low quota below {quota_threshold} after {after_hours}h",
            },
        )
        session.add(account)
        print(
            "[LingYaKeepalive] account "
            f"{int(account.id or 0)} retired: quota={quota_balance}, "
            f"threshold={quota_threshold}, age>={after_hours}h"
        )

    def _run_for_accounts(self, *, refresh_quota: bool, run_hello: bool = True) -> None:
        runtime = PlatformRuntime()
        for account_id in self._target_account_ids(refresh_quota=refresh_quota):
            if not self._try_lock_account(account_id):
                continue
            try:
                command = ActionExecutionCommand(
                    platform="lingya_qq",
                    account_id=account_id,
                    action_id="keepalive_sync",
                    params={
                        "force_refresh": "false",
                        "refresh_quota": "true" if refresh_quota else "false",
                        "run_hello": "true" if run_hello else "false",
                    },
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


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


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


lingya_keepalive_worker = LingYaKeepaliveWorker()
