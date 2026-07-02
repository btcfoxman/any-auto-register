"""Background keepalive loop for ImgsWeryai accounts."""
from __future__ import annotations

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
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 300


class ImgsWeryaiKeepaliveWorker:
    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_heartbeat = 0.0
        self._account_locks: set[int] = set()

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="imgs-weryai-keepalive")
            self._thread.start()
            print("[ImgsWeryaiKeepalive] started")

    def stop(self) -> None:
        with self._lock:
            self._running = False
        print("[ImgsWeryaiKeepalive] stopping")

    def _loop(self) -> None:
        time.sleep(10)
        while self._is_running():
            config = self._config()
            if not _as_bool(config.get("imgs_weryai_keepalive_enabled"), True):
                self._sleep(60)
                continue

            now = time.time()
            heartbeat_interval = _bounded_int(
                config.get("imgs_weryai_heartbeat_interval_seconds"),
                30,
                86400,
                DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
            )
            heartbeat_due = now - self._last_heartbeat >= heartbeat_interval
            if heartbeat_due:
                self._run_for_accounts()
                self._last_heartbeat = now
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
            accounts = session.exec(select(AccountModel).where(AccountModel.platform == "imgs_weryai")).all()
            graphs = load_account_graphs(session, [int(item.id or 0) for item in accounts if item.id])
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
                if _as_bool(overview.get("imgs_weryai_keepalive_disabled"), False):
                    continue
                validity = str(graph.get("validity_status") or overview.get("validity_status") or "").lower()
                if validity == "invalid" or overview.get("valid") is False:
                    continue
                ids.append(account_id)
            return ids

    def _run_for_accounts(self) -> None:
        runtime = PlatformRuntime()
        config = self._config()
        daily_sign_enabled = _as_bool(config.get("imgs_weryai_daily_sign_in_enabled"), True)
        for account_id in self._target_account_ids():
            if not self._try_lock_account(account_id):
                continue
            try:
                should_sign = daily_sign_enabled and self._daily_sign_due(account_id)
                action_id = "daily_sign_in" if should_sign else "keepalive_sync"
                params = {"force_refresh": "false"}
                if should_sign:
                    configured_day = str(config.get("imgs_weryai_daily_sign_in_day") or "").strip()
                    if configured_day:
                        params["day"] = configured_day
                command = ActionExecutionCommand(
                    platform="imgs_weryai",
                    account_id=account_id,
                    action_id=action_id,
                    params=params,
                )
                result = runtime.execute_action(command, log_fn=lambda message: print(f"[ImgsWeryaiKeepalive] {message}"))
                if not getattr(result, "ok", False):
                    print(f"[ImgsWeryaiKeepalive] account {account_id} failed: {getattr(result, 'error', '')}")
            except Exception as exc:
                print(f"[ImgsWeryaiKeepalive] account {account_id} failed: {exc}")
            finally:
                self._unlock_account(account_id)

    def _daily_sign_due(self, account_id: int) -> bool:
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            with Session(engine) as session:
                graph = load_account_graphs(session, [account_id]).get(account_id, {})
                overview = graph.get("overview") or {}
                if _as_bool(overview.get("imgs_weryai_daily_sign_in_disabled"), False):
                    return False
                status = str(
                    overview.get("daily_sign_in_status")
                    or overview.get("last_daily_sign_in_status")
                    or ""
                ).strip().lower()
                signed_date = str(overview.get("imgs_weryai_daily_sign_in_date") or "").strip()
                if not signed_date:
                    signed_at = str(overview.get("daily_sign_in_at") or "").strip()
                    signed_date = signed_at[:10] if len(signed_at) >= 10 else ""
                if signed_date == today and status in {"signed", "already_signed"}:
                    return False
                return True
        except Exception:
            return True

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
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


imgs_weryai_keepalive_worker = ImgsWeryaiKeepaliveWorker()
