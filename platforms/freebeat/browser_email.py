"""Browser-assisted Freebeat email verification sender."""
from __future__ import annotations

import json
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
FREEBEAT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
FREEBEAT_BROWSER_TIMEOUT_SECONDS = 120


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


def _install_capture_hooks(page) -> None:
    page.add_init_script(
        """
(() => {
  window.__freebeatTurnstile = window.__freebeatTurnstile || { tokens: [], renders: [] };
  const install = () => {
    const ts = window.turnstile;
    if (!ts || ts.__freebeatHooked || typeof ts.render !== 'function') return;
    const originalRender = ts.render.bind(ts);
    ts.render = (container, options = {}) => {
      const copied = {};
      for (const key of ['sitekey', 'action', 'cData', 'chlPageData']) {
        if (options && options[key]) copied[key] = String(options[key]);
      }
      window.__freebeatTurnstile.renders.push(copied);
      const originalCallback = options.callback;
      options.callback = (token, ...rest) => {
        if (token) window.__freebeatTurnstile.tokens.push(String(token));
        if (typeof originalCallback === 'function') return originalCallback(token, ...rest);
      };
      return originalRender(container, options);
    };
    ts.__freebeatHooked = true;
  };
  install();
  let current = window.turnstile;
  try {
    Object.defineProperty(window, 'turnstile', {
      configurable: true,
      get() { return current; },
      set(value) {
        current = value;
        setTimeout(install, 0);
      }
    });
  } catch (_) {}
  setInterval(install, 300);
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


def _click_login_entry(page) -> dict[str, Any]:
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


def _fill_email(page, email: str) -> dict[str, Any]:
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
    if (el.tagName === 'INPUT' && ['text', 'email', 'search', ''].includes((el.type || '').toLowerCase())) return 1;
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


def _click_email_submit(page) -> dict[str, Any]:
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


def _is_transient_navigation_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "execution context was destroyed" in message
        or "most likely because of a navigation" in message
        or "navigation" in message and "interrupted" in message
    )


def _wait_after_navigation(page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    try:
        page.wait_for_timeout(750)
    except Exception:
        pass


def _send_code_fetch_from_page(page, *, email: str, verify_source: str) -> dict[str, Any]:
    return page.evaluate(
        """
async ({ path, email, verifySource }) => {
  if (!/(^|\\.)freebeat\\.ai$/i.test(window.location.hostname || '')) {
    return { ok: false, reason: 'external_origin', url: window.location.href };
  }
  const state = window.__freebeatTurnstile || {};
  const tokens = Array.isArray(state.tokens) ? state.tokens.filter(Boolean) : [];
  const token = tokens.length ? String(tokens[tokens.length - 1]) : '';
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
        },
    )


def send_email_verify_code_in_browser(
    email: str,
    *,
    proxy: str | None = None,
    log_fn: Callable[[str], None] = print,
    frontend_path: str = "",
    verify_source: str = FREEBEAT_DEFAULT_VERIFY_SOURCE,
    headless: bool = True,
    timeout_seconds: float = FREEBEAT_BROWSER_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    target_email = str(email or "").strip()
    if not target_email:
        raise RuntimeError("Freebeat browser email sender requires email")
    page_urls = _candidate_page_urls(frontend_path)
    timeout_ms = max(10_000, int(float(timeout_seconds or FREEBEAT_BROWSER_TIMEOUT_SECONDS) * 1000))
    request_record: dict[str, Any] = {}
    response_record: dict[str, Any] = {}

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        launch_options: dict[str, Any] = {
            "headless": bool(headless),
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--lang=en-US",
            ],
        }
        proxy_options = _playwright_proxy(proxy)
        if proxy_options:
            launch_options["proxy"] = proxy_options
        browser = playwright.chromium.launch(**launch_options)
        try:
            context = browser.new_context(
                locale="en-US",
                timezone_id="America/New_York",
                viewport={"width": 1365, "height": 768},
                user_agent=FREEBEAT_BROWSER_USER_AGENT,
                extra_http_headers={
                    "accept-language": FREEBEAT_BROWSER_ACCEPT_LANGUAGE,
                    "fb-language": "en",
                    "x-platform-type": "web",
                },
            )
            context.set_default_timeout(timeout_ms)
            page = context.new_page()
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

            page.on("request", on_request)
            page.on("response", on_response)
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
                page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
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
                        filled = _fill_email(page, target_email)
                    except Exception as exc:
                        if not _is_transient_navigation_error(exc):
                            raise
                        last_action = {"error": str(exc), "stage": "fill_email", "page_url": page_url}
                        _wait_after_navigation(page)
                        continue
                    if filled.get("ok"):
                        try:
                            clicked = _click_email_submit(page)
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
                        if not response_record:
                            try:
                                fetch_result = _send_code_fetch_from_page(
                                    page,
                                    email=target_email,
                                    verify_source=verify_source,
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
                    else:
                        try:
                            clicked = _click_login_entry(page)
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

            if not response_record:
                title = ""
                try:
                    title = page.title()
                except Exception:
                    pass
                raise RuntimeError(
                    "Freebeat browser send-code did not capture sendEmailVerifyCodeV2 request "
                    f"within {timeout_ms // 1000}s; tried={attempted_urls} page={page.url} "
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
            }
        finally:
            browser.close()
