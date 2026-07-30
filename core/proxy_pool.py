"""代理池 - 从数据库读取代理，支持轮询和按区域选取"""
from typing import Iterable, Optional
from sqlmodel import Session, select
from .db import ProxyModel, engine
import time, threading, random
from datetime import datetime, timezone


class ProxyPool:
    def __init__(self):
        self._index = 0
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._leases: dict[str, int] = {}
        self._last_assigned: dict[str, float] = {}

    def get_next(self, region: str = "") -> Optional[str]:
        """获取下一个可用代理。

        优先级:
          1. 动态代理 provider（如果已配置且启用）
          2. 静态代理池（数据库中的固定代理列表）
        """
        # 1. 尝试动态代理
        try:
            from core.proxy_providers import get_dynamic_proxy
            dynamic = get_dynamic_proxy()
            if dynamic:
                return dynamic
        except Exception:
            pass

        # 2. 回退到静态代理池
        with Session(engine) as s:
            q = select(ProxyModel).where(ProxyModel.is_active == True)
            if region:
                q = q.where(ProxyModel.region == region)
            proxies = s.exec(q).all()
            if not proxies:
                return None
            proxies.sort(
                key=lambda p: p.success_count / max(p.success_count + p.fail_count, 1),
                reverse=True
            )
            with self._lock:
                idx = self._index % len(proxies)
                self._index += 1
            return proxies[idx].url

    def acquire(
        self,
        region: str = "",
        *,
        exclude: Iterable[str] | None = None,
        cooldown_seconds: float = 0,
        max_draws: int = 16,
        wait_timeout: float = 0,
    ) -> Optional[str]:
        """Lease a broadly distributed proxy without duplicating active workers."""
        excluded = {str(item or "").strip() for item in (exclude or []) if str(item or "").strip()}
        deadline = time.monotonic() + max(float(wait_timeout or 0), 0)
        while True:
            cooling_candidates: list[str] = []
            seen: set[str] = set()
            for _ in range(max(int(max_draws or 1), 1)):
                candidate = str(self.get_next(region) or "").strip()
                if not candidate or candidate in excluded or candidate in seen:
                    continue
                seen.add(candidate)
                now = time.monotonic()
                with self._condition:
                    if self._leases.get(candidate, 0) > 0:
                        continue
                    if now - self._last_assigned.get(candidate, 0) >= max(
                        float(cooldown_seconds or 0),
                        0,
                    ):
                        self._leases[candidate] = 1
                        self._last_assigned[candidate] = now
                        return candidate
                    cooling_candidates.append(candidate)
            now = time.monotonic()
            with self._condition:
                available = [
                    candidate
                    for candidate in cooling_candidates
                    if self._leases.get(candidate, 0) <= 0
                ]
                if available:
                    selected = min(
                        available,
                        key=lambda candidate: self._last_assigned.get(candidate, 0),
                    )
                    self._leases[selected] = 1
                    self._last_assigned[selected] = now
                    return selected
                remaining = deadline - now
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=min(remaining, 1))

    def release(self, url: str) -> None:
        proxy = str(url or "").strip()
        if not proxy:
            return
        with self._condition:
            remaining = self._leases.get(proxy, 0) - 1
            if remaining > 0:
                self._leases[proxy] = remaining
            else:
                self._leases.pop(proxy, None)
            self._condition.notify_all()

    def report_success(self, url: str) -> None:
        with Session(engine) as s:
            p = s.exec(select(ProxyModel).where(ProxyModel.url == url)).first()
            if p:
                p.success_count += 1
                p.last_checked = datetime.now(timezone.utc)
                s.add(p)
                s.commit()

    def report_fail(self, url: str) -> None:
        with Session(engine) as s:
            p = s.exec(select(ProxyModel).where(ProxyModel.url == url)).first()
            if p:
                p.fail_count += 1
                p.last_checked = datetime.now(timezone.utc)
                # 连续失败超过10次自动禁用
                if p.fail_count > 0 and p.success_count == 0 and p.fail_count >= 5:
                    p.is_active = False
                s.add(p)
                s.commit()

    def check_all(self) -> dict:
        """检测所有代理可用性"""
        import requests
        with Session(engine) as s:
            proxies = s.exec(select(ProxyModel)).all()
        results = {"ok": 0, "fail": 0}
        for p in proxies:
            try:
                r = requests.get("https://httpbin.org/ip",
                                 proxies={"http": p.url, "https": p.url},
                                 timeout=8)
                if r.status_code == 200:
                    self.report_success(p.url)
                    results["ok"] += 1
                    continue
            except Exception:
                pass
            self.report_fail(p.url)
            results["fail"] += 1
        return results


proxy_pool = ProxyPool()
