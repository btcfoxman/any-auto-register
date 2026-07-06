"""Browser-assisted Freebeat email verification sender."""
from __future__ import annotations

import json
import random
import time
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from platforms.freebeat.core import (
    FREEBEAT_BASE,
    FREEBEAT_DEFAULT_FRONTEND_PATH,
    FREEBEAT_DEFAULT_VERIFY_SOURCE,
    FREEBEAT_SEND_CODE_PATH,
    _cookie_header_from_any,
    _is_send_code_already_sent,
    _json_dumps,
    _normalize_frontend_path,
    _validate_api_payload,
)


FREEBEAT_BROWSER_ACCEPT_LANGUAGE = "en-US,en;q=0.9"
FREEBEAT_BROWSER_LOCALE = "en-US"
FREEBEAT_BROWSER_TIMEZONE = "America/New_York"
FREEBEAT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
FREEBEAT_BROWSER_TIMEOUT_SECONDS = 120
FREEBEAT_BROWSER_ENGINE = "playwright"


def _page_url(frontend_path: str = "") -> str:
    path = _normalize_frontend_path(frontend_path or FREEBEAT_DEFAULT_FRONTEND_PATH)
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{FREEBEAT_BASE}{path}"


def _candidate_page_urls(frontend_path: str = "") -> list[str]:
    urls = [_page_url(frontend_path)]
    for path in (
        "/login?redirectTo=%2F",
        "/tw/login?redirectTo=%2Ftw",
    ):
        urls.append(f"{FREEBEAT_BASE}{path}")
    return list(dict.fromkeys(urls))


def _is_freebeat_page_url(url: Any) -> bool:
    try:
        hostname = urlparse(str(url or "")).hostname or ""
    except Exception:
        return False
    return hostname == "freebeat.ai" or hostname.endswith(".freebeat.ai")


