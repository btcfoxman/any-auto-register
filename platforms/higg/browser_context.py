"""Native CDP browser context for Higgsfield Clerk and DataDome."""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, quote_plus, urlparse

import requests
import websocket

from platforms.higg.core import HIGG_APP_URL, cookie_header_from_any, cookie_value


DEFAULT_BITBROWSER_API_URL = "http://127.0.0.1:54345"
DEFAULT_BITBROWSER_PROFILE_IDS = (
    "7a046e29b9964f41b0d023956c8d62e5",
    "4c417fd9e5fa4cf085fac3e4eb02f7dd",
    "9e567805001c493fb7bae305332d1c2a",
    "c100ade220ea4ef5b8f37cdfb035539e",
)
DEFAULT_CHROME_PROXY_PORTS = tuple(range(20001, 20021))
DEFAULT_CHROME_CDP_BASE_PORT = 19200
CLERK_SIGNUP_MARKER = "clerk.higgsfield.ai/v1/client/sign_ups"
FNF_MARKER = "fnf-api-gw.higgsfield.ai/fnf"


def parse_profile_ids(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        source = value
    else:
        source = str(value or "").replace("\r", "\n").replace(",", "\n").split("\n")
    return list(
        dict.fromkeys(
            item
            for item in (str(part or "").strip() for part in source)
            if item
        )
    )


def parse_proxy_ports(value: Any) -> list[int]:
    if isinstance(value, (list, tuple, set)):
        source = value
    else:
        source = str(value or "").replace("\r", "\n").replace(",", "\n").split("\n")
    result: list[int] = []
    for item in source:
        try:
            port = int(str(item or "").strip())
        except (TypeError, ValueError):
            continue
        if 1 <= port <= 65535 and port not in result:
            result.append(port)
    return result


def find_chrome_executable(value: str = "") -> str:
    explicit = str(value or "").strip().strip('"')
    candidates = [
        explicit,
        str(
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            / "Google/Chrome/Application/chrome.exe"
        ),
        str(
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Google/Chrome/Application/chrome.exe"
        ),
        str(
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google/Chrome/Application/chrome.exe"
        ),
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError("Google Chrome executable was not found")


def _proxy_url(profile: dict[str, Any]) -> str:
    host = str(profile.get("host") or "").strip()
    port = str(profile.get("port") or "").strip()
    proxy_type = str(profile.get("proxyType") or "socks5").strip().lower()
    if not host or not port:
        return ""
    if proxy_type == "socks":
        proxy_type = "socks5"
    username = str(profile.get("username") or "").strip()
    password = str(profile.get("password") or "").strip()
    auth = ""
    if username:
        auth = quote_plus(username)
        if password:
            auth += f":{quote_plus(password)}"
        auth += "@"
    return f"{proxy_type}://{auth}{host}:{port}"


def _proxy_identity(value: Any) -> tuple[str, int] | None:
    try:
        parsed = urlparse(str(value or "").strip())
        if not parsed.hostname or not parsed.port:
            return None
        return parsed.hostname.lower(), int(parsed.port)
    except Exception:
        return None


def _sec_ch_ua(user_agent: str) -> str:
    import re

    match = re.search(r"(?:Chrome|Chromium)/(\d+)", str(user_agent or ""))
    major = match.group(1) if match else "142"
    return (
        f'"Chromium";v="{major}", "Google Chrome";v="{major}", '
        '"Not_A Brand";v="99"'
    )


class _ProfileLeases:
    def __init__(self):
        self.condition = threading.Condition()
        self.leased: set[str] = set()
        self.cursor = 0

    def acquire(
        self,
        profiles: list[dict[str, Any]],
        *,
        preferred_proxy: str = "",
        timeout: float = 60,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(float(timeout), 1)
        preferred = _proxy_identity(preferred_proxy)
        with self.condition:
            while True:
                available = [
                    profile
                    for profile in profiles
                    if str(profile.get("id") or "") not in self.leased
                ]
                if preferred:
                    matched = [
                        profile
                        for profile in available
                        if _proxy_identity(_proxy_url(profile)) == preferred
                    ]
                    if matched:
                        available = matched
                if available:
                    index = self.cursor % len(available)
                    self.cursor += 1
                    selected = available[index]
                    profile_id = str(selected.get("id") or "")
                    self.leased.add(profile_id)
                    return selected
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("No free Higgsfield BitBrowser profile")
                self.condition.wait(timeout=min(remaining, 1))

    def release(self, profile_id: str) -> None:
        with self.condition:
            self.leased.discard(str(profile_id or ""))
            self.condition.notify_all()


_PROFILE_LEASES = _ProfileLeases()
_CHROME_PROFILE_LEASES = _ProfileLeases()


class _LocalPortLeases:
    def __init__(self):
        self.condition = threading.Condition()
        self.leased: set[int] = set()

    @staticmethod
    def _available(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("127.0.0.1", int(port)))
            except OSError:
                return False
        return True

    def acquire(self, base_port: int, *, timeout: float = 30) -> int:
        base = min(max(int(base_port or DEFAULT_CHROME_CDP_BASE_PORT), 1024), 64511)
        deadline = time.monotonic() + max(float(timeout or 30), 1)
        with self.condition:
            while True:
                for port in range(base + 1, min(base + 1024, 65535)):
                    if port in self.leased or not self._available(port):
                        continue
                    self.leased.add(port)
                    return port
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"No free Higgsfield Chrome CDP port after {base}"
                    )
                self.condition.wait(timeout=min(remaining, 1))

    def release(self, port: int) -> None:
        with self.condition:
            self.leased.discard(int(port or 0))
            self.condition.notify_all()


_CHROME_CDP_PORT_LEASES = _LocalPortLeases()


class _NativeCdpPage:
    def __init__(self, http_endpoint: str, timeout_seconds: float):
        response = requests.put(
            f"http://{http_endpoint}/json/new?{quote('about:blank', safe='')}",
            timeout=15,
        )
        response.raise_for_status()
        target = response.json()
        ws_url = str(target.get("webSocketDebuggerUrl") or "").strip()
        if not ws_url:
            raise RuntimeError(f"BitBrowser CDP target has no WebSocket URL: {target}")
        self.timeout_seconds = max(float(timeout_seconds), 20)
        self.ws = websocket.create_connection(
            ws_url,
            timeout=2,
            suppress_origin=True,
            enable_multithread=False,
        )
        self.next_id = 0
        self.clerk_responses: list[dict[str, Any]] = []
        self.fnf_statuses: list[dict[str, Any]] = []
        self.turnstile_session_urls: set[str] = set()
        self.turnstile_proof_responses = 0
        for method in ("Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"):
            self.command(method)

    def close(self) -> None:
        try:
            self.command("Page.close", timeout=3)
        except Exception:
            pass
        try:
            self.ws.close()
        except Exception:
            pass

    def command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 15,
    ) -> dict[str, Any]:
        self.next_id += 1
        command_id = self.next_id
        self.ws.send(
            json.dumps(
                {"id": command_id, "method": method, "params": params or {}},
                separators=(",", ":"),
            )
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self._receive(max(min(deadline - time.monotonic(), 1), 0.05))
            if not message:
                continue
            if message.get("id") == command_id:
                if message.get("error"):
                    raise RuntimeError(f"CDP {method} failed: {message['error']}")
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            self._record_event(message)
        raise TimeoutError(f"CDP command timed out: {method}")

    def poll(self, timeout: float = 0.5) -> dict[str, Any] | None:
        message = self._receive(timeout)
        if message:
            self._record_event(message)
        return message

    def _receive(self, timeout: float) -> dict[str, Any] | None:
        self.ws.settimeout(max(timeout, 0.05))
        try:
            raw = self.ws.recv()
        except websocket.WebSocketTimeoutException:
            return None
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    def _record_event(self, message: dict[str, Any]) -> None:
        if message.get("method") != "Network.responseReceived":
            return
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        response = params.get("response") if isinstance(params.get("response"), dict) else {}
        url = str(response.get("url") or "")
        record = {
            "request_id": str(params.get("requestId") or ""),
            "url": url,
            "status": int(response.get("status") or 0),
            "mime_type": str(response.get("mimeType") or ""),
            "headers": response.get("headers") if isinstance(response.get("headers"), dict) else {},
        }
        if CLERK_SIGNUP_MARKER in url:
            self.clerk_responses.append(record)
        parsed = urlparse(url)
        if parsed.netloc.endswith("challenges.cloudflare.com"):
            if "/turnstile/f/" in parsed.path:
                self.turnstile_session_urls.add(url)
            if "/challenge-platform/" in parsed.path and "/fo/" in parsed.path:
                self.turnstile_proof_responses += 1
        if FNF_MARKER in url:
            path = urlparse(url).path
            query = urlparse(url).query
            self.fnf_statuses.append(
                {
                    "path": f"{path}?{query}" if query else path,
                    "status": record["status"],
                    "datadome_protected": bool(
                        record["headers"].get("x-datadome")
                        or record["headers"].get("X-DataDome")
                    ),
                }
            )

    def evaluate(self, expression: str, *, await_promise: bool = False) -> Any:
        result = self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
            timeout=30,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(f"CDP Runtime.evaluate failed: {result['exceptionDetails']}")
        remote = result.get("result") if isinstance(result.get("result"), dict) else {}
        return remote.get("value")

    def navigate(self, url: str) -> None:
        self.command("Page.navigate", {"url": url}, timeout=20)
        self.wait_js(
            "document.readyState === 'interactive' || document.readyState === 'complete'",
            timeout=40,
        )

    def wait_js(self, expression: str, *, timeout: float = 30) -> Any:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.evaluate(expression)
            if value:
                return value
            self.poll(0.25)
        raise TimeoutError(f"JavaScript condition timed out: {expression[:100]}")

    def click_js(self, expression: str, *, timeout: float = 30) -> None:
        self.wait_js(expression, timeout=timeout)

    def fill_input(self, selector: str, value: str) -> None:
        payload = json.dumps({"selector": selector, "value": value})
        if not self.evaluate(
            f"""
(() => {{
  const args = {payload};
  const input = document.querySelector(args.selector);
  if (!input) return false;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(input, args.value);
  input.dispatchEvent(new Event('input', {{bubbles:true}}));
  input.dispatchEvent(new Event('change', {{bubbles:true}}));
  return true;
}})()
            """
        ):
            raise RuntimeError(f"Higgsfield input not found: {selector}")

    def click_turnstile_if_visible(self) -> bool:
        rect = self.evaluate(
            """
(() => {
  const frame = [...document.querySelectorAll('iframe')].find((item) => {
    const box = item.getBoundingClientRect();
    return ((item.src || '').includes('challenges.cloudflare.com') ||
      (item.src || '').includes('turnstile') ||
      /cloudflare|challenge/i.test(item.title || '')) &&
      box.width > 20 && box.height > 20;
  });
  if (!frame) return null;
  const box = frame.getBoundingClientRect();
  return {x: box.x + Math.min(Math.max(box.width * 0.18, 24), 46), y: box.y + box.height * 0.52};
})()
            """
        )
        if not isinstance(rect, dict):
            return False
        x = float(rect.get("x") or 0)
        y = float(rect.get("y") or 0)
        self.command("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self.command(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
        )
        self.command(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
        )
        return True

    def response_body(self, record: dict[str, Any]) -> dict[str, Any]:
        result = self.command(
            "Network.getResponseBody",
            {"requestId": record["request_id"]},
            timeout=10,
        )
        try:
            value = json.loads(str(result.get("body") or ""))
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def all_cookies(self) -> list[dict[str, Any]]:
        result = self.command("Network.getAllCookies")
        cookies = result.get("cookies")
        return [item for item in cookies if isinstance(item, dict)] if isinstance(cookies, list) else []

    def higg_cookies(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self.all_cookies()
            if str(item.get("domain") or "").lstrip(".").endswith("higgsfield.ai")
        ]

    def set_cookies(self, cookies: Any) -> None:
        if isinstance(cookies, list):
            source = cookies
        else:
            source = []
            for pair in cookie_header_from_any(cookies).split(";"):
                name, separator, value = pair.strip().partition("=")
                if separator and name and value:
                    source.append({"name": name, "value": value})
        records: list[dict[str, Any]] = []
        for item in source:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if not name or not value or name == "datadome":
                continue
            record: dict[str, Any] = {
                "name": name,
                "value": value,
                "domain": str(item.get("domain") or ".higgsfield.ai"),
                "path": str(item.get("path") or "/"),
                "secure": bool(item.get("secure", True)),
                "httpOnly": bool(item.get("httpOnly", False)),
            }
            expires = item.get("expires")
            if isinstance(expires, (int, float)) and expires > 0:
                record["expires"] = float(expires)
            records.append(record)
        if records:
            self.command("Network.setCookies", {"cookies": records})

    def clear_higg_data(self) -> None:
        for origin in (
            "https://higgsfield.ai",
            "https://clerk.higgsfield.ai",
            "https://fnf-api-gw.higgsfield.ai",
        ):
            self.command(
                "Storage.clearDataForOrigin",
                {"origin": origin, "storageTypes": "all"},
            )


class HiggBitBrowserSession:
    """Owns one BitBrowser profile and drives it through raw CDP only."""

    def __init__(
        self,
        *,
        proxy: str | None = None,
        log_fn: Callable[[str], None] = print,
        bitbrowser_api_url: str = DEFAULT_BITBROWSER_API_URL,
        bitbrowser_profile_ids: Any = DEFAULT_BITBROWSER_PROFILE_IDS,
        close_after_use: bool = True,
        clear_site_data: bool = True,
        timeout_seconds: float = 120,
        turnstile_max_sessions: int = 2,
        turnstile_max_proof_responses: int = 8,
        **_ignored: Any,
    ):
        self.preferred_proxy = str(proxy or "").strip()
        self.proxy_url = self.preferred_proxy
        self.log = log_fn
        self.api_url = str(bitbrowser_api_url or DEFAULT_BITBROWSER_API_URL).rstrip("/")
        self.profile_ids = parse_profile_ids(bitbrowser_profile_ids) or list(
            DEFAULT_BITBROWSER_PROFILE_IDS
        )
        self.close_after_use = bool(close_after_use)
        self.clear_site_data = bool(clear_site_data)
        self.timeout_seconds = max(float(timeout_seconds or 120), 30)
        self.turnstile_max_sessions = max(int(turnstile_max_sessions or 2), 1)
        self.turnstile_max_proof_responses = max(
            int(turnstile_max_proof_responses or 8),
            3,
        )
        self.profile_id = ""
        self._page: _NativeCdpPage | None = None
        self._leased = False

    def _post(self, path: str, payload: dict[str, Any], *, timeout: int = 90) -> dict[str, Any]:
        response = requests.post(
            f"{self.api_url}{path}",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else {}

    def _profiles(self) -> list[dict[str, Any]]:
        value = self._post("/browser/list", {"page": 0, "pageSize": 100}, timeout=20)
        data = value.get("data") if isinstance(value.get("data"), dict) else {}
        items = data.get("list") if isinstance(data.get("list"), list) else []
        by_id = {
            str(item.get("id") or ""): item
            for item in items
            if isinstance(item, dict)
        }
        profiles = [by_id[profile_id] for profile_id in self.profile_ids if profile_id in by_id]
        if not profiles:
            raise RuntimeError("No configured Higgsfield BitBrowser profiles were found")
        return profiles

    def _open_profile(self) -> str:
        value = self._post("/browser/open", {"id": self.profile_id})
        data = value.get("data") if isinstance(value.get("data"), dict) else {}
        http_endpoint = str(data.get("http") or "").strip()
        ws_url = str(data.get("ws") or "").strip()
        if not http_endpoint and ws_url:
            http_endpoint = ws_url.split("/devtools/", 1)[0].removeprefix("ws://")
        if not http_endpoint:
            ports = self._post("/browser/ports", {"ids": [self.profile_id]}, timeout=20)
            port_data = ports.get("data") if isinstance(ports.get("data"), dict) else {}
            port = str(port_data.get(self.profile_id) or "").strip()
            if port:
                http_endpoint = f"127.0.0.1:{port}"
        if not http_endpoint:
            raise RuntimeError(f"BitBrowser did not return a CDP endpoint: {value}")
        return http_endpoint

    def start(self) -> "HiggBitBrowserSession":
        if self._page is not None:
            return self
        profile = _PROFILE_LEASES.acquire(
            self._profiles(),
            preferred_proxy=self.preferred_proxy,
            timeout=min(self.timeout_seconds, 90),
        )
        self.profile_id = str(profile.get("id") or "")
        self.proxy_url = _proxy_url(profile) or self.preferred_proxy
        self._leased = True
        try:
            endpoint = self._open_profile()
            self._page = _NativeCdpPage(endpoint, self.timeout_seconds)
            if self.clear_site_data:
                self._page.clear_higg_data()
            self._page.navigate(HIGG_APP_URL)
            time.sleep(3)
            snapshot = self.snapshot()
            self.log(
                "Higgsfield: native BitBrowser session ready "
                f"profile={self.profile_id} proxy={self.proxy_url or '-'} "
                f"datadome={'yes' if snapshot.get('datadome') else 'no'}"
            )
            return self
        except Exception:
            self.close()
            raise

    def create_signup(
        self,
        *,
        email: str,
        password: str,
        captcha_token: str = "",
    ) -> dict[str, Any]:
        self.start()
        if captcha_token:
            self.log("Higgsfield: supplied Turnstile token ignored; Clerk UI owns the challenge")
        self._page.click_js(
            """
(() => {
  const button = document.querySelector('button.hfnav-auth-signup') ||
    [...document.querySelectorAll('button')].find((item) => item.textContent.trim() === 'Sign up');
  if (!button) return false;
  button.click();
  return true;
})()
            """,
            timeout=35,
        )
        self._page.click_js(
            """
(() => {
  const button = [...document.querySelectorAll('button')].find(
    (item) => item.textContent.trim() === 'Continue with Email'
  );
  if (!button) return false;
  button.click();
  return true;
})()
            """,
            timeout=20,
        )
        self._page.wait_js("document.querySelector('input[type=email]') !== null", timeout=15)
        self._page.fill_input("input[type=email]", str(email or "").strip())
        self._page.fill_input("input[type=password]", str(password or ""))
        start_index = len(self._page.clerk_responses)
        self._page.click_js(
            """
(() => {
  const submit = document.querySelector('input[type=submit]');
  if (!submit) return false;
  submit.click();
  return true;
})()
            """
        )
        deadline = time.monotonic() + self.timeout_seconds
        clicked = False
        while time.monotonic() < deadline:
            new_records = self._page.clerk_responses[start_index:]
            signup_record = next(
                (
                    record
                    for record in new_records
                    if urlparse(record["url"]).path.endswith("/sign_ups")
                ),
                None,
            )
            if signup_record:
                payload = self._page.response_body(signup_record)
                if signup_record["status"] >= 400:
                    raise RuntimeError(
                        f"Higgsfield browser Clerk signup HTTP {signup_record['status']}: {payload}"
                    )
                self.log("Higgsfield: native Clerk UI submitted signup")
                return payload
            if (
                len(self._page.turnstile_session_urls) >= self.turnstile_max_sessions
                or self._page.turnstile_proof_responses >= self.turnstile_max_proof_responses
            ):
                raise RuntimeError(
                    "Higgsfield Turnstile retry budget exhausted for current proxy "
                    f"(sessions={len(self._page.turnstile_session_urls)}, "
                    f"proofs={self._page.turnstile_proof_responses})"
                )
            if not clicked:
                clicked = self._page.click_turnstile_if_visible()
                if clicked:
                    self.log("Higgsfield: clicked the visible Turnstile verifier")
            self._page.poll(0.4)
        raise TimeoutError("Higgsfield Clerk signup timed out in native BitBrowser")

    def complete_email_verification(self, code: str) -> dict[str, Any]:
        self.start()
        normalized = str(code or "").strip()
        self._page.wait_js(
            """
(() => [...document.querySelectorAll('input')].some((item) =>
  item.autocomplete === 'one-time-code' || /code/i.test(item.name || '') ||
  /verification/i.test(item.getAttribute('aria-label') || '')
))()
            """,
            timeout=35,
        )
        payload = json.dumps(normalized)
        if not self._page.evaluate(
            f"""
(() => {{
  const code = {payload};
  const inputs = [...document.querySelectorAll('input')].filter((item) =>
    item.autocomplete === 'one-time-code' || /code/i.test(item.name || '') ||
    /verification/i.test(item.getAttribute('aria-label') || '')
  );
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  if (inputs.length === 1) {{
    setter.call(inputs[0], code);
    inputs[0].dispatchEvent(new Event('input', {{bubbles:true}}));
    inputs[0].dispatchEvent(new Event('change', {{bubbles:true}}));
    return true;
  }}
  if (inputs.length >= code.length) {{
    inputs.slice(0, code.length).forEach((input, index) => {{
      setter.call(input, code[index]);
      input.dispatchEvent(new Event('input', {{bubbles:true}}));
      input.dispatchEvent(new Event('change', {{bubbles:true}}));
    }});
    return true;
  }}
  return false;
}})()
                """
        ):
            raise RuntimeError("Unable to fill Clerk email verification code")
        self._page.evaluate(
            """
(() => {
  const button = [...document.querySelectorAll('button,input[type=submit]')].find((item) =>
    /verify|continue|confirm/i.test((item.textContent || item.value || '').trim())
  );
  if (button) button.click();
  return true;
})()
            """
        )
        deadline = time.monotonic() + min(self.timeout_seconds, 90)
        while time.monotonic() < deadline:
            self._page.poll(0.4)
            snapshot = self.snapshot()
            if snapshot.get("clerk_jwt"):
                time.sleep(3)
                for _ in range(10):
                    self._page.poll(0.2)
                snapshot = self.snapshot()
                self.log("Higgsfield: native Clerk email verification completed")
                return snapshot
        raise TimeoutError("Clerk session cookie was not created after email verification")

    def snapshot(self) -> dict[str, Any]:
        if self._page is None:
            raise RuntimeError("Higgsfield BitBrowser session is not started")
        cookies = self._page.higg_cookies()
        cookie_header = cookie_header_from_any(cookies)
        token = next(
            (
                str(item.get("value") or "")
                for item in cookies
                if item.get("name") == "__session"
            ),
            "",
        )
        fingerprint = self._page.evaluate(
            """
(() => ({
  userAgent: navigator.userAgent || '',
  platform: (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '',
  webdriver: navigator.webdriver === true
}))()
            """
        )
        user_agent = str((fingerprint or {}).get("userAgent") or "")
        raw_platform = str((fingerprint or {}).get("platform") or "Windows")
        lowered = raw_platform.lower()
        platform = (
            "Windows"
            if "win" in lowered
            else "macOS"
            if "mac" in lowered
            else "Linux"
            if "linux" in lowered
            else raw_platform
        )
        return {
            "cookies": cookies,
            "cookie_header": cookie_header,
            "datadome": cookie_value(cookie_header, "datadome"),
            "clerk_jwt": token,
            "user_agent": user_agent,
            "sec_ch_ua": _sec_ch_ua(user_agent),
            "sec_ch_ua_platform": f'"{platform}"',
            "browser_profile_id": self.profile_id,
            "browser_webdriver": bool((fingerprint or {}).get("webdriver")),
            "proxy_url": self.proxy_url,
            "risk_probe": self._risk_probe(),
        }

    def _risk_probe(self) -> dict[str, Any]:
        statuses = self._page.fnf_statuses[-30:]
        last_blocked = max(
            (index for index, item in enumerate(statuses) if item.get("status") == 403),
            default=-1,
        )
        last_success = max(
            (
                index
                for index, item in enumerate(statuses)
                if 200 <= int(item.get("status") or 0) < 400
            ),
            default=-1,
        )
        return {
            "fnf_statuses": statuses,
            "blocked": last_blocked > last_success,
        }

    def bootstrap_authenticated(self, *, cookies: Any, token: str) -> dict[str, Any]:
        self.start()
        self._page.set_cookies(cookies)
        self._page.navigate(HIGG_APP_URL)
        deadline = time.monotonic() + min(self.timeout_seconds, 60)
        while time.monotonic() < deadline:
            self._page.poll(0.4)
            snapshot = self.snapshot()
            if snapshot.get("datadome") and self._page.fnf_statuses:
                break
        snapshot = self.snapshot()
        snapshot["clerk_jwt"] = snapshot.get("clerk_jwt") or str(token or "")
        if not snapshot.get("datadome"):
            raise RuntimeError("BitBrowser did not establish a Higgsfield DataDome cookie")
        if snapshot["risk_probe"].get("blocked"):
            raise RuntimeError("BitBrowser FNF requests remain blocked by DataDome")
        return snapshot

    def close(self) -> None:
        if self._page is not None:
            self._page.close()
            self._page = None
        if self.profile_id and self.close_after_use:
            try:
                self._post("/browser/close", {"id": self.profile_id}, timeout=30)
            except Exception as exc:
                self.log(f"Higgsfield: failed to close BitBrowser profile {self.profile_id}: {exc}")
        if self._leased:
            _PROFILE_LEASES.release(self.profile_id)
            self._leased = False
        self.profile_id = ""

    def __enter__(self) -> "HiggBitBrowserSession":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class HiggChromeBrowserSession(HiggBitBrowserSession):
    """Runs a dedicated installed-Chrome profile with a fixed proxy and CDP port."""

    def __init__(
        self,
        *,
        proxy: str | None = None,
        log_fn: Callable[[str], None] = print,
        chrome_executable: str = "",
        chrome_user_data_root: str = "",
        chrome_proxy_ports: Any = DEFAULT_CHROME_PROXY_PORTS,
        chrome_cdp_base_port: int = DEFAULT_CHROME_CDP_BASE_PORT,
        close_after_use: bool = True,
        clear_site_data: bool = True,
        timeout_seconds: float = 120,
        turnstile_max_sessions: int = 2,
        turnstile_max_proof_responses: int = 8,
        **_ignored: Any,
    ):
        super().__init__(
            proxy=proxy,
            log_fn=log_fn,
            close_after_use=close_after_use,
            clear_site_data=clear_site_data,
            timeout_seconds=timeout_seconds,
            turnstile_max_sessions=turnstile_max_sessions,
            turnstile_max_proof_responses=turnstile_max_proof_responses,
        )
        self.chrome_executable = find_chrome_executable(chrome_executable)
        root = str(chrome_user_data_root or "").strip()
        self.chrome_user_data_root = Path(
            root or Path("data") / "higg-chrome-profiles"
        ).resolve()
        self.chrome_proxy_ports = parse_proxy_ports(chrome_proxy_ports) or list(
            DEFAULT_CHROME_PROXY_PORTS
        )
        self.chrome_cdp_base_port = max(int(chrome_cdp_base_port or 19200), 1024)
        self._process: subprocess.Popen | None = None
        self._cdp_port = 0
        self._user_data_dir: Path | None = None
        self._chrome_log_path: Path | None = None
        self._chrome_log_file: Any = None

    def _chrome_profiles(self) -> list[dict[str, Any]]:
        preferred = urlparse(self.preferred_proxy)
        if preferred.hostname and preferred.port:
            proxy_type = preferred.scheme.lower()
            if proxy_type in {"socks", "socks5h"}:
                proxy_type = "socks5"
            return [
                {
                    "id": f"chrome-proxy-{preferred.hostname}-{preferred.port}",
                    "proxyType": proxy_type or "socks5",
                    "host": preferred.hostname,
                    "port": preferred.port,
                    "username": preferred.username or "",
                    "password": preferred.password or "",
                }
            ]
        return [
            {
                "id": f"chrome-proxy-{port}",
                "proxyType": "socks5",
                "host": "127.0.0.1",
                "port": port,
            }
            for port in self.chrome_proxy_ports
        ]

    def _chrome_log_tail(self) -> str:
        if self._chrome_log_file is not None:
            try:
                self._chrome_log_file.flush()
            except Exception:
                pass
        if not self._chrome_log_path or not self._chrome_log_path.is_file():
            return ""
        try:
            value = self._chrome_log_path.read_text(
                encoding="utf-8",
                errors="replace",
            )[-1600:].strip()
        except Exception:
            return ""
        if self.proxy_url:
            value = value.replace(self.proxy_url, "[proxy]")
        return " ".join(value.splitlines())

    def _wait_for_cdp(self, port: int) -> str:
        endpoint = f"127.0.0.1:{port}"
        deadline = time.monotonic() + min(self.timeout_seconds, 60)
        last_error = ""
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                log_tail = self._chrome_log_tail()
                raise RuntimeError(
                    f"Google Chrome exited before CDP became ready "
                    f"({self._process.returncode})"
                    + (f": {log_tail}" if log_tail else "")
                )
            try:
                response = requests.get(f"http://{endpoint}/json/version", timeout=1)
                response.raise_for_status()
                return endpoint
            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.25)
        log_tail = self._chrome_log_tail()
        raise TimeoutError(
            f"Google Chrome CDP did not start on {endpoint}: {last_error}"
            + (f"; Chrome log: {log_tail}" if log_tail else "")
        )

    def start(self) -> "HiggChromeBrowserSession":
        if self._page is not None:
            return self
        profile = _CHROME_PROFILE_LEASES.acquire(
            self._chrome_profiles(),
            preferred_proxy=self.preferred_proxy,
            timeout=min(self.timeout_seconds, 90),
        )
        self.profile_id = str(profile.get("id") or "")
        self.proxy_url = _proxy_url(profile)
        self._leased = True
        try:
            proxy_port = int(profile["port"])
            self._cdp_port = _CHROME_CDP_PORT_LEASES.acquire(
                self.chrome_cdp_base_port,
                timeout=min(self.timeout_seconds, 30),
            )
            proxy_root = self.chrome_user_data_root / f"proxy-{proxy_port}"
            proxy_root.mkdir(parents=True, exist_ok=True)
            self._user_data_dir = Path(
                tempfile.mkdtemp(prefix="session-", dir=str(proxy_root))
            )
            self._chrome_log_path = self._user_data_dir / "chrome.log"
            self._chrome_log_file = self._chrome_log_path.open("ab")
            command = [
                self.chrome_executable,
                f"--user-data-dir={self._user_data_dir}",
                "--profile-directory=Default",
                f"--proxy-server={self.proxy_url}",
                f"--remote-debugging-port={self._cdp_port}",
                "--remote-allow-origins=*",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-mode",
                HIGG_APP_URL,
            ]
            if os.name != "nt":
                command[1:1] = ["--no-sandbox", "--disable-dev-shm-usage"]
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._process = subprocess.Popen(
                command,
                stdout=self._chrome_log_file,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
            )
            endpoint = self._wait_for_cdp(self._cdp_port)
            self._page = _NativeCdpPage(endpoint, self.timeout_seconds)
            if self.clear_site_data:
                self._page.clear_higg_data()
            self._page.navigate(HIGG_APP_URL)
            time.sleep(3)
            snapshot = self.snapshot()
            self.log(
                "Higgsfield: native Chrome session ready "
                f"profile={self.profile_id} proxy={self.proxy_url} "
                f"cdp={self._cdp_port} webdriver={snapshot.get('browser_webdriver')}"
            )
            return self
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._page is not None:
            self._page.close()
            self._page = None
        if self._process is not None:
            try:
                if os.name != "nt":
                    os.killpg(self._process.pid, signal.SIGTERM)
                else:
                    self._process.terminate()
                self._process.wait(timeout=10)
            except Exception:
                try:
                    if os.name != "nt":
                        os.killpg(self._process.pid, signal.SIGKILL)
                    else:
                        self._process.kill()
                    self._process.wait(timeout=5)
                except Exception:
                    pass
        self._process = None
        if self._chrome_log_file is not None:
            try:
                self._chrome_log_file.close()
            except Exception:
                pass
            self._chrome_log_file = None
        if self._cdp_port:
            _CHROME_CDP_PORT_LEASES.release(self._cdp_port)
            self._cdp_port = 0
        if self._leased:
            _CHROME_PROFILE_LEASES.release(self.profile_id)
            self._leased = False
        if self._user_data_dir is not None:
            shutil.rmtree(self._user_data_dir, ignore_errors=True)
            self._user_data_dir = None
        self._chrome_log_path = None
        self.profile_id = ""


def HiggBrowserSession(
    *,
    browser_mode: str = "native_chrome",
    **kwargs: Any,
) -> HiggBitBrowserSession:
    mode = str(browser_mode or "native_chrome").strip().lower()
    if mode in {"native_chrome", "chrome", "local_chrome"}:
        return HiggChromeBrowserSession(**kwargs)
    if mode in {"bitbrowser", "bit_browser"}:
        return HiggBitBrowserSession(**kwargs)
    raise ValueError(f"Unsupported Higgsfield browser mode: {browser_mode}")
