from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit


LINGYA_ASSIST_KIND = "lingya_phone_login"
LINGYA_ASSIST_PAGE_URL = "https://lingya.qq.com/"
ACTIVE_ASSIST_STATUSES = {"pending", "claimed", "opened", "visible", "filled"}
RESUMABLE_ASSIST_STATUSES = {"claimed"}
TERMINAL_ASSIST_STATUSES = {"failed", "expired", "cancelled"}


def normalize_proxy_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except Exception:
        return text.rstrip("/")
    if not parsed.scheme or not parsed.netloc:
        return text.rstrip("/")

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return text.rstrip("/")
    username = parsed.username or ""
    password = parsed.password or ""
    default_port = {"http": 80, "https": 443, "socks5": 1080, "socks4": 1080}.get(scheme)

    userinfo = ""
    if username:
        userinfo = username
        if password:
            userinfo += f":{password}"
        userinfo += "@"
    netloc = f"{userinfo}{hostname}"
    if port and port != default_port:
        netloc += f":{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parsed.query, "")).rstrip("/")


@dataclass(slots=True)
class BrowserAssistRequest:
    assist_id: str
    task_id: str
    platform: str
    kind: str
    phone: str
    local_phone: str
    area_code: str
    proxy_url: str = ""
    page_url: str = LINGYA_ASSIST_PAGE_URL
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 360)
    status: str = "pending"
    claimed_by: str = ""
    claimed_at: float = 0.0
    last_state: str = ""
    last_error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: float | None = None) -> bool:
        return float(now or time.time()) >= float(self.expires_at or 0)

    def public_payload(self) -> dict[str, Any]:
        return {
            "assist_id": self.assist_id,
            "task_id": self.task_id,
            "platform": self.platform,
            "kind": self.kind,
            "phone": self.phone,
            "local_phone": self.local_phone,
            "area_code": self.area_code,
            "proxy_url": self.proxy_url,
            "page_url": self.page_url,
            "status": self.status,
            "expires_at": int(self.expires_at),
            "metadata": dict(self.metadata or {}),
        }


class BrowserAssistRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, BrowserAssistRequest] = {}

    def publish_lingya_phone_login(
        self,
        *,
        task_id: str,
        phone: str,
        local_phone: str,
        area_code: str,
        proxy_url: str = "",
        ttl_seconds: int = 360,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ttl = max(int(ttl_seconds or 360), 30)
        request = BrowserAssistRequest(
            assist_id=f"assist_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
            task_id=str(task_id or "").strip(),
            platform="lingya_qq",
            kind=LINGYA_ASSIST_KIND,
            phone=str(phone or "").strip(),
            local_phone=str(local_phone or "").strip(),
            area_code=str(area_code or "").strip(),
            proxy_url=normalize_proxy_url(proxy_url),
            page_url=LINGYA_ASSIST_PAGE_URL,
            expires_at=time.time() + ttl,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._cleanup_expired_locked()
            self._requests[request.assist_id] = request
        return request.public_payload()

    def claim(
        self,
        *,
        platform: str,
        proxy_url: str = "",
        extension_id: str = "",
        current_url: str = "",
    ) -> dict[str, Any] | None:
        del current_url
        normalized_proxy = normalize_proxy_url(proxy_url)
        extension = str(extension_id or "").strip() or "anonymous"
        with self._lock:
            self._cleanup_expired_locked()
            existing = self._active_for_extension_locked(extension, platform)
            if existing:
                return existing.public_payload()

            for request in sorted(self._requests.values(), key=lambda item: item.created_at):
                if request.platform != platform:
                    continue
                if request.status != "pending":
                    continue
                if normalize_proxy_url(request.proxy_url) != normalized_proxy:
                    continue
                request.status = "claimed"
                request.claimed_by = extension
                request.claimed_at = time.time()
                request.last_state = "claimed"
                return request.public_payload()
        return None

    def update_state(
        self,
        assist_id: str,
        *,
        extension_id: str = "",
        state: str,
        error: str = "",
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        state = str(state or "").strip().lower()
        extension = str(extension_id or "").strip()
        with self._lock:
            self._cleanup_expired_locked()
            request = self._requests.get(str(assist_id or "").strip())
            if not request:
                return None
            if request.claimed_by and extension and request.claimed_by != extension:
                return None
            old_state = request.last_state or request.status
            if state in ACTIVE_ASSIST_STATUSES or state in TERMINAL_ASSIST_STATUSES:
                request.status = state
                request.last_state = state
            if error:
                request.last_error = str(error)
            payload = request.public_payload()
            payload["error"] = request.last_error

        if state and state != old_state:
            self._append_task_event(request, state=state, error=error, detail=detail or {})
        return payload

    def clear_for_tests(self) -> None:
        with self._lock:
            self._requests.clear()

    def _active_for_extension_locked(self, extension_id: str, platform: str) -> BrowserAssistRequest | None:
        for request in sorted(self._requests.values(), key=lambda item: item.created_at):
            if request.platform != platform:
                continue
            if request.claimed_by != extension_id:
                continue
            if request.status not in RESUMABLE_ASSIST_STATUSES:
                continue
            return request
        return None

    def _cleanup_expired_locked(self) -> None:
        now = time.time()
        stale_ids: list[str] = []
        for request in self._requests.values():
            if request.status not in TERMINAL_ASSIST_STATUSES and request.is_expired(now):
                request.status = "expired"
                request.last_state = "expired"
            if request.status in TERMINAL_ASSIST_STATUSES and now - float(request.expires_at or now) > 600:
                stale_ids.append(request.assist_id)
        for assist_id in stale_ids:
            self._requests.pop(assist_id, None)

    def _append_task_event(
        self,
        request: BrowserAssistRequest,
        *,
        state: str,
        error: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        if not request.task_id:
            return
        try:
            from application.tasks import append_task_event

            message = _state_message(state, request, error=error)
            level = "error" if state == "failed" else "info"
            append_task_event(
                request.task_id,
                message,
                event_type="browser_assist",
                level=level,
                detail={
                    "assist_id": request.assist_id,
                    "state": state,
                    "proxy_url": request.proxy_url,
                    "phone": request.phone,
                    **dict(detail or {}),
                },
            )
        except Exception:
            return


def _state_message(state: str, request: BrowserAssistRequest, *, error: str = "") -> str:
    phone = request.phone or request.local_phone or "-"
    if state == "claimed":
        return f"LingYaQQ 浏览器助手已领取手机号任务: {phone}"
    if state == "opened":
        return "LingYaQQ 浏览器助手已打开页面"
    if state == "visible":
        return "LingYaQQ 浏览器助手提示面板已显示"
    if state == "filled":
        return f"LingYaQQ 浏览器助手已自动填入手机号: {phone}"
    if state == "failed":
        return f"LingYaQQ 浏览器助手执行失败: {error or '-'}"
    if state == "expired":
        return "LingYaQQ 浏览器助手任务已过期"
    return f"LingYaQQ 浏览器助手状态: {state}"


browser_assist_registry = BrowserAssistRegistry()