def _playwright_proxy(proxy: str | None) -> dict[str, str] | None:
    raw = str(proxy or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname:
        return {"server": raw}
    if parsed.username or parsed.password:
        netloc = parsed.hostname
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        data = {"server": f"{parsed.scheme}://{netloc}"}
        if parsed.username:
            data["username"] = unquote(parsed.username)
        if parsed.password:
            data["password"] = unquote(parsed.password)
        return data
    return {"server": raw}


def _normalize_browser_engine(browser_engine: str | None) -> str:
    value = str(browser_engine or FREEBEAT_BROWSER_ENGINE).strip().lower()
    if value in {"", "auto"}:
        return "auto"
    if value in {"patchright", "playwright"}:
        return value
    return "playwright"


def _sync_playwright_context(browser_engine: str | None):
    engine = _normalize_browser_engine(browser_engine)
    if engine in {"auto", "patchright"}:
        try:
            from patchright.sync_api import sync_playwright as sync_patchright

            return sync_patchright(), "patchright"
        except Exception:
            if engine == "patchright":
                raise
    from playwright.sync_api import sync_playwright

    return sync_playwright(), "playwright"


def _cookies_to_header(cookies: list[dict[str, Any]]) -> str:
    return _cookie_header_from_any(
        [
            {"name": item.get("name"), "value": item.get("value")}
            for item in cookies
            if str(item.get("domain") or "").lstrip(".").endswith("freebeat.ai")
        ]
    )


def _parse_json_text(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


def _first_locator(locator):
    first = locator.first
    return first() if callable(first) else first


def _human_pause(page, min_ms: int = 120, max_ms: int = 420) -> None:
    try:
        page.wait_for_timeout(random.randint(min_ms, max(min_ms, max_ms)))
    except Exception:
        pass


def _viewport_size(page) -> dict[str, float]:
    try:
        data = page.evaluate(
            "() => ({ width: window.innerWidth || 1365, height: window.innerHeight || 768 })"
        )
        return {
            "width": float((data or {}).get("width") or 1365),
            "height": float((data or {}).get("height") or 768),
        }
    except Exception:
        return {"width": 1365.0, "height": 768.0}


def _human_mouse_path(page, x: float, y: float, *, start_near: bool = False) -> None:
    viewport = _viewport_size(page)
    width = max(320.0, viewport["width"])
    height = max(240.0, viewport["height"])
    target_x = min(max(float(x), 2.0), width - 2.0)
    target_y = min(max(float(y), 2.0), height - 2.0)

    if start_near:
        start_x = target_x + random.uniform(-180, 180)
        start_y = target_y + random.uniform(-120, 120)
    else:
        start_x = random.uniform(width * 0.12, width * 0.88)
        start_y = random.uniform(height * 0.12, height * 0.88)
    start_x = min(max(start_x, 2.0), width - 2.0)
    start_y = min(max(start_y, 2.0), height - 2.0)

    ctrl_x = (start_x + target_x) / 2 + random.uniform(-120, 120)
    ctrl_y = (start_y + target_y) / 2 + random.uniform(-80, 80)
    steps = random.randint(18, 34)
    ease_power = random.uniform(1.7, 2.4)
    page.mouse.move(start_x, start_y, steps=random.randint(4, 9))
    _human_pause(page, 80, 240)
    for index in range(1, steps + 1):
        t = index / steps
        ease = 1 - (1 - t) ** ease_power
        bx = (1 - ease) ** 2 * start_x + 2 * (1 - ease) * ease * ctrl_x + ease ** 2 * target_x
        by = (1 - ease) ** 2 * start_y + 2 * (1 - ease) * ease * ctrl_y + ease ** 2 * target_y
        bx += random.uniform(-1.5, 1.5)
        by += random.uniform(-1.2, 1.2)
        page.mouse.move(min(max(bx, 1.0), width - 1.0), min(max(by, 1.0), height - 1.0))
        if index in {steps // 3, (steps * 2) // 3} and random.random() < 0.45:
            _human_pause(page, 25, 120)


def _human_mouse_click(page, x: float, y: float, *, pre_hover: bool = True) -> None:
    if pre_hover:
        _human_mouse_path(
            page,
            x + random.uniform(-24, 24),
            y + random.uniform(-18, 18),
            start_near=random.random() < 0.35,
        )
        _human_pause(page, 160, 520)
    _human_mouse_path(page, x, y, start_near=True)
    for _ in range(random.randint(1, 3)):
        page.mouse.move(x + random.uniform(-3, 3), y + random.uniform(-2.5, 2.5), steps=random.randint(2, 5))
        _human_pause(page, 35, 130)
    page.mouse.down()
    _human_pause(page, 75, 190)
    page.mouse.up()


def _human_click_locator(page, locator, *, timeout: int = 5000, humanize: bool = True) -> None:
    if not humanize:
        locator.click(timeout=timeout)
        return
    locator.scroll_into_view_if_needed(timeout=timeout)
    box = locator.bounding_box(timeout=timeout)
    if not box:
        locator.click(timeout=timeout)
        return
    x = box["x"] + box["width"] * random.uniform(0.35, 0.65)
    y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
    _human_mouse_click(page, x, y)


def _human_fill_locator(page, locator, text: str, *, timeout: int = 5000, humanize: bool = True) -> None:
    if not humanize:
        locator.click(timeout=timeout)
        locator.fill(text, timeout=timeout)
        return
    _human_click_locator(page, locator, timeout=timeout, humanize=True)
    _human_pause(page, 120, 320)
    try:
        page.keyboard.press("Control+A")
    except Exception:
        pass
    try:
        page.keyboard.press("Backspace")
    except Exception:
        pass
    _human_pause(page, 80, 180)
    page.keyboard.type(text, delay=random.randint(45, 105))


def _install_stealth_evasions(page) -> None:
    page.add_init_script(
        """
(() => {
  const defineGetter = (target, prop, getter) => {
    try {
      Object.defineProperty(target, prop, { configurable: true, get: getter });
    } catch (_) {}
  };
  defineGetter(Navigator.prototype, 'webdriver', () => undefined);
  defineGetter(Navigator.prototype, 'languages', () => ['en-US', 'en']);
  defineGetter(Navigator.prototype, 'hardwareConcurrency', () => 8);
  defineGetter(Navigator.prototype, 'deviceMemory', () => 8);
  defineGetter(Navigator.prototype, 'maxTouchPoints', () => 0);
  defineGetter(Navigator.prototype, 'plugins', () => {
    const plugins = [
      { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }
    ];
    plugins.item = (index) => plugins[index] || null;
    plugins.namedItem = (name) => plugins.find((item) => item.name === name) || null;
    plugins.refresh = () => undefined;
    return plugins;
  });
  defineGetter(Navigator.prototype, 'mimeTypes', () => {
    const mimeTypes = [
      { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
      { type: 'text/pdf', suffixes: 'pdf', description: 'Portable Document Format' }
    ];
    mimeTypes.item = (index) => mimeTypes[index] || null;
    mimeTypes.namedItem = (name) => mimeTypes.find((item) => item.type === name) || null;
    return mimeTypes;
  });
  window.chrome = window.chrome || {};
  window.chrome.runtime = window.chrome.runtime || {};
  window.chrome.app = window.chrome.app || { isInstalled: false };
  window.chrome.csi = window.chrome.csi || (() => ({}));
  window.chrome.loadTimes = window.chrome.loadTimes || (() => ({}));
  const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
  if (originalQuery) {
    window.navigator.permissions.query = (parameters) => {
      if (parameters && parameters.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission });
      }
      return originalQuery.call(window.navigator.permissions, parameters);
    };
  }
  const originalGetParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return originalGetParameter.call(this, parameter);
  };
  if (window.WebGL2RenderingContext) {
    const originalGetParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(parameter) {
      if (parameter === 37445) return 'Intel Inc.';
      if (parameter === 37446) return 'Intel Iris OpenGL Engine';
      return originalGetParameter2.call(this, parameter);
    };
  }
  const originalCanPlayType = HTMLMediaElement.prototype.canPlayType;
  HTMLMediaElement.prototype.canPlayType = function(type) {
    if (typeof type === 'string' && /video\\/mp4|audio\\/mpeg|audio\\/mp4/i.test(type)) return 'probably';
    return originalCanPlayType.call(this, type);
  };
  if (!window.outerWidth) defineGetter(window, 'outerWidth', () => window.innerWidth);
  if (!window.outerHeight) defineGetter(window, 'outerHeight', () => window.innerHeight + 85);
})();
        """
    )


def _install_capture_hooks(page) -> None:
    page.add_init_script(
        """
(() => {
  window.__freebeatTurnstile = window.__freebeatTurnstile || { tokens: [], renders: [], widgets: [] };
  const install = () => {
    const ts = window.turnstile;
    if (!ts || ts.__freebeatHooked || typeof ts.render !== 'function') return;
    const originalRender = ts.render.bind(ts);
    const originalExecute = typeof ts.execute === 'function' ? ts.execute.bind(ts) : null;
    const originalGetResponse = typeof ts.getResponse === 'function' ? ts.getResponse.bind(ts) : null;
    const rememberToken = (token) => {
      if (token && String(token).length > 20) window.__freebeatTurnstile.tokens.push(String(token));
    };
    ts.render = (container, options = {}) => {
      const copied = {};
      for (const key of ['sitekey', 'action', 'cData', 'chlPageData']) {
        if (options && options[key]) copied[key] = String(options[key]);
      }
      window.__freebeatTurnstile.renders.push(copied);
      const originalCallback = options.callback;
      options.callback = (token, ...rest) => {
        rememberToken(token);
        if (typeof originalCallback === 'function') return originalCallback(token, ...rest);
      };
      const widgetId = originalRender(container, options);
      if (widgetId !== undefined && widgetId !== null) {
        copied.widgetId = String(widgetId);
        window.__freebeatTurnstile.widgets.push(widgetId);
      }
      return widgetId;
    };
    if (originalExecute) {
      ts.execute = (...args) => {
        const result = originalExecute(...args);
        if (result && typeof result.then === 'function') {
          result.then(rememberToken).catch(() => {});
        } else {
          rememberToken(result);
        }
        return result;
      };
    }
    if (originalGetResponse) {
      ts.getResponse = (...args) => {
        const result = originalGetResponse(...args);
        rememberToken(result);
        return result;
      };
    }
    ts.__freebeatHooked = true;
  };
  install();
  setInterval(install, 100);
})();
        """
    )


def _click_matching(page, patterns: list[str], reject_patterns: list[str] | None = None) -> dict[str, Any]:
    return page.evaluate(
        """
(args) => {
  const patterns = args.patterns || [];
  const rejectPatterns = args.rejectPatterns || [];
  if (!/(^|\\.)freebeat\\.ai$/i.test(window.location.hostname || '')) {
    return { ok: false, external: true, url: window.location.href };
  }
  const re = new RegExp(patterns.join('|'), 'i');
  const rejectRe = rejectPatterns.length ? new RegExp(rejectPatterns.join('|'), 'i') : null;
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const nodes = Array.from(document.querySelectorAll('button,a,[role="button"],div[role="button"]'));
  for (const el of nodes) {
    if (!visible(el)) continue;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    const text = [
      el.innerText,
      el.textContent,
      el.getAttribute('aria-label'),
      el.getAttribute('title'),
      el.getAttribute('href')
    ].filter(Boolean).join(' ').trim();
    if (!text || !re.test(text)) continue;
    if (rejectRe && rejectRe.test(text)) continue;
    el.click();
    return { ok: true, text: text.slice(0, 120), tag: el.tagName };
  }
  return { ok: false };
}
        """,
        {"patterns": patterns, "rejectPatterns": reject_patterns or []},
    )


def _click_login_entry(page, *, humanize: bool = True) -> dict[str, Any]:
    if not _is_freebeat_page_url(page.url):
        return {"ok": False, "external": True, "url": page.url}
    for text in ("Login", "Log in", "Sign in"):
        try:
            locator = _first_locator(page.get_by_text(text, exact=True))
            if locator.count() <= 0 or not locator.is_visible(timeout=1000):
                continue
            _human_click_locator(page, locator, humanize=humanize)
            return {"ok": True, "native": True, "text": text, "tag": "TEXT"}
        except Exception:
            continue
    return page.evaluate(
        """
() => {
  if (!/(^|\\.)freebeat\\.ai$/i.test(window.location.hostname || '')) {
    return { ok: false, external: true, url: window.location.href };
  }
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const nodes = Array.from(document.querySelectorAll('button,a,[role="button"],div'));
  for (const el of nodes) {
    if (!visible(el)) continue;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim();
    const href = el.getAttribute('href') || '';
    const joined = `${text} ${href}`.toLowerCase();
    if (joined.includes('google') || joined.includes('oauth') || joined.includes('apple') || joined.includes('facebook')) {
      continue;
    }
    if (/^login$/i.test(text) || /^log\\s*in$/i.test(text) || /^sign\\s*in$/i.test(text)) {
      el.click();
      return { ok: true, text: text.slice(0, 120), tag: el.tagName };
    }
  }
  return { ok: false };
}
        """
    )


def _fill_email(page, email: str, *, humanize: bool = True) -> dict[str, Any]:
    if not _is_freebeat_page_url(page.url):
        return {"ok": False, "external": True, "url": page.url}
    for selector in (
        'input[placeholder="Continue with your email"]',
        'input[type="email"]',
    ):
        try:
            locator = _first_locator(page.locator(selector))
            if locator.count() <= 0 or not locator.is_visible(timeout=1000):
                continue
            _human_fill_locator(page, locator, email, humanize=humanize)
            return {"ok": True, "native": True, "selector": selector}
        except Exception:
            continue
    return page.evaluate(
        """
(email) => {
  if (!/(^|\\.)freebeat\\.ai$/i.test(window.location.hostname || '')) {
    return { ok: false, external: true, url: window.location.href };
  }
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const scoreInput = (el) => {
    const text = [
      el.type,
      el.name,
      el.id,
      el.placeholder,
      el.getAttribute('aria-label'),
      el.autocomplete
    ].filter(Boolean).join(' ').toLowerCase();
    if (/email|mail|account|username|login/.test(text)) return 10;
    return 0;
  };
  const exact = Array.from(document.querySelectorAll('input[placeholder="Continue with your email"], input[type="email"]'))
    .find((el) => visible(el) && !el.disabled && !el.readOnly);
  if (exact) {
    exact.focus();
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    if (setter) setter.call(exact, email);
    else exact.value = email;
    exact.dispatchEvent(new Event('input', { bubbles: true }));
    exact.dispatchEvent(new Event('change', { bubbles: true }));
    return {
      ok: true,
      exact: true,
      type: exact.type || '',
      name: exact.name || '',
      placeholder: exact.placeholder || '',
      id: exact.id || ''
    };
  }
  const candidates = Array.from(document.querySelectorAll('input'))
    .filter((el) => visible(el) && !el.disabled && !el.readOnly)
    .map((el) => ({ el, score: scoreInput(el) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);
  if (!candidates.length) return { ok: false };
  const input = candidates[0].el;
  input.focus();
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
  if (setter) setter.call(input, email);
  else input.value = email;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return {
    ok: true,
    type: input.type || '',
    name: input.name || '',
    placeholder: input.placeholder || '',
    id: input.id || ''
  };
}
        """,
        email,
    ) 


def _click_email_submit(page, *, humanize: bool = True) -> dict[str, Any]:
    if not _is_freebeat_page_url(page.url):
        return {"ok": False, "external": True, "url": page.url}
    for selector in (
        'button[aria-label="Send login code"]',
        'input[placeholder="Continue with your email"] ~ button',
        'input[type="email"] ~ button',
    ):
        try:
            locator = _first_locator(page.locator(selector))
            if locator.count() <= 0 or not locator.is_visible(timeout=1000):
                continue
            text = ""
            try:
                text = str(locator.inner_text(timeout=1000) or "")
            except Exception:
                pass
            label = ""
            try:
                label = str(locator.get_attribute("aria-label", timeout=1000) or "")
            except Exception:
                pass
            lower = f"{text} {label}".lower()
            if any(token in lower for token in ("forgot", "google", "oauth", "apple", "facebook")):
                continue
            _human_click_locator(page, locator, humanize=humanize)
            return {
                "ok": True,
                "native": True,
                "selector": selector,
                "text": (text or label)[:120],
                "tag": "BUTTON",
            }
        except Exception:
            continue
    return page.evaluate(
        """
() => {
  if (!/(^|\\.)freebeat\\.ai$/i.test(window.location.hostname || '')) {
    return { ok: false, external: true, url: window.location.href };
  }
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const candidates = [
    ...Array.from(document.querySelectorAll('button[aria-label="Send login code"]')),
    ...Array.from(document.querySelectorAll('input[placeholder="Continue with your email"] ~ button')),
    ...Array.from(document.querySelectorAll('input[type="email"] ~ button')),
  ];
  for (const button of candidates) {
    if (!visible(button)) continue;
    if (button.disabled || button.getAttribute('aria-disabled') === 'true') continue;
    button.click();
    return {
      ok: true,
      exact: true,
      text: (button.innerText || button.textContent || button.getAttribute('aria-label') || '').trim().slice(0, 120),
      tag: button.tagName
    };
  }
  return { ok: false };
}
        """
    )


def _dismiss_popups(page) -> None:
    for patterns in (
        ["accept", "agree", "ok", "got it", "allow"],
        ["close", "skip", "later", "no thanks"],
    ):
        try:
            _click_matching(page, patterns)
        except Exception:
            pass


def _api_response_ok(payload: dict[str, Any]) -> dict[str, Any]:
    if _is_send_code_already_sent(payload):
        return payload
    return _validate_api_payload(payload, label="sendEmailVerifyCodeV2")


def _is_send_code_verification_failed(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    code = payload.get("code")
    msg = str(payload.get("msg") or payload.get("message") or payload.get("raw") or "").lower()
    return str(code) in {"-1", "1"} and (
        "verification failed" in msg
        or "refresh and try again" in msg
        or "turnstile" in msg
        or "captcha" in msg
    )


def _is_transient_navigation_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "execution context was destroyed" in message
        or "most likely because of a navigation" in message
        or "navigation" in message and "interrupted" in message
    )


def _is_retryable_network_error(exc: Exception) -> bool:
    message = str(exc)
    return any(
        marker in message
        for marker in (
            "ERR_CONNECTION_CLOSED",
            "ERR_CONNECTION_RESET",
            "ERR_CONNECTION_ABORTED",
            "ERR_TIMED_OUT",
            "net::ERR_HTTP2_PROTOCOL_ERROR",
        )
    )


def _goto_with_retries(
    page,
    url: str,
    *,
    timeout: int,
    attempts: int = 3,
    log_fn: Callable[[str], None] = print,
) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts or not _is_retryable_network_error(exc):
                raise
            log_fn(f"Freebeat browser goto retry {attempt + 1}/{attempts} after {str(exc).splitlines()[0]}")
            try:
                page.wait_for_timeout(900 * attempt)
            except Exception:
                pass
    if last_exc:
        raise last_exc


def _wait_after_navigation(page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    try:
        page.wait_for_timeout(750)
    except Exception:
        pass


def _page_send_code_diagnostics(page) -> dict[str, Any]:
    if not _is_freebeat_page_url(page.url):
        return {"external": True, "url": page.url}
    return page.evaluate(
        """
() => {
  const visible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const textOf = (el) => (el ? String(el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim() : '');
  const emailInput = document.querySelector('input[type="email"]');
  const tokenInput = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
  const sendButton = document.querySelector('button[aria-label="Send login code"], input[type="email"] ~ button, button[type="submit"]');
  const resources = performance.getEntriesByType('resource')
    .map((item) => String(item.name || ''))
    .filter((url) => /turnstile|challenge|sendEmailVerify|api\\/proxy|cdn-cgi|cloudflare/i.test(url))
    .slice(-12);
  const bodyText = String(document.body ? document.body.innerText || '' : '');
  const lowerBodyText = bodyText.toLowerCase();
  const verificationFailed = lowerBodyText.includes('verification failed')
    || lowerBodyText.includes('please refresh and try again');
  const visibleErrors = [
    'Verification failed. Please refresh and try again.',
    'Verification failed',
    'Please refresh and try again.'
  ].filter((text) => bodyText.includes(text));
  return {
    url: window.location.href,
    title: document.title || '',
    webdriver: navigator.webdriver,
    hasTurnstile: !!window.turnstile,
    cfInputs: document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]').length,
    turnstileIframes: document.querySelectorAll('iframe[src*="turnstile"], iframe[src*="challenges.cloudflare"]').length,
    cfTokenLength: tokenInput && tokenInput.value ? String(tokenInput.value).length : 0,
    sendingText: !!(document.body && /Sending login code/i.test(document.body.innerText || '')),
    verificationFailed,
    visibleErrors,
    fingerprint: {
      userAgent: navigator.userAgent || '',
      platform: navigator.platform || '',
      languages: Array.from(navigator.languages || []),
      language: navigator.language || '',
      webdriver: navigator.webdriver,
      plugins: navigator.plugins ? navigator.plugins.length : 0,
      hardwareConcurrency: navigator.hardwareConcurrency || 0,
      deviceMemory: navigator.deviceMemory || 0,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
      screen: {
        width: window.screen ? window.screen.width : 0,
        height: window.screen ? window.screen.height : 0,
        availWidth: window.screen ? window.screen.availWidth : 0,
        availHeight: window.screen ? window.screen.availHeight : 0,
        colorDepth: window.screen ? window.screen.colorDepth : 0
      }
    },
    emailInput: emailInput ? {
      visible: visible(emailInput),
      disabled: !!emailInput.disabled,
      readOnly: !!emailInput.readOnly,
      valueLength: String(emailInput.value || '').length,
      placeholder: emailInput.placeholder || ''
    } : null,
    sendButton: sendButton ? {
      visible: visible(sendButton),
      disabled: !!sendButton.disabled,
      ariaDisabled: sendButton.getAttribute('aria-disabled') || '',
      text: textOf(sendButton).slice(0, 120),
      tag: sendButton.tagName || ''
    } : null,
    challengeResources: resources
  };
}
        """
    )


def _turnstile_challenge_state(page) -> dict[str, Any]:
    state: dict[str, Any] = {"hasIframe": False, "visibleIframe": False, "signals": [], "frames": []}
    try:
        state = page.evaluate(
            """
() => {
  const visible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const bodyText = String(document.body ? document.body.innerText || '' : '').toLowerCase();
  const signals = [
    'confirm you are human',
    'verify you are human',
    'verifying you are human',
    'checking your browser',
    'security verification',
    'verification failed',
    'please refresh and try again'
  ].filter((item) => bodyText.includes(item));
  const iframes = Array.from(document.querySelectorAll('iframe'))
    .map((iframe) => {
      const rect = iframe.getBoundingClientRect();
      const src = iframe.src || iframe.getAttribute('src') || '';
      const cloudflare = /turnstile|challenges\\.cloudflare|cloudflare/i.test(src);
      return {
        src,
        cloudflare,
        visible: visible(iframe),
        width: rect.width,
        height: rect.height,
        x: rect.x,
        y: rect.y
      };
    })
    .filter((item) => item.cloudflare);
  return {
    hasIframe: iframes.length > 0,
    visibleIframe: iframes.some((item) => item.visible && item.width > 10 && item.height > 10),
    signals,
    iframes
  };
}
            """
        )
    except Exception:
        pass
    try:
        frames = [frame.url for frame in page.frames if "challenges.cloudflare.com" in str(frame.url or "")]
        if frames:
            state["hasIframe"] = True
            state["frames"] = frames[-5:]
    except Exception:
        pass
    return state


def _latest_turnstile_render(token_state: dict[str, Any]) -> dict[str, str]:
    renders = token_state.get("renders")
    if not isinstance(renders, list):
        return {}
    for item in reversed(renders):
        if not isinstance(item, dict):
            continue
        sitekey = str(item.get("sitekey") or "").strip()
        if not sitekey:
            continue
        return {
            "sitekey": sitekey,
            "action": str(item.get("action") or "").strip(),
            "cdata": str(item.get("cData") or item.get("cdata") or "").strip(),
            "pagedata": str(item.get("chlPageData") or item.get("pagedata") or "").strip(),
            "widgetId": str(item.get("widgetId") or "").strip(),
        }
    return {}


def _click_turnstile_verifier(page, *, humanize: bool = True, log_fn: Callable[[str], None] = print) -> dict[str, Any]:
    state = _turnstile_challenge_state(page)
    deadline = time.monotonic() + 12
    iframe_el = None
    while time.monotonic() < deadline and iframe_el is None:
        for selector in (
            'iframe[src*="challenges.cloudflare.com"]',
            'iframe[src*="turnstile"]',
            'iframe[title*="Cloudflare"]',
            'iframe[title*="challenge"]',
        ):
            try:
                for element in page.query_selector_all(selector):
                    box = element.bounding_box()
                    if box and box.get("width", 0) > 10 and box.get("height", 0) > 10:
                        iframe_el = element
                        break
                if iframe_el is not None:
                    break
            except Exception:
                continue
        if iframe_el is None:
            try:
                for frame in page.frames:
                    if "challenges.cloudflare.com" not in str(frame.url or ""):
                        continue
                    element = frame.frame_element()
                    box = element.bounding_box()
                    if box and box.get("width", 0) > 10 and box.get("height", 0) > 10:
                        iframe_el = element
                        break
            except Exception:
                pass
        if iframe_el is None:
            _human_pause(page, 400, 900)

    if iframe_el is None:
        return {"clicked": False, "reason": "no_visible_turnstile_iframe", "state": state}

    box = iframe_el.bounding_box()
    if not box:
        return {"clicked": False, "reason": "turnstile_iframe_no_box", "state": state}

    x = box["x"] + min(max(box["width"] * 0.18, 24), 46) + random.uniform(-4, 4)
    y = box["y"] + box["height"] * random.uniform(0.45, 0.58)
    try:
        if humanize:
            try:
                page.bring_to_front()
            except Exception:
                pass
            _human_pause(page, 500, 1600)
            _human_mouse_path(
                page,
                box["x"] + box["width"] * random.uniform(0.42, 0.76),
                box["y"] + box["height"] * random.uniform(0.18, 0.82),
            )
            _human_pause(page, 220, 780)
            _human_mouse_click(page, x, y)
        else:
            page.mouse.move(x, y, steps=1)
            page.mouse.down()
            _human_pause(page, 70, 180)
            page.mouse.up()
        _human_pause(page, 900, 1800)
        log_fn("Freebeat Turnstile verifier clicked")
        return {"clicked": True, "x": round(x, 2), "y": round(y, 2), "humanized": bool(humanize), "state": state}
    except Exception as exc:
        return {"clicked": False, "reason": str(exc), "state": state}


def _turnstile_state(page) -> dict[str, Any]:
    return page.evaluate(
        """
() => {
  const state = window.__freebeatTurnstile || {};
  const bodyText = String(document.body ? document.body.innerText || '' : '');
  const lowerBodyText = bodyText.toLowerCase();
  const verificationFailed = lowerBodyText.includes('verification failed')
    || lowerBodyText.includes('please refresh and try again');
  const visibleErrors = [
    'Verification failed. Please refresh and try again.',
    'Verification failed',
    'Please refresh and try again.'
  ].filter((text) => bodyText.includes(text));
  const tokens = Array.isArray(state.tokens) ? state.tokens.filter(Boolean).map(String).filter((token) => token.length > 20) : [];
  const widgets = Array.isArray(state.widgets) ? state.widgets : [];
  const inputTokens = Array.from(
    document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]')
  ).map((el) => String(el.value || '')).filter((token) => token.length > 20);
  const responseTokens = [];
  if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
    for (const widget of widgets) {
      try {
        const token = window.turnstile.getResponse(widget);
        if (token && String(token).length > 20) responseTokens.push(String(token));
      } catch (_) {}
    }
    try {
      const token = window.turnstile.getResponse();
      if (token && String(token).length > 20) responseTokens.push(String(token));
    } catch (_) {}
  }
  const allTokens = [...tokens, ...inputTokens, ...responseTokens].filter(Boolean);
  return {
    tokens,
    inputTokens,
    responseTokens,
    renders: Array.isArray(state.renders) ? state.renders : [],
    widgets: widgets.map(String),
    token: allTokens.length ? allTokens[allTokens.length - 1] : '',
    hasTurnstile: !!window.turnstile,
    verificationFailed,
    visibleErrors,
    url: window.location.href
  };
}
        """
    )


def _wait_for_turnstile_token(page, *, timeout_seconds: float = 18.0) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds or 18.0))
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        if not _is_freebeat_page_url(page.url):
            return {"token": "", "external": True, "url": page.url}
        try:
            state = _turnstile_state(page)
        except Exception as exc:
            if not _is_transient_navigation_error(exc):
                raise
            _wait_after_navigation(page)
            continue
        last_state = state if isinstance(state, dict) else {}
        if str(last_state.get("token") or "").strip():
            return last_state
        page.wait_for_timeout(750)
    return last_state


def _send_code_fetch_from_page(page, *, email: str, verify_source: str, turnstile_token: str = "") -> dict[str, Any]:
    return page.evaluate(
        """
async ({ path, email, verifySource, turnstileToken }) => {
  if (!/(^|\\.)freebeat\\.ai$/i.test(window.location.hostname || '')) {
    return { ok: false, reason: 'external_origin', url: window.location.href };
  }
  const state = window.__freebeatTurnstile || {};
  const tokens = Array.isArray(state.tokens) ? state.tokens.filter(Boolean).map(String).filter((token) => token.length > 20) : [];
  const inputTokens = Array.from(
    document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]')
  ).map((el) => String(el.value || '')).filter((token) => token.length > 20);
  const token = turnstileToken || (inputTokens.length ? inputTokens[inputTokens.length - 1] : '') || (tokens.length ? String(tokens[tokens.length - 1]) : '');
  if (!token) return { ok: false, reason: 'missing_turnstile_token' };
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'accept': '*/*',
      'content-type': 'application/json',
      'fb-language': 'en',
      'x-platform-type': 'web',
      'cache-control': 'no-cache',
      'pragma': 'no-cache',
      'priority': 'u=1, i'
    },
    body: JSON.stringify({
      email,
      verifySource,
      turnstileToken: token
    })
  });
  return {
    ok: true,
    status: response.status,
    text: await response.text(),
    turnstileToken: token
  };
}
        """,
        {
            "path": FREEBEAT_SEND_CODE_PATH,
            "email": email,
            "verifySource": verify_source,
            "turnstileToken": turnstile_token,
        },
    )


def send_email_verify_code_in_browser(
    email: str,
    *,
    proxy: str | None = None,
    log_fn: Callable[[str], None] = print,
    turnstile_solver: Callable[..., str] | None = None,
    frontend_path: str = "",
    verify_source: str = FREEBEAT_DEFAULT_VERIFY_SOURCE,
    headless: bool = True,
    browser_engine: str = FREEBEAT_BROWSER_ENGINE,
    browser_channel: str = "",
    browser_cdp_url: str = "",
    user_data_dir: str = "",
    stealth_enabled: bool = True,
    humanize: bool = True,
    turnstile_click_enabled: bool = True,
    turnstile_wait_seconds: float = 30.0,
    accept_language: str = FREEBEAT_BROWSER_ACCEPT_LANGUAGE,
    locale: str = FREEBEAT_BROWSER_LOCALE,
    timezone_id: str = FREEBEAT_BROWSER_TIMEZONE,
    user_agent: str = FREEBEAT_BROWSER_USER_AGENT,
    timeout_seconds: float = FREEBEAT_BROWSER_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    target_email = str(email or "").strip()
    if not target_email:
        raise RuntimeError("Freebeat browser email sender requires email")
    page_urls = _candidate_page_urls(frontend_path)
    timeout_ms = max(10_000, int(float(timeout_seconds or FREEBEAT_BROWSER_TIMEOUT_SECONDS) * 1000))
    request_record: dict[str, Any] = {}
    response_record: dict[str, Any] = {}
    request_failures: list[dict[str, Any]] = []
    console_events: list[dict[str, str]] = []

    playwright_context, resolved_browser_engine = _sync_playwright_context(browser_engine)

    with playwright_context as playwright:
        launch_options: dict[str, Any] = {
            "headless": bool(headless),
            "ignore_default_args": ["--enable-automation"],
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--lang=en-US",
                "--no-first-run",
                "--no-default-browser-check",
                "--password-store=basic",
            ],
        }
        channel = str(browser_channel or "").strip()
        cdp_url = str(browser_cdp_url or "").strip()
        if channel:
            launch_options["channel"] = channel
        proxy_options = _playwright_proxy(proxy)
        if proxy_options and not cdp_url:
            launch_options["proxy"] = proxy_options
        log_fn(
            "Freebeat browser engine="
            f"{resolved_browser_engine} stealth={'on' if stealth_enabled else 'off'} "
            f"humanize={'on' if humanize else 'off'} "
            f"turnstile_click={'on' if turnstile_click_enabled else 'off'} "
            f"solver={'on' if turnstile_solver else 'off'} "
            f"headless={'on' if headless else 'off'} "
            f"cdp={'on' if cdp_url else 'off'}"
        )
        context_options: dict[str, Any] = {
            "locale": str(locale or FREEBEAT_BROWSER_LOCALE),
            "timezone_id": str(timezone_id or FREEBEAT_BROWSER_TIMEZONE),
            "viewport": {"width": 1365, "height": 768},
            "screen": {"width": 1365, "height": 768},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
            "color_scheme": "light",
            "reduced_motion": "no-preference",
            "extra_http_headers": {
                "accept-language": str(accept_language or FREEBEAT_BROWSER_ACCEPT_LANGUAGE),
            },
        }
        ua = str(user_agent or "").strip()
        if ua and ua.lower() != "native":
            context_options["user_agent"] = ua
        browser = None
        context = None
        page = None
        close_context = True
        close_browser = True
        close_page = False
        try:
            profile_dir = str(user_data_dir or "").strip()
            if cdp_url:
                browser = playwright.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0] if browser.contexts else browser.new_context(**context_options)
                close_context = False
                close_browser = False
                close_page = True
                if proxy:
                    log_fn("Freebeat browser CDP mode uses the external browser proxy; launch proxy option is ignored")
            elif profile_dir:
                context = playwright.chromium.launch_persistent_context(
                    profile_dir,
                    **launch_options,
                    **context_options,
                )
                close_context = True
                close_browser = False
            else:
                browser = playwright.chromium.launch(**launch_options)
                context = browser.new_context(**context_options)
                close_context = True
                close_browser = True
            context.set_default_timeout(timeout_ms)
            page = context.new_page()
            if cdp_url:
                log_fn("Freebeat browser CDP mode skips init-script stealth/capture hooks")
            elif stealth_enabled:
                _install_stealth_evasions(page)
            if not cdp_url:
                _install_capture_hooks(page)

            def on_request(request) -> None:
                if FREEBEAT_SEND_CODE_PATH not in request.url:
                    return
                body_text = request.post_data or ""
                body = _parse_json_text(body_text)
                request_record.update(
                    {
                        "url": request.url,
                        "method": request.method,
                        "body": body,
                        "headers": dict(request.headers),
                    }
                )

            def on_response(response) -> None:
                if FREEBEAT_SEND_CODE_PATH not in response.url:
                    return
                text = ""
                try:
                    text = response.text()
                except Exception as exc:
                    text = _json_dumps({"error": str(exc)})
                response_record.update(
                    {
                        "url": response.url,
                        "status": response.status,
                        "body": _parse_json_text(text),
                        "text": text,
                    }
                )

            def on_request_failed(request) -> None:
                if not any(
                    marker in request.url
                    for marker in (FREEBEAT_SEND_CODE_PATH, "challenges.cloudflare.com", "/cdn-cgi/challenge")
                ):
                    return
                failure = request.failure or ""
                request_failures.append(
                    {
                        "url": request.url,
                        "method": request.method,
                        "failure": str(failure),
                    }
                )
                del request_failures[:-8]

            def on_console(message) -> None:
                text = str(getattr(message, "text", "") or "")
                if not text:
                    return
                if not any(marker in text.lower() for marker in ("turnstile", "challenge", "cloudflare", "failed", "error")):
                    return
                console_events.append({"type": str(getattr(message, "type", "") or ""), "text": text[:300]})
                del console_events[:-8]

            page.on("request", on_request)
            page.on("response", on_response)
            page.on("requestfailed", on_request_failed)
            page.on("console", on_console)
            open_patterns = ["log\\s*in", "login", "sign\\s*in"]
            open_reject_patterns = ["google", "accounts\\.google", "oauth", "apple", "facebook"]
            send_patterns = [
                "send",
                "code",
                "verify",
                "email",
                "next",
            ]
            send_reject_patterns = ["forgot", "google", "accounts\\.google", "oauth", "apple", "facebook"]
            last_action: dict[str, Any] = {}
            attempted_urls: list[str] = []
            per_url_timeout = max(15.0, (timeout_ms / 1000) / max(1, len(page_urls)))
            for page_url in page_urls:
                attempted_urls.append(page_url)
                log_fn(f"Freebeat browser send-code open {page_url}")
                _goto_with_retries(page, page_url, timeout=timeout_ms, log_fn=log_fn)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15_000))
                except Exception:
                    pass
                page.wait_for_timeout(1500)

                start = time.monotonic()
                while time.monotonic() - start < per_url_timeout:
                    if response_record:
                        break
                    if not _is_freebeat_page_url(page.url):
                        last_action = {"stage": "external_origin", "page_url": page_url, "current_url": page.url}
                        break
                    try:
                        _dismiss_popups(page)
                        filled = _fill_email(page, target_email, humanize=humanize)
                    except Exception as exc:
                        if not _is_transient_navigation_error(exc):
                            raise
                        last_action = {"error": str(exc), "stage": "fill_email", "page_url": page_url}
                        _wait_after_navigation(page)
                        continue
                    if filled.get("ok"):
                        try:
                            clicked = _click_email_submit(page, humanize=humanize)
                            if not clicked.get("ok"):
                                clicked = _click_matching(page, send_patterns, send_reject_patterns)
                        except Exception as exc:
                            if not _is_transient_navigation_error(exc):
                                raise
                            last_action = {"filled": filled, "error": str(exc), "stage": "click_send", "page_url": page_url}
                            _wait_after_navigation(page)
                            continue
                        last_action = {"filled": filled, "clicked": clicked, "page_url": page_url}
                        if not clicked.get("ok"):
                            try:
                                page.keyboard.press("Enter")
                                last_action["pressed_enter"] = True
                            except Exception:
                                pass
                        if response_record and _is_send_code_verification_failed(response_record.get("body")):
                            last_action["verification_failed_response"] = {
                                "status": response_record.get("status"),
                                "body": response_record.get("body"),
                                "source": response_record.get("source", "page_request"),
                            }
                            response_record.clear()
                        if not response_record:
                            for _ in range(8):
                                if response_record:
                                    break
                                page.wait_for_timeout(500)
                            if response_record and _is_send_code_verification_failed(response_record.get("body")):
                                last_action["verification_failed_response"] = {
                                    "status": response_record.get("status"),
                                    "body": response_record.get("body"),
                                    "source": response_record.get("source", "page_request"),
                                }
                                response_record.clear()
                            token_state: dict[str, Any] = {}
                            if not response_record:
                                turnstile_click: dict[str, Any] = {}
                                if turnstile_click_enabled:
                                    try:
                                        challenge_state = _turnstile_challenge_state(page)
                                        if challenge_state.get("hasIframe") or challenge_state.get("signals"):
                                            turnstile_click = _click_turnstile_verifier(
                                                page,
                                                humanize=humanize,
                                                log_fn=log_fn,
                                            )
                                            last_action["turnstile_click"] = turnstile_click
                                    except Exception as exc:
                                        if not _is_transient_navigation_error(exc):
                                            last_action["turnstile_click_error"] = str(exc)
                                try:
                                    token_state = _wait_for_turnstile_token(page, timeout_seconds=turnstile_wait_seconds)
                                except Exception as exc:
                                    if not _is_transient_navigation_error(exc):
                                        raise
                                    last_action = {
                                        "filled": filled,
                                        "clicked": clicked,
                                        "error": str(exc),
                                        "stage": "wait_turnstile_token",
                                        "page_url": page_url,
                                    }
                                    _wait_after_navigation(page)
                                    continue
                                last_action["turnstile"] = {
                                    "hasTurnstile": token_state.get("hasTurnstile"),
                                    "token": bool(str(token_state.get("token") or "").strip()),
                                    "renders": token_state.get("renders"),
                                    "widgets": token_state.get("widgets"),
                                    "inputTokens": len(token_state.get("inputTokens") or []),
                                    "responseTokens": len(token_state.get("responseTokens") or []),
                                    "verificationFailed": bool(token_state.get("verificationFailed")),
                                    "visibleErrors": token_state.get("visibleErrors"),
                                }
                                if not str(token_state.get("token") or "").strip() and turnstile_solver:
                                    render = _latest_turnstile_render(token_state)
                                    if render.get("sitekey"):
                                        solver_info = {
                                            "sitekey": render.get("sitekey"),
                                            "action": render.get("action"),
                                            "cdata": bool(render.get("cdata")),
                                            "pagedata": bool(render.get("pagedata")),
                                        }
                                        try:
                                            log_fn(
                                                "Freebeat Turnstile solver start "
                                                f"sitekey={render.get('sitekey')} action={render.get('action') or '-'} "
                                                f"cdata={'yes' if render.get('cdata') else 'no'}"
                                            )
                                            solved_token = str(
                                                turnstile_solver(
                                                    page.url,
                                                    render["sitekey"],
                                                    action=render.get("action") or "",
                                                    cdata=render.get("cdata") or "",
                                                    pagedata=render.get("pagedata") or "",
                                                    proxy=proxy or "",
                                                )
                                                or ""
                                            ).strip()
                                            solver_info["token"] = bool(solved_token)
                                            if solved_token:
                                                token_state["token"] = solved_token
                                                token_state.setdefault("tokens", []).append(solved_token)
                                                last_action["turnstile"]["token"] = True
                                                log_fn("Freebeat Turnstile solver returned token")
                                        except Exception as exc:
                                            solver_info["error"] = str(exc)
                                            log_fn(f"Freebeat Turnstile solver failed: {exc}")
                                        last_action["turnstile_solver"] = solver_info
                                try:
                                    last_action["diagnostics"] = _page_send_code_diagnostics(page)
                                except Exception as exc:
                                    if not _is_transient_navigation_error(exc):
                                        last_action["diagnostics_error"] = str(exc)
                                if request_failures:
                                    last_action["request_failures"] = list(request_failures)
                                if console_events:
                                    last_action["console"] = list(console_events)
                                if (
                                    not str(token_state.get("token") or "").strip()
                                    and (
                                        token_state.get("hasTurnstile")
                                        or last_action.get("turnstile_click")
                                        or (last_action.get("diagnostics") or {}).get("cfInputs")
                                    )
                                ):
                                    last_action["cf_verification_required"] = True
                                if (
                                    not str(token_state.get("token") or "").strip()
                                    and (
                                        token_state.get("verificationFailed")
                                        or (last_action.get("diagnostics") or {}).get("verificationFailed")
                                    )
                                ):
                                    last_action["cf_verification_failed"] = True
                                    last_action["stage"] = "cf_verification_failed"
                            if response_record:
                                break
                            if last_action.get("cf_verification_failed"):
                                break
                            try:
                                fetch_result = _send_code_fetch_from_page(
                                    page,
                                    email=target_email,
                                    verify_source=verify_source,
                                    turnstile_token=str(token_state.get("token") or "").strip(),
                                )
                            except Exception as exc:
                                if not _is_transient_navigation_error(exc):
                                    raise
                                last_action = {
                                    "filled": filled,
                                    "clicked": clicked,
                                    "error": str(exc),
                                    "stage": "fetch_fallback",
                                    "page_url": page_url,
                                }
                                _wait_after_navigation(page)
                                continue
                            last_action["browser_fetch_fallback"] = {
                                key: value
                                for key, value in dict(fetch_result or {}).items()
                                if key not in {"text"}
                            }
                            if fetch_result.get("ok"):
                                request_record.update(
                                    {
                                        "url": f"{FREEBEAT_BASE}{FREEBEAT_SEND_CODE_PATH}",
                                        "method": "POST",
                                        "body": {
                                            "email": target_email,
                                            "verifySource": verify_source,
                                            "turnstileToken": fetch_result.get("turnstileToken", ""),
                                        },
                                        "source": "browser_fetch_fallback",
                                    }
                                )
                                text = str(fetch_result.get("text") or "")
                                response_record.update(
                                    {
                                        "url": f"{FREEBEAT_BASE}{FREEBEAT_SEND_CODE_PATH}",
                                        "status": int(fetch_result.get("status") or 0),
                                        "body": _parse_json_text(text),
                                        "text": text,
                                        "source": "browser_fetch_fallback",
                                    }
                                )
                                break
                    if last_action.get("cf_verification_failed"):
                        break
                    else:
                        try:
                            clicked = _click_login_entry(page, humanize=humanize)
                            if not clicked.get("ok"):
                                clicked = _click_matching(page, open_patterns, open_reject_patterns)
                        except Exception as exc:
                            if not _is_transient_navigation_error(exc):
                                raise
                            last_action = {"filled": filled, "error": str(exc), "stage": "click_open", "page_url": page_url}
                            _wait_after_navigation(page)
                            continue
                        last_action = {"filled": filled, "clicked": clicked, "page_url": page_url}
                    page.wait_for_timeout(2500)
                if response_record:
                    break
                if last_action.get("cf_verification_failed"):
                    break

            if not response_record:
                title = ""
                try:
                    title = page.title()
                except Exception:
                    pass
                raise RuntimeError(
                    "Freebeat browser send-code did not capture sendEmailVerifyCodeV2 request "
                    f"within {timeout_ms // 1000}s; engine={resolved_browser_engine} "
                    f"channel={channel or 'default'} cdp={'on' if cdp_url else 'off'} "
                    f"stealth={'on' if stealth_enabled else 'off'} "
                    f"init_hooks={'off' if cdp_url else 'on'} "
                    f"humanize={'on' if humanize else 'off'} profile={'on' if str(user_data_dir or '').strip() else 'off'} "
                    f"turnstile_click={'on' if turnstile_click_enabled else 'off'} solver={'on' if turnstile_solver else 'off'} "
                    f"locale={context_options.get('locale')} timezone={context_options.get('timezone_id')} "
                    f"tried={attempted_urls} page={page.url} "
                    f"title={title!r} action={last_action}"
                )
            if int(response_record.get("status") or 0) != 200:
                raise RuntimeError(
                    f"Freebeat browser send-code failed: HTTP {response_record.get('status')} "
                    f"{str(response_record.get('text') or '')[:300]}"
                )
            payload = response_record.get("body") if isinstance(response_record.get("body"), dict) else {}
            payload = _api_response_ok(payload)
            cookies = context.cookies()
            cookie_header = _cookies_to_header(cookies)
            request_body = request_record.get("body") if isinstance(request_record.get("body"), dict) else {}
            turnstile_state = page.evaluate("() => window.__freebeatTurnstile || {}")
            return {
                "ok": True,
                "browser_sent": True,
                "email": target_email,
                "verify_source": verify_source,
                "payload": payload,
                "response": payload,
                "request": request_record,
                "cookie_header": cookie_header,
                "cookies": cookies,
                "turnstile_token": str(request_body.get("turnstileToken") or "").strip(),
                "turnstile": turnstile_state,
                "page_url": page.url,
                "browser_engine": resolved_browser_engine,
                "browser_channel": channel,
                "browser_cdp_url": cdp_url,
                "stealth_enabled": bool(stealth_enabled),
                "humanize": bool(humanize),
                "locale": context_options.get("locale"),
                "timezone_id": context_options.get("timezone_id"),
            }
        finally:
            if close_page and page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            if close_context and context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if close_browser and browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
