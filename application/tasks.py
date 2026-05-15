"""Task orchestration and persistence helpers."""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlmodel import Session, select, func

from core.account_graph import (
    load_account_graphs,
    patch_account_graph,
    recover_lifecycle_status_for_valid_account,
)
from core.base_platform import AccountStatus, RegisterConfig
from core.datetime_utils import format_local_clock, serialize_datetime
from core.db import AccountModel, TaskEventModel, TaskLog, TaskModel, engine, save_account
from core.platform_accounts import build_platform_account
from core.registry import get
from infrastructure.platform_runtime import PlatformRuntime

TASK_TYPE_REGISTER = "register"
TASK_TYPE_ACCOUNT_CHECK = "account_check"
TASK_TYPE_ACCOUNT_CHECK_ALL = "account_check_all"
TASK_TYPE_PLATFORM_ACTION = "platform_action"

TASK_STATUS_PENDING = "pending"
TASK_STATUS_CLAIMED = "claimed"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCEEDED = "succeeded"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_INTERRUPTED = "interrupted"
TASK_STATUS_CANCEL_REQUESTED = "cancel_requested"
TASK_STATUS_CANCELLED = "cancelled"

TERMINAL_TASK_STATUSES = {
    TASK_STATUS_SUCCEEDED,
    TASK_STATUS_FAILED,
    TASK_STATUS_INTERRUPTED,
    TASK_STATUS_CANCELLED,
}
ACTIVE_TASK_STATUSES = {
    TASK_STATUS_CLAIMED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_CANCEL_REQUESTED,
}
ACTIVE_ACCOUNT_CHECK_LIFECYCLE_STATUSES = {"registered", "trial", "subscribed"}
LINGYA_STATUS_LABELS = {
    "signed": "已签到",
    "already_signed": "今日已签到",
    "not_available": "不可用",
    "panel_unavailable": "签到面板不可用",
    "disabled": "已关闭",
    "skipped": "已跳过",
    "succeeded": "成功",
    "failed": "失败",
}

_task_locks: dict[str, threading.Lock] = {}
_task_locks_guard = threading.Lock()


def _lingya_status_label(value: Any) -> str:
    text = str(value or "").strip()
    return LINGYA_STATUS_LABELS.get(text, text)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat().replace("+00:00", "Z")


def _serialize_datetime(value: datetime | None) -> str | None:
    return serialize_datetime(value)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return _serialize_datetime(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def _dump_json(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False, default=_json_default)


def _task_lock(task_id: str) -> threading.Lock:
    with _task_locks_guard:
        lock = _task_locks.get(task_id)
        if lock is None:
            lock = threading.Lock()
            _task_locks[task_id] = lock
        return lock


def _mutate_task(task_id: str, fn: Callable[[TaskModel], None]) -> Optional[TaskModel]:
    with _task_lock(task_id):
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                return None
            fn(task)
            task.updated_at = _utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
            return task


def _save_task_log(platform: str, email: str, status: str, error: str = "", detail: dict | None = None) -> None:
    with Session(engine) as session:
        log = TaskLog(
            platform=platform,
            email=email,
            status=status,
            error=error,
            detail_json=_dump_json(detail or {}),
        )
        session.add(log)
        session.commit()


def _task_result_seed(result: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {"errors": [], "cashier_urls": [], "data": None}
    if result:
        base.update(result)
    return base


def _task_account_keys(task_type: str, payload: dict[str, Any]) -> list[str]:
    if task_type in {TASK_TYPE_ACCOUNT_CHECK, TASK_TYPE_PLATFORM_ACTION}:
        account_id = int(payload.get("account_id", 0) or 0)
        if account_id > 0:
            return [f"account:{account_id}"]
    return []


def serialize_task(task: TaskModel) -> dict[str, Any]:
    result = task.get_result()
    progress_total = int(task.progress_total or 0)
    progress_current = int(task.progress_current or 0)
    return {
        "id": task.id,
        "task_id": task.id,
        "type": task.type,
        "platform": task.platform,
        "status": task.status,
        "terminal": task.status in TERMINAL_TASK_STATUSES,
        "cancellable": task.status in {TASK_STATUS_PENDING, TASK_STATUS_CLAIMED, TASK_STATUS_RUNNING, TASK_STATUS_CANCEL_REQUESTED},
        "progress": f"{progress_current}/{progress_total}" if progress_total else "0/0",
        "progress_detail": {
            "current": progress_current,
            "total": progress_total,
            "label": f"{progress_current}/{progress_total}" if progress_total else "0/0",
        },
        "success": int(task.success_count or 0),
        "error_count": int(task.error_count or 0),
        "errors": list(result.get("errors", [])),
        "cashier_urls": list(result.get("cashier_urls", [])),
        "data": result.get("data"),
        "result": result,
        "error": task.error,
        "created_at": _serialize_datetime(task.created_at),
        "started_at": _serialize_datetime(task.started_at),
        "finished_at": _serialize_datetime(task.finished_at),
        "updated_at": _serialize_datetime(task.updated_at),
    }


def serialize_event(event: TaskEventModel) -> dict[str, Any]:
    return {
        "id": event.id,
        "task_id": event.task_id,
        "type": event.type,
        "level": event.level,
        "message": event.message,
        "line": f"[{format_local_clock(event.created_at)}] {event.message}",
        "detail": event.get_detail(),
        "created_at": _serialize_datetime(event.created_at),
    }


def create_task(
    *,
    task_type: str,
    platform: str,
    payload: dict[str, Any],
    progress_total: int = 1,
    result_seed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_id = f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    task = TaskModel(
        id=task_id,
        type=task_type,
        platform=platform,
        status=TASK_STATUS_PENDING,
        payload_json=_dump_json(payload),
        result_json=_dump_json(_task_result_seed(result_seed)),
        progress_current=0,
        progress_total=max(int(progress_total or 0), 0),
    )
    with Session(engine) as session:
        session.add(task)
        session.commit()
        session.refresh(task)
    append_task_event(task.id, f"任务已创建: {task_type}", event_type="state")
    return serialize_task(task)


def create_register_task(payload: dict[str, Any]) -> dict[str, Any]:
    count = max(int(payload.get("count", 1) or 1), 1)
    return create_task(
        task_type=TASK_TYPE_REGISTER,
        platform=str(payload.get("platform", "")),
        payload=payload,
        progress_total=count,
    )


def create_account_check_task(account_id: int) -> dict[str, Any]:
    platform = ""
    with Session(engine) as session:
        model = session.get(AccountModel, account_id)
        if model:
            platform = model.platform
    return create_task(
        task_type=TASK_TYPE_ACCOUNT_CHECK,
        platform=platform,
        payload={"account_id": int(account_id)},
        progress_total=1,
    )


def create_account_check_all_task(platform: str = "", limit: int = 50) -> dict[str, Any]:
    return create_task(
        task_type=TASK_TYPE_ACCOUNT_CHECK_ALL,
        platform=platform,
        payload={"platform": platform, "limit": int(limit or 50)},
        progress_total=max(int(limit or 50), 1),
    )


def create_platform_action_task(payload: dict[str, Any]) -> dict[str, Any]:
    return create_task(
        task_type=TASK_TYPE_PLATFORM_ACTION,
        platform=str(payload.get("platform", "")),
        payload=payload,
        progress_total=1,
    )


def get_task(task_id: str) -> Optional[dict[str, Any]]:
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        return serialize_task(task) if task else None


def list_tasks(*, platform: str = "", status: str = "", page: int = 1, page_size: int = 50) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    with Session(engine) as session:
        q = select(TaskModel)
        total_q = select(func.count()).select_from(TaskModel)
        if platform:
            q = q.where(TaskModel.platform == platform)
            total_q = total_q.where(TaskModel.platform == platform)
        if status:
            q = q.where(TaskModel.status == status)
            total_q = total_q.where(TaskModel.status == status)
        q = q.order_by(TaskModel.created_at.desc())
        total = int(session.exec(total_q).one() or 0)
        items = session.exec(q.offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "page": page, "items": [serialize_task(item) for item in items]}


def list_task_events(task_id: str, *, since: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), 500)
    with Session(engine) as session:
        q = (
            select(TaskEventModel)
            .where(TaskEventModel.task_id == task_id)
            .where(TaskEventModel.id > since)
            .order_by(TaskEventModel.id)
            .limit(limit)
        )
        items = session.exec(q).all()
    return [serialize_event(item) for item in items]


def append_task_event(task_id: str, message: str, *, event_type: str = "log", level: str = "info", detail: dict | None = None) -> dict[str, Any]:
    with Session(engine) as session:
        event = TaskEventModel(
            task_id=task_id,
            type=event_type,
            level=level,
            message=message,
            detail_json=_dump_json(detail or {}),
        )
        session.add(event)
        session.commit()
        session.refresh(event)
    return serialize_event(event)


def mark_incomplete_tasks_interrupted() -> None:
    with Session(engine) as session:
        non_terminal = [TASK_STATUS_PENDING] + list(ACTIVE_TASK_STATUSES)
        tasks = session.exec(
            select(TaskModel).where(TaskModel.status.in_(non_terminal))
        ).all()
        for task in tasks:
            task.status = TASK_STATUS_INTERRUPTED
            task.error = task.error or "任务在服务重启后被中断"
            task.finished_at = _utcnow()
            task.updated_at = _utcnow()
            session.add(task)
        session.commit()
    for task in tasks:
        append_task_event(
            task.id,
            "任务在服务重启后被标记为中断",
            event_type="state",
            level="warning",
        )


def request_cancel(task_id: str) -> Optional[dict[str, Any]]:
    task = _mutate_task(
        task_id,
        lambda model: _request_cancel_mutation(model),
    )
    if not task:
        return None
    append_task_event(task_id, "已请求取消任务", event_type="state", level="warning")
    return serialize_task(task)


def _request_cancel_mutation(task: TaskModel) -> None:
    if task.status in TERMINAL_TASK_STATUSES:
        return
    if task.status == TASK_STATUS_PENDING:
        task.status = TASK_STATUS_CANCELLED
        task.finished_at = _utcnow()
        task.error = task.error or "任务在开始前被取消"
    else:
        task.status = TASK_STATUS_CANCEL_REQUESTED


def claim_next_runnable_task(
    *,
    running_platform_counts: dict[str, int] | None = None,
    busy_account_keys: set[str] | None = None,
    max_parallel_per_platform: int = 1,
) -> Optional[dict[str, Any]]:
    running_platform_counts = dict(running_platform_counts or {})
    busy_account_keys = set(busy_account_keys or set())
    with Session(engine) as session:
        tasks = session.exec(
            select(TaskModel)
            .where(TaskModel.status == TASK_STATUS_PENDING)
            .order_by(TaskModel.created_at)
        ).all()
        for task in tasks:
            payload = task.get_payload()
            platform = task.platform or str(payload.get("platform", "") or "")
            account_keys = _task_account_keys(task.type, payload)
            if platform and running_platform_counts.get(platform, 0) >= max_parallel_per_platform:
                continue
            if account_keys and busy_account_keys.intersection(account_keys):
                continue
            task.status = TASK_STATUS_CLAIMED
            task.started_at = task.started_at or _utcnow()
            task.updated_at = _utcnow()
            session.add(task)
            session.commit()
            return {"id": task.id, "platform": platform, "account_keys": account_keys}
    return None


class TaskLogger:
    def __init__(self, task_id: str):
        self.task_id = task_id

    def log(self, message: str, *, level: str = "info", event_type: str = "log", detail: dict | None = None) -> None:
        append_task_event(
            self.task_id,
            message,
            event_type=event_type,
            level=level,
            detail=detail,
        )
        print(f"[task:{self.task_id}] {message}")

    def mark_running(self) -> None:
        def _update(task: TaskModel) -> None:
            task.status = TASK_STATUS_RUNNING
            task.started_at = task.started_at or _utcnow()

        _mutate_task(self.task_id, _update)
        self.log("任务已开始执行", event_type="state")

    def is_cancel_requested(self) -> bool:
        with Session(engine) as session:
            task = session.get(TaskModel, self.task_id)
            return bool(task and task.status == TASK_STATUS_CANCEL_REQUESTED)

    def set_progress(self, current: int, total: Optional[int] = None) -> None:
        current = max(int(current), 0)

        def _update(task: TaskModel) -> None:
            task.progress_current = current
            if total is not None:
                task.progress_total = max(int(total), 0)

        _mutate_task(self.task_id, _update)

    def record_success(self) -> None:
        def _update(task: TaskModel) -> None:
            task.success_count += 1

        _mutate_task(self.task_id, _update)

    def record_error(self, error: str) -> None:
        def _update(task: TaskModel) -> None:
            task.error_count += 1
            result = task.get_result()
            errors = list(result.get("errors", []))
            errors.append(error)
            result["errors"] = errors
            task.set_result(result)

        _mutate_task(self.task_id, _update)

    def add_cashier_url(self, url: str) -> None:
        def _update(task: TaskModel) -> None:
            result = task.get_result()
            urls = list(result.get("cashier_urls", []))
            urls.append(url)
            result["cashier_urls"] = urls
            task.set_result(result)

        _mutate_task(self.task_id, _update)

    def set_result_data(self, data: Any) -> None:
        def _update(task: TaskModel) -> None:
            result = task.get_result()
            result["data"] = data
            task.set_result(result)

        _mutate_task(self.task_id, _update)

    def finish(self, status: str, *, error: str = "") -> None:
        def _update(task: TaskModel) -> None:
            task.status = status
            task.finished_at = _utcnow()
            if error:
                task.error = error

        _mutate_task(self.task_id, _update)
        event_level = "error" if status == TASK_STATUS_FAILED else ("warning" if status in {TASK_STATUS_INTERRUPTED, TASK_STATUS_CANCELLED} else "info")
        self.log(
            f"任务结束: {status}",
            level=event_level,
            event_type="state",
            detail={"status": status, "error": error},
        )


def _auto_push_any2api(task_logger: TaskLogger, account) -> None:
    """注册成功后自动推送账号到 Any2API（如果已配置）。"""
    try:
        from core.any2api_sync import push_account_to_any2api
        push_account_to_any2api(account, log_fn=task_logger.log)
    except Exception as exc:
        task_logger.log(f"  [Any2API] 自动推送异常: {exc}", level="warning")


def _auto_sync_lingya2api(task_logger: TaskLogger, account) -> None:
    if getattr(account, "platform", "") != "lingya_qq":
        return
    try:
        from core.lingya2api_sync import sync_account_to_lingya2api

        result = sync_account_to_lingya2api(account, log_fn=task_logger.log)
        if result:
            task_logger.log("  [Lingya2API] LingYaQQ account synced")
        else:
            task_logger.log("  [Lingya2API] auto sync skipped or failed; check lingya2api_url/API key and previous warning logs", level="warning")
    except Exception as exc:
        task_logger.log(f"  [Lingya2API] auto sync error: {exc}", level="warning")


def _existing_account_id(platform: str, email: str) -> int | None:
    if not platform or not email:
        return None
    try:
        with Session(engine) as session:
            model = session.exec(
                select(AccountModel)
                .where(AccountModel.platform == platform)
                .where(AccountModel.email == email)
            ).first()
            return int(model.id or 0) if model and model.id else None
    except Exception:
        return None


def _task_config_value(extra: dict[str, Any], key: str, default: Any = "") -> Any:
    if extra.get(key) not in (None, ""):
        return extra.get(key)
    try:
        from core.config_store import config_store

        return config_store.get(key, default)
    except Exception:
        return default


def _merge_lingya_followup_data(account, data: dict[str, Any]) -> None:
    extra = dict(getattr(account, "extra", {}) or {})
    overview = dict(extra.get("account_overview") or {})
    compact_keys = {
        "daily_sign_in_status",
        "daily_sign_in_at",
        "daily_sign_signed",
        "daily_sign_already_signed",
        "last_publish_vid",
        "last_publish_title",
        "last_publish_status",
        "last_publish_work_status",
        "last_publish_at",
        "last_publish_review_result",
        "publish_skipped",
        "publish_skip_reason",
        "quota_balance",
        "quota_sum",
    }
    for key in compact_keys:
        if data.get(key) not in (None, ""):
            extra[key] = data.get(key)
            overview[key] = data.get(key)
    publish_config_keys = {
        "lingya_qq_publish_source_url",
        "lingya_qq_publish_source_timeout",
        "lingya_qq_publish_source_retries",
        "lingya_qq_publish_cover_url",
        "lingya_qq_publish_prompt",
        "lingya_qq_publish_creation_process_text",
        "lingya_qq_publish_initial_delay",
        "lingya_qq_publish_poll_interval",
        "lingya_qq_publish_timeout",
        "lingya_qq_publish_generation_timeout",
        "lingya_qq_publish_generation_poll_interval",
        "lingya_qq_video_upload_service_id",
    }
    publish_config = {
        key: data.get(key)
        for key in publish_config_keys
        if data.get(key) not in (None, "")
    }
    if publish_config:
        legacy_extra = dict(overview.get("legacy_extra") or {})
        legacy_extra.update(publish_config)
        overview["legacy_extra"] = legacy_extra
        extra.update(publish_config)
    quota = {
        "quota_balance": data.get("quota_balance"),
        "quota_sum": data.get("quota_sum"),
    }
    if quota.get("quota_balance") not in (None, "") or quota.get("quota_sum") not in (None, ""):
        extra["quota"] = quota
    chips = list(overview.get("chips") or [])
    if data.get("daily_sign_in_status"):
        chips.append(f"签到 {_lingya_status_label(data.get('daily_sign_in_status'))}")
    if data.get("last_publish_status"):
        chips.append(f"发布 {_lingya_status_label(data.get('last_publish_status'))}")
    if quota.get("quota_balance") not in (None, "") or quota.get("quota_sum") not in (None, ""):
        chips.append(f"额度 {quota.get('quota_balance', '-')}/{quota.get('quota_sum', '-')}")
    if chips:
        seen: set[str] = set()
        overview["chips"] = [chip for chip in chips if chip and not (chip in seen or seen.add(chip))]
    extra["account_overview"] = overview
    account.extra = extra


def _auto_followup_lingya_qq_rewards(
    *,
    platform_name: str,
    payload: dict[str, Any],
    platform,
    account,
    logger: "TaskLogger",
) -> None:
    if platform_name != "lingya_qq" or getattr(account, "platform", "") != "lingya_qq":
        return

    def _target() -> None:
        try:
            _run_auto_followup_lingya_qq_rewards(
                platform_name=platform_name,
                payload=payload,
                platform=platform,
                account=account,
                logger=logger,
            )
        except Exception as exc:
            logger.log(f"  [LingYaQQ] async follow-up error: {exc}", level="warning")

    threading.Thread(
        target=_target,
        daemon=True,
        name=f"lingya-followup-{str(getattr(account, 'email', '') or 'account')[:24]}",
    ).start()
    logger.log("  [LingYaQQ] post-register follow-up queued in background")


def _run_auto_followup_lingya_qq_rewards(
    *,
    platform_name: str,
    payload: dict[str, Any],
    platform,
    account,
    logger: "TaskLogger",
) -> None:
    if platform_name != "lingya_qq" or getattr(account, "platform", "") != "lingya_qq":
        return
    if not hasattr(platform, "execute_action"):
        return
    extra_cfg = dict(payload.get("extra") or {})
    publish_source_url = str(_task_config_value(extra_cfg, "lingya_qq_publish_source_url", "") or "").strip()
    publish_required = _bool_config(_task_config_value(extra_cfg, "lingya_qq_publish_required", False), False)
    daily_sign_enabled = _bool_config(_task_config_value(extra_cfg, "lingya_qq_daily_sign_in_enabled", True), True)

    if not daily_sign_enabled:
        logger.log("  [LingYaQQ] 签到功能已关闭")
    elif _bool_config(_task_config_value(extra_cfg, "lingya_qq_auto_daily_sign_in", True), True):
        try:
            logger.log("  [LingYaQQ] 执行每日签到")
            result = platform.execute_action("daily_sign_in", account, {"_cancel_check": logger.is_cancel_requested})
            if result.get("ok"):
                _merge_lingya_followup_data(account, dict(result.get("data") or {}))
                save_account(account)
            else:
                logger.log(f"  [LingYaQQ] 签到跳过或失败: {result.get('error')}", level="warning")
        except Exception as exc:
            logger.log(f"  [LingYaQQ] 签到异常: {exc}", level="warning")

    try:
        should_publish = _bool_config(
            _task_config_value(extra_cfg, "lingya_qq_auto_publish_after_register", bool(publish_source_url)),
            bool(publish_source_url),
        )
        if not should_publish:
            return
        if not publish_source_url:
            message = "LingYaQQ 发布内容接口未配置"
            if publish_required:
                raise RuntimeError(message)
            logger.log(f"  [LingYaQQ] 发布已跳过: {message}", level="warning")
            return

        logger.log("  [LingYaQQ] 登录/注册后发布一个作品")
        result = platform.execute_action(
            "publish_work",
            account,
            {
                "source_url": publish_source_url,
                "initial_delay": _task_config_value(extra_cfg, "lingya_qq_publish_initial_delay", 600),
                "poll_interval": _task_config_value(extra_cfg, "lingya_qq_publish_poll_interval", 60),
                "timeout": _task_config_value(extra_cfg, "lingya_qq_publish_timeout", 7200),
                "generation_timeout": _task_config_value(extra_cfg, "lingya_qq_publish_generation_timeout", 600),
                "generation_poll_interval": _task_config_value(extra_cfg, "lingya_qq_publish_generation_poll_interval", 5),
                "_cancel_check": logger.is_cancel_requested,
            },
        )
        if result.get("ok"):
            _merge_lingya_followup_data(account, dict(result.get("data") or {}))
            save_account(account)
            logger.log("  [LingYaQQ] 发布完成并已刷新额度")
            logger.log("  [Lingya2API] 发布完成后再次同步 LingYaQQ 账号")
            _auto_sync_lingya2api(logger, account)
        else:
            message = str(result.get("error") or "LingYaQQ 发布失败")
            if publish_required:
                raise RuntimeError(message)
            logger.log(f"  [LingYaQQ] 发布失败: {message}", level="warning")
    except Exception as exc:
        if publish_required:
            raise
        logger.log(f"  [LingYaQQ] 后续自动化异常: {exc}", level="warning")


def _auto_upload_cpa(task_logger: TaskLogger, account) -> None:
    if getattr(account, "platform", "") != "chatgpt":
        return
    try:
        from core.config_store import config_store

        cpa_url = config_store.get("cpa_api_url", "")
        if cpa_url:
            from platforms.chatgpt.cpa_upload import generate_token_json, upload_to_cpa

            class _AccountProxy:
                pass

            target = _AccountProxy()
            target.email = account.email
            extra = account.extra or {}
            target.access_token = extra.get("access_token") or account.token
            target.refresh_token = extra.get("refresh_token", "")
            target.id_token = extra.get("id_token", "")
            target.session_token = extra.get("session_token", "")
            target.user_id = account.user_id or ""
            target.account_id = account.user_id or ""
            target.cookies = extra.get("cookies", "")

            token_data = generate_token_json(target)
            ok, msg = upload_to_cpa(token_data)
            task_logger.log(f"  [CPA] {'✓ ' + msg if ok else '✗ ' + msg}")
    except Exception as exc:
        task_logger.log(f"  [CPA] 自动上传异常: {exc}", level="warning")


def _build_platform_instance(platform_name: str, payload: dict[str, Any], logger: TaskLogger, resolved_proxy: str | None = None, shared_mailbox=None):
    from core.base_identity import normalize_identity_provider
    from core.base_mailbox import create_mailbox

    executor_type = str(payload.get("executor_type", "protocol") or "protocol")
    captcha_solver = str(payload.get("captcha_solver", "auto") or "auto")
    extra = dict(payload.get("extra") or {})
    extra.setdefault("_task_id", logger.task_id)
    config = RegisterConfig(
        executor_type=executor_type,
        captcha_solver=captcha_solver,
        proxy=resolved_proxy,
        extra=extra,
    )
    identity_provider = normalize_identity_provider(extra.get("identity_provider", "mailbox"))
    mailbox = shared_mailbox
    if mailbox is None and identity_provider == "mailbox":
        if not extra.get("mail_provider"):
            from infrastructure.provider_settings_repository import ProviderSettingsRepository

            extra["mail_provider"] = ProviderSettingsRepository().get_default_provider_key("mailbox")
        mailbox = create_mailbox(
            provider=extra.get("mail_provider", ""),
            extra=extra,
            proxy=resolved_proxy,
        )

    platform_cls = get(platform_name)
    platform = platform_cls(config=config, mailbox=mailbox)
    if hasattr(platform, "set_logger"):
        platform.set_logger(logger.log)
    else:
        platform._log_fn = logger.log
    return platform


def _run_single_account_check(account_id: int, logger: TaskLogger | None = None) -> tuple[bool, dict[str, Any]]:
    with Session(engine) as session:
        model = session.get(AccountModel, account_id)
        if not model:
            raise ValueError("账号不存在")
        plugin = get(model.platform)(config=RegisterConfig())
        account = build_platform_account(session, model)

    valid = plugin.check_valid(account)
    with Session(engine) as session:
        model = session.get(AccountModel, account_id)
        if model:
            model.updated_at = _utcnow()
            current_graph = load_account_graphs(session, [account_id]).get(account_id, {})
            summary_updates = {"checked_at": _utcnow_iso(), "valid": bool(valid)}
            if hasattr(plugin, "get_last_check_overview"):
                summary_updates.update(plugin.get_last_check_overview() or {})
            lifecycle_status = None
            if valid:
                lifecycle_status = recover_lifecycle_status_for_valid_account(current_graph)
            patch_account_graph(
                session,
                model,
                lifecycle_status=lifecycle_status,
                summary_updates=summary_updates,
            )
            session.add(model)
            session.commit()

    result = {"account_id": account_id, "valid": bool(valid), "platform": account.platform, "email": account.email}
    if logger:
        logger.log(f"{account.email}: {'有效' if valid else '失效'}")
    return valid, result


def execute_task(task_id: str) -> None:
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            return
        task_type = task.type
        payload = task.get_payload()

    logger = TaskLogger(task_id)
    logger.mark_running()

    if logger.is_cancel_requested():
        logger.finish(TASK_STATUS_CANCELLED, error="任务在启动后立即被取消")
        return

    handlers: dict[str, Callable[[dict[str, Any], TaskLogger], None]] = {
        TASK_TYPE_REGISTER: _execute_register_task,
        TASK_TYPE_ACCOUNT_CHECK: _execute_account_check_task,
        TASK_TYPE_ACCOUNT_CHECK_ALL: _execute_account_check_all_task,
        TASK_TYPE_PLATFORM_ACTION: _execute_platform_action_task,
    }
    handler = handlers.get(task_type)
    if not handler:
        logger.finish(TASK_STATUS_FAILED, error=f"未知任务类型: {task_type}")
        return
    handler(payload, logger)


def _resolve_sms_provider_for_task(extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from infrastructure.provider_definitions_repository import ProviderDefinitionsRepository
    from infrastructure.provider_settings_repository import ProviderSettingsRepository

    settings_repo = ProviderSettingsRepository()
    definitions_repo = ProviderDefinitionsRepository()
    provider_key = str(
        extra.get("sms_provider")
        or extra.get("phone_provider")
        or settings_repo.get_default_provider_key("sms")
        or ""
    ).strip()
    if not provider_key:
        provider_key = "sms_activate" if extra.get("sms_activate_api_key") else ""
    definition = definitions_repo.get_by_key("sms", provider_key) if provider_key else None
    settings = settings_repo.resolve_runtime_settings("sms", provider_key, extra) if definition else dict(extra)
    return provider_key, settings


def _bool_config(value: Any, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "否"}


def _int_config(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _auto_followup_windsurf_payment(
    *,
    platform_name: str,
    payload: dict[str, Any],
    platform,
    account,
    logger: "TaskLogger",
) -> None:
    if platform_name != "windsurf":
        return
    executor_type = str(payload.get("executor_type", "") or "").strip()
    use_browser = executor_type in {"headless", "headed"}
    if not use_browser:
        extra_cfg = dict(payload.get("extra") or {})
        if not _bool_config(extra_cfg.get("auto_payment_link"), True):
            return
    if not str(getattr(account, "password", "") or "").strip() and use_browser:
        logger.log("Windsurf 注册后自动升级已跳过: 账号缺少密码", level="error")
        return
    extra = dict(payload.get("extra") or {})
    turnstile_token = str(extra.get("turnstile_token") or "").strip()
    if use_browser:
        action_id = "payment_link_browser"
        params = {
            "timeout": _int_config(extra.get("windsurf_payment_timeout"), 240),
            "headless": "true" if _bool_config(extra.get("windsurf_payment_headless"), False) else "false",
            "payment_channel": "checkout",
        }
        if turnstile_token:
            params["turnstile_token"] = turnstile_token
    else:
        action_id = "payment_link"
        params = {}
        if turnstile_token:
            params["turnstile_token"] = turnstile_token
    logger.log("注册成功，开始自动生成 Windsurf Pro Trial Stripe 链接")
    try:
        result = platform.execute_action(action_id, account, params)
    except Exception as exc:
        message = f"Windsurf 注册后自动升级失败: {exc}"
        logger.record_error(message)
        logger.log(message, level="error")
        return
    if not result.get("ok"):
        message = f"Windsurf 注册后自动升级失败: {result.get('error') or 'unknown error'}"
        logger.record_error(message)
        logger.log(message, level="error")
        return
    data = dict(result.get("data") or {})
    if data:
        merged_extra = dict(getattr(account, "extra", {}) or {})
        merged_extra.update(data)
        account.extra = merged_extra
        save_account(account)
    cashier_url = str(data.get("cashier_url") or data.get("url") or "").strip()
    if cashier_url:
        logger.log(f"Windsurf 自动升级链接已生成: {cashier_url}")
        logger.add_cashier_url(cashier_url)


def _execute_register_task(payload: dict[str, Any], logger: TaskLogger) -> None:
    from core.proxy_pool import proxy_pool

    count = max(int(payload.get("count", 1) or 1), 1)
    concurrency = min(max(int(payload.get("concurrency", 1) or 1), 1), count, 5)
    platform_name = str(payload.get("platform", ""))
    email = payload.get("email") or None
    password = payload.get("password") or None
    extra = dict(payload.get("extra") or {})
    proxy = str(payload.get("proxy") or "").strip() or None
    use_proxy_pool = _bool_config(
        payload.get("use_proxy_pool")
        or extra.get("use_proxy_pool")
        or extra.get("use_proxy_mode"),
        False,
    )
    sms_provider_key, sms_settings = _resolve_sms_provider_for_task(extra)
    herosms_enabled = sms_provider_key == "herosms" and bool(str(sms_settings.get("herosms_api_key") or "").strip())
    hero_extra_max = max(_int_config(sms_settings.get("register_phone_extra_max"), 3), 0) if herosms_enabled else 0
    hero_reuse_to_max = _bool_config(sms_settings.get("register_reuse_phone_to_max"), True) if herosms_enabled else False
    target_success = count
    max_success = count + hero_extra_max if herosms_enabled and hero_reuse_to_max else count
    progress_total = max_success if herosms_enabled else count

    logger.set_progress(0, progress_total)
    if herosms_enabled:
        logger.log(
            f"HeroSMS 模式: 成功目标 {target_success}，失败自动补尝试，"
            f"号码仍可复用时最多额外成功 {hero_extra_max} 个"
        )

    try:
        get(platform_name)
    except Exception as exc:
        logger.log(f"致命错误: {exc}", level="error")
        logger.finish(TASK_STATUS_FAILED, error=str(exc))
        return

    success = 0
    errors: list[str] = []

    # Pre-create a shared mailbox instance for the entire task to avoid
    # concurrent initialization issues (e.g. MoeMail auto-registering
    # multiple provider accounts simultaneously).
    shared_mailbox = None
    try:
        from core.base_identity import normalize_identity_provider
        from core.base_mailbox import create_mailbox

        identity_provider = normalize_identity_provider(extra.get("identity_provider", "mailbox"))
        if identity_provider == "mailbox":
            if not extra.get("mail_provider"):
                from infrastructure.provider_settings_repository import ProviderSettingsRepository
                extra["mail_provider"] = ProviderSettingsRepository().get_default_provider_key("mailbox")
            shared_mailbox = create_mailbox(
                provider=extra.get("mail_provider", ""),
                extra=extra,
                proxy=proxy or None,
            )
    except Exception as exc:
        logger.log(f"邮箱初始化失败: {exc}", level="error")
        logger.finish(TASK_STATUS_FAILED, error=f"邮箱初始化失败: {exc}")
        return

    def _do_one(index: int) -> bool | str:
        if logger.is_cancel_requested():
            return "__cancel_requested__"
        resolved_proxy = proxy or (proxy_pool.get_next() if use_proxy_pool else None)
        platform = _build_platform_instance(platform_name, payload, logger, resolved_proxy=resolved_proxy, shared_mailbox=shared_mailbox)
        try:
            logger.log(f"开始注册第 {index + 1}/{count} 个账号")
            if resolved_proxy:
                logger.log(f"使用代理: {resolved_proxy}")
            account = platform.register(email=email, password=password)
            if resolved_proxy:
                account_extra = dict(account.extra or {})
                account_extra["proxy_url"] = resolved_proxy
                account.extra = account_extra
            existing_account_id = _existing_account_id(account.platform, account.email)
            save_account(account)
            saved_account_id = existing_account_id or _existing_account_id(account.platform, account.email) or 0
            if saved_account_id:
                save_mode = "updated existing" if existing_account_id else "created"
                logger.log(f"  [Accounts] saved account id={saved_account_id} ({save_mode})")
            _auto_sync_lingya2api(logger, account)
            _auto_followup_windsurf_payment(
                platform_name=platform_name,
                payload=payload,
                platform=platform,
                account=account,
                logger=logger,
            )
            if resolved_proxy:
                proxy_pool.report_success(resolved_proxy)
            logger.record_success()
            logger.log(f"✓ 注册成功: {account.email}")
            _save_task_log(platform_name, account.email, "success")
            _auto_followup_lingya_qq_rewards(
                platform_name=platform_name,
                payload=payload,
                platform=platform,
                account=account,
                logger=logger,
            )
            _auto_upload_cpa(logger, account)
            _auto_push_any2api(logger, account)
            extra = dict(account.extra or {})
            overview = dict(extra.get("account_overview") or {})
            cashier_url = str(extra.get("cashier_url") or overview.get("cashier_url") or "")
            if cashier_url:
                logger.log(f"  [升级链接] {cashier_url}")
                logger.add_cashier_url(cashier_url)
            return True
        except Exception as exc:
            if resolved_proxy:
                proxy_pool.report_fail(resolved_proxy)
            error = str(exc)
            logger.record_error(error)
            logger.log(f"✗ 注册失败: {error}", level="error")
            _save_task_log(platform_name, email or "", "failed", error=error)
            return error

    try:
        submitted = 0
        completed = 0
        futures: dict[Any, int] = {}
        max_attempts = max(count if not herosms_enabled else max_success * 3, 1)

        def _hero_phone_alive() -> bool:
            if not (herosms_enabled and hero_reuse_to_max):
                return False
            try:
                from core.base_sms import is_herosms_phone_cache_alive
                alive, info = is_herosms_phone_cache_alive(sms_settings)
                if alive:
                    logger.log(
                        "HeroSMS 号码仍可复用: "
                        f"{str(info.get('phone_number') or '')[:5]}**** "
                        f"剩余 {int(info.get('remaining_seconds') or 0)} 秒，"
                        f"已成功 {int(info.get('use_count') or 0)} 次"
                    )
                return bool(alive)
            except Exception:
                return False

        def _should_submit_more() -> bool:
            if submitted >= max_attempts or logger.is_cancel_requested():
                return False
            if not herosms_enabled:
                return submitted < count
            if success + len(futures) >= max_success:
                return False
            if success < target_success:
                return True
            if success >= max_success:
                return False
            return _hero_phone_alive()

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            while _should_submit_more() and len(futures) < concurrency:
                futures[pool.submit(_do_one, submitted)] = submitted
                submitted += 1

            while futures:
                done, _ = wait(set(futures.keys()), return_when=FIRST_COMPLETED)
                for future in done:
                    futures.pop(future, None)
                    result = future.result()
                    completed += 1
                    if result is True:
                        success += 1
                    elif result != "__cancel_requested__":
                        errors.append(str(result))
                    logger.set_progress(min(success if herosms_enabled else completed, progress_total), progress_total)
                while _should_submit_more() and len(futures) < concurrency:
                    futures[pool.submit(_do_one, submitted)] = submitted
                    submitted += 1
                if logger.is_cancel_requested() and not futures:
                    break
    except Exception as exc:
        logger.log(f"致命错误: {exc}", level="error")
        logger.finish(TASK_STATUS_FAILED, error=str(exc))
        return

    if herosms_enabled:
        logger.set_result_data({
            "target_count": target_success,
            "attempts": submitted,
            "success": success,
            "fail": len(errors),
            "extra_success": max(0, success - target_success),
            "hero_sms_reuse": True,
        })
    summary = f"完成: 成功 {success} 个, 失败 {len(errors)} 个"
    logger.log(summary, event_type="summary")
    if logger.is_cancel_requested():
        logger.finish(TASK_STATUS_CANCELLED, error="任务已取消")
        return
    final_status = TASK_STATUS_FAILED if errors and success == 0 else TASK_STATUS_SUCCEEDED
    final_error = "" if final_status == TASK_STATUS_SUCCEEDED else errors[0]
    logger.finish(final_status, error=final_error)


def _execute_platform_action_task(payload: dict[str, Any], logger: TaskLogger) -> None:
    command_platform = str(payload.get("platform", ""))
    account_id = int(payload.get("account_id", 0) or 0)
    action_id = str(payload.get("action_id", ""))
    params = dict(payload.get("params") or {})
    runtime = PlatformRuntime()
    result = runtime.execute_action(
        type("Command", (), {
            "platform": command_platform,
            "account_id": account_id,
            "action_id": action_id,
            "params": params,
        })(),
        log_fn=logger.log,
    )
    if not result.ok:
        logger.record_error(result.error)
        logger.finish(TASK_STATUS_FAILED, error=result.error)
        return
    logger.set_result_data(result.data)
    message = ""
    if isinstance(result.data, dict):
        message = str(result.data.get("message", "") or "")
    if message:
        logger.log(message, event_type="summary")
    logger.set_progress(1, 1)
    logger.finish(TASK_STATUS_SUCCEEDED)


def _execute_account_check_task(payload: dict[str, Any], logger: TaskLogger) -> None:
    account_id = int(payload.get("account_id", 0) or 0)
    if account_id <= 0:
        logger.finish(TASK_STATUS_FAILED, error="缺少 account_id")
        return
    try:
        _, result = _run_single_account_check(account_id, logger)
        logger.set_result_data(result)
        logger.set_progress(1, 1)
        logger.finish(TASK_STATUS_SUCCEEDED)
    except Exception as exc:
        logger.record_error(str(exc))
        logger.finish(TASK_STATUS_FAILED, error=str(exc))


def _execute_account_check_all_task(payload: dict[str, Any], logger: TaskLogger) -> None:
    platform = str(payload.get("platform", "") or "")
    limit = max(int(payload.get("limit", 50) or 50), 1)

    with Session(engine) as session:
        q = select(AccountModel)
        if platform:
            q = q.where(AccountModel.platform == platform)
        q = q.order_by(AccountModel.created_at.desc(), AccountModel.id.desc())
        accounts = session.exec(q.limit(limit)).all()
        graphs = load_account_graphs(session, [int(item.id or 0) for item in accounts if item.id])

    targets = [
        model for model in accounts
        if _is_active_account_check_target(graphs.get(int(model.id or 0), {}), platform=model.platform)
    ]
    skipped = len(accounts) - len(targets)
    total = len(targets)
    logger.set_progress(0, total)
    if total == 0:
        logger.set_result_data({"valid": 0, "invalid": 0, "error": 0, "skipped": skipped})
        logger.finish(TASK_STATUS_SUCCEEDED)
        return

    results = {"valid": 0, "invalid": 0, "error": 0, "skipped": skipped}
    completed = 0
    for model in targets:
        if logger.is_cancel_requested():
            logger.finish(TASK_STATUS_CANCELLED, error="任务已取消")
            return
        try:
            valid, _ = _run_single_account_check(int(model.id or 0), logger)
            if valid:
                results["valid"] += 1
            else:
                results["invalid"] += 1
        except Exception as exc:
            results["error"] += 1
            logger.record_error(str(exc))
            logger.log(f"{model.email}: 检测异常 {exc}", level="error")
        completed += 1
        logger.set_progress(completed, total)
    logger.set_result_data(results)
    logger.finish(TASK_STATUS_SUCCEEDED)


def _is_active_account_check_target(graph: dict[str, Any], *, platform: str = "") -> bool:
    overview = graph.get("overview") or {}
    lifecycle = str(graph.get("lifecycle_status") or overview.get("lifecycle_status") or "registered")
    if platform == "lingya_qq":
        return lifecycle != "expired"
    if lifecycle not in ACTIVE_ACCOUNT_CHECK_LIFECYCLE_STATUSES:
        return False
    validity = str(graph.get("validity_status") or overview.get("validity_status") or "").lower()
    if validity == "invalid" or overview.get("valid") is False:
        return False
    return True
