# -*- coding: utf-8 -*-
"""
run_adspower.py

参考 run_bitbrowser.py 的用法，通过短号批量启动 AdsPower 环境窗口：
    python run_adspower.py 3 4 5

准备工作：
1. 先打开 AdsPower 客户端，并确认 Local API 已开启。
2. 修改下方 ENV_MAPPING：把短号映射到 AdsPower 的 profile_id / user_id，以及你的本地代理端口。
3. 默认 AdsPower Local API 地址为 http://127.0.0.1:50325
   如端口不同，可改 ADSPOWER_API_URL，或运行前设置环境变量：
      set ADSPOWER_API_URL=http://127.0.0.1:50325

可选：
    python run_adspower.py 3 4 --stop-first
    python run_adspower.py 3 4 --no-proxy-update
    python run_adspower.py 3 4 --no-cdp-check
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright


# ================= 基础配置区 =================

ADSPOWER_API_URL = os.getenv("ADSPOWER_API_URL", "http://127.0.0.1:50325").rstrip("/")
ADSPOWER_API_KEY = os.getenv("ADSPOWER_API_KEY", "")
REQUEST_TIMEOUT = int(os.getenv("ADSPOWER_API_TIMEOUT", "15"))
REQUEST_DELAY_SECONDS = float(os.getenv("ADSPOWER_API_REQUEST_INTERVAL_SECONDS", "1.2"))

# 如果是本地 Local API，通常不需要鉴权；远程 API 才建议开启。
ADSPOWER_API_USE_AUTH = os.getenv("ADSPOWER_API_USE_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}

HEADERS = {"Content-Type": "application/json"}

# 环境短号到 AdsPower 环境 ID 和代理端口的映射表
# 说明：
# - id：建议填写 AdsPower Local API 返回的 profile_id / user_id
# - port：你本地 v2rayN / 代理池为该环境分配的端口
#
# 如果你的 AdsPower 环境编号刚好就是 3、4、5、6，也可以先这样用；
# 否则请把 id 改成真实 profile_id / user_id。
ENV_MAPPING: Dict[str, Dict[str, Any]] = {

    "4": {"id": "kya11bb", "port": 20009},
    "5": {"id": "kyydguy", "port": 20020},
    "6": {"id": "k10ryn4a", "port": 20022},
}

PROXY_SOFT = "other"
PROXY_TYPE = "socks5"
PROXY_HOST = "127.0.0.1"
PROXY_USER = ""
PROXY_PASS = ""

# 窗口错开显示的初始设置
START_X = 50
START_Y = 50
OFFSET = 40
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800

# 启动后 CDP 连接测试超时
CDP_CONNECT_TIMEOUT_MS = 30000

# ==============================================


def ensure_local_no_proxy() -> None:
    """避免本机 AdsPower Local API 请求走系统代理。"""
    local_hosts = ["127.0.0.1", "localhost", "::1"]
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.getenv(key, "") or ""
        items = [item.strip() for item in raw.split(",") if item.strip()]
        for host in local_hosts:
            if host not in items:
                items.append(host)
        os.environ[key] = ",".join(items)


def normalize_api_url(api_url: str) -> str:
    base = str(api_url or "").strip().rstrip("/")
    for suffix in ("/api/v1", "/api/v2"):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base


def is_local_api_url(api_url: str) -> bool:
    try:
        parsed = urlparse(api_url if "://" in api_url else f"http://{api_url}")
    except Exception:
        return False
    return (parsed.hostname or "").strip().lower() in {"127.0.0.1", "localhost", "::1"}


def build_url(path: str) -> str:
    return f"{normalize_api_url(ADSPOWER_API_URL)}{path}"


def build_headers(include_auth: bool = True) -> Dict[str, str]:
    headers = dict(HEADERS)

    use_auth = ADSPOWER_API_USE_AUTH or (ADSPOWER_API_KEY and not is_local_api_url(ADSPOWER_API_URL))
    if include_auth and use_auth and ADSPOWER_API_KEY:
        raw = ADSPOWER_API_KEY.strip()
        bearer = raw if raw.lower().startswith("bearer ") else f"Bearer {raw}"
        headers["Authorization"] = bearer
        headers["X-API-KEY"] = raw[7:].strip() if raw.lower().startswith("bearer ") else raw

    return headers


def response_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload or "")
    for key in ("msg", "message", "error", "detail"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return str(payload)


def is_success_response(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    code = payload.get("code")
    if isinstance(code, str):
        code = code.strip()
    if code in {0, "0", 200, "200"}:
        return True
    if payload.get("success") is True:
        return True
    status = str(payload.get("status") or "").strip().lower()
    return status in {"ok", "success"}


def is_rate_limited_text(message: str) -> bool:
    text = str(message or "").lower()
    return "too many request per second" in text or "429" in text


def request_json(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None,
    include_auth: bool = True,
    retries: int = 2,
) -> Dict[str, Any]:
    """同步版 AdsPower 请求封装。"""
    session = requests.Session()
    session.trust_env = False

    url = build_url(path)
    headers = build_headers(include_auth=include_auth)

    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.request(
                method.upper(),
                url,
                params=params,
                json=json_data,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            try:
                payload = resp.json()
            except Exception:
                payload = {"code": resp.status_code, "msg": resp.text}

            if resp.status_code == 429 or is_rate_limited_text(response_message(payload)):
                if attempt < retries:
                    time.sleep(max(1.1, REQUEST_DELAY_SECONDS))
                    continue

            resp.raise_for_status()
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(max(1.1, REQUEST_DELAY_SECONDS))
                continue

    raise RuntimeError(f"{method.upper()} {path} 请求失败: {last_error}")


def request_success(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """要求 AdsPower 返回成功；如果远程鉴权异常，按 adspower_driver.py 的思路尝试无鉴权兜底。"""
    payload = request_json(method, path, params=params, json_data=json_data, include_auth=True)
    if is_success_response(payload):
        return payload

    message = response_message(payload)
    should_try_without_auth = bool(
        ADSPOWER_API_KEY
        and any(token in message.lower() for token in ("api key", "apikey", "unauthorized", "auth", "token", "forbidden"))
    )
    if should_try_without_auth:
        retry_payload = request_json(method, path, params=params, json_data=json_data, include_auth=False)
        if is_success_response(retry_payload):
            return retry_payload
        message = response_message(retry_payload)

    raise RuntimeError(message)


def extract_ws_endpoint(payload: Any) -> Optional[str]:
    """递归提取 AdsPower 返回中的 ws/cdp/playwright/puppeteer 地址。"""
    if payload is None:
        return None
    if isinstance(payload, str):
        return payload if payload.startswith(("ws://", "wss://")) else None
    if isinstance(payload, dict):
        for key in (
            "puppeteer",
            "playwright",
            "cdp",
            "devtools",
            "browserWSEndpoint",
            "browser_ws_endpoint",
            "ws",
            "wsEndpoint",
            "ws_endpoint",
            "debug_ws",
            "debugWs",
            "webSocketDebuggerUrl",
            "websocketDebuggerUrl",
        ):
            endpoint = extract_ws_endpoint(payload.get(key))
            if endpoint:
                return endpoint
        endpoint = extract_ws_endpoint(payload.get("data"))
        if endpoint:
            return endpoint
    if isinstance(payload, list):
        for item in payload:
            endpoint = extract_ws_endpoint(item)
            if endpoint:
                return endpoint
    return None


def extract_value(payload: Any, keys: List[str]) -> Optional[str]:
    if payload is None:
        return None
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        nested = extract_value(payload.get("data"), keys)
        if nested:
            return nested
    if isinstance(payload, list):
        for item in payload:
            nested = extract_value(item, keys)
            if nested:
                return nested
    return None


def extract_http_endpoint(payload: Any) -> Optional[str]:
    value = extract_value(
        payload,
        ["http", "httpEndpoint", "http_endpoint", "debug_http", "debugHttp", "debugging_address"],
    )
    if not value:
        return None
    value = value.strip()
    return value if value.startswith(("http://", "https://")) else f"http://{value}"


def try_get_debug_ws_endpoint(debug_port: Any, host: str = "127.0.0.1") -> Optional[str]:
    port = str(debug_port or "").strip()
    if not port.isdigit():
        return None

    session = requests.Session()
    session.trust_env = False

    for suffix in ("/json/version", "/json/list"):
        url = f"http://{host}:{port}{suffix}"
        try:
            resp = session.get(url, timeout=3)
            if resp.status_code >= 400:
                continue
            ws = extract_ws_endpoint(resp.json())
            if ws:
                return ws
        except Exception:
            continue

    return None


def resolve_cdp_url(start_payload: Dict[str, Any]) -> Optional[str]:
    """优先 ws，其次 http，最后 debug_port 拼本地 CDP 地址。"""
    ws_endpoint = extract_ws_endpoint(start_payload)
    if ws_endpoint:
        return ws_endpoint

    http_endpoint = extract_http_endpoint(start_payload)
    if http_endpoint:
        return http_endpoint

    debug_port = extract_value(start_payload, ["debug_port", "debugPort", "port", "cdp_port"])
    if debug_port:
        ws = try_get_debug_ws_endpoint(debug_port)
        if ws:
            return ws
        return f"http://127.0.0.1:{debug_port}"

    return None


def build_user_proxy_config(proxy_type: str, host: str, port: Any, user: str = "", password: str = "") -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "proxy_soft": PROXY_SOFT,
        "proxy_type": proxy_type,
        "proxy_host": host,
        "proxy_port": str(port),
    }
    if user:
        config["proxy_user"] = user
    if password:
        config["proxy_password"] = password
    return config


def update_profile_proxy(profile_id: str, proxy_config: Dict[str, Any]) -> bool:
    """参考 adspower_driver.py：POST /api/v2/browser-profile/update 更新代理。"""
    body = {"profile_id": profile_id, "user_proxy_config": proxy_config}
    attempts = [
        (None, body),
        ({"profile_id": profile_id}, {"user_proxy_config": proxy_config}),
    ]

    last_error: Optional[Exception] = None
    for params, json_data in attempts:
        try:
            request_success("POST", "/api/v2/browser-profile/update", params=params, json_data=json_data)
            return True
        except Exception as exc:
            last_error = exc

    print(f"  ❌ AdsPower 代理更新失败: {last_error}")
    return False


def stop_profile(profile_id: str) -> None:
    """可选：先关闭环境，便于代理/窗口启动参数生效。"""
    attempts = [
        ("POST", "/api/v2/browser-profile/stop", None, {"profile_id": profile_id}),
        ("POST", "/api/v2/browser-profile/stop", None, {"user_id": profile_id}),
        ("GET", "/api/v1/browser/stop", {"user_id": profile_id}, None),
        ("GET", "/api/v1/browser/stop", {"id": profile_id}, None),
        ("POST", "/api/v1/browser/stop", None, {"user_id": profile_id}),
        ("POST", "/api/v1/browser/stop", None, {"id": profile_id}),
    ]
    for method, path, params, body in attempts:
        try:
            request_success(method, path, params=params, json_data=body)
            print("  ✅ 已先关闭环境。")
            return
        except Exception:
            continue
    print("  ⚠️ 关闭环境失败或环境原本未打开，继续启动。")


def start_profile(profile_id: str, launch_args: Optional[List[str]] = None, start_options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """参考 adspower_driver.py：v2 start 优先，v1 browser/start 兜底。"""
    v2_body: Dict[str, Any] = dict(start_options or {})
    v2_body["profile_id"] = profile_id
    if launch_args:
        # AdsPower 不同版本字段略有差异，这里两个都带上。
        v2_body["launch_args"] = launch_args
        v2_body["args"] = launch_args

    attempts = [
        ("POST", "/api/v2/browser-profile/start", None, v2_body),
        ("POST", "/api/v2/browser-profile/start", None, {"user_id": profile_id}),
        ("GET", "/api/v1/browser/start", {"user_id": profile_id}, None),
        ("GET", "/api/v1/browser/start", {"id": profile_id}, None),
        ("POST", "/api/v1/browser/start", None, {"user_id": profile_id}),
        ("POST", "/api/v1/browser/start", None, {"id": profile_id}),
    ]

    last_error: Optional[Exception] = None
    for method, path, params, body in attempts:
        try:
            return request_success(method, path, params=params, json_data=body)
        except Exception as exc:
            last_error = exc
            continue

    raise RuntimeError(f"AdsPower 启动失败: {last_error}")


def launch_args_for_window(pos_x: int, pos_y: int, width: int, height: int) -> List[str]:
    return [
        f"--window-position={pos_x},{pos_y}",
        f"--window-size={width},{height}",
    ]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量启动 AdsPower 环境并错开窗口位置")
    parser.add_argument("short_codes", nargs="+", help="环境短号列表，例如：3 4 5")

    parser.add_argument("--api-url", default=ADSPOWER_API_URL, help="AdsPower Local API 地址，默认读取 ADSPOWER_API_URL 或 127.0.0.1:50325")
    parser.add_argument("--api-key", default=ADSPOWER_API_KEY, help="AdsPower API Key，通常本地 Local API 不需要")
    parser.add_argument("--delay", type=float, default=1.5, help="每个环境之间的启动间隔秒数，默认 1.5")
    parser.add_argument("--stop-first", action="store_true", help="启动前先关闭环境，窗口坐标/代理更容易生效")
    parser.add_argument("--no-proxy-update", action="store_true", help="不更新 AdsPower 环境代理")
    parser.add_argument("--no-cdp-check", action="store_true", help="启动后不做 Playwright CDP 连接验证")

    parser.add_argument("--proxy-type", default=PROXY_TYPE, help="代理类型：http/socks5 等")
    parser.add_argument("--proxy-host", default=PROXY_HOST, help="代理主机")
    parser.add_argument("--proxy-user", default=PROXY_USER, help="代理用户名")
    parser.add_argument("--proxy-pass", default=PROXY_PASS, help="代理密码")

    parser.add_argument("--start-x", type=int, default=START_X)
    parser.add_argument("--start-y", type=int, default=START_Y)
    parser.add_argument("--offset", type=int, default=OFFSET)
    parser.add_argument("--width", type=int, default=WINDOW_WIDTH)
    parser.add_argument("--height", type=int, default=WINDOW_HEIGHT)

    return parser.parse_args()


def main() -> None:
    global ADSPOWER_API_URL, ADSPOWER_API_KEY

    ensure_local_no_proxy()
    args = parse_arguments()

    ADSPOWER_API_URL = args.api_url.rstrip("/")
    ADSPOWER_API_KEY = args.api_key

    short_codes = list(dict.fromkeys(str(code) for code in args.short_codes))

    print(f"🚀 准备启动 AdsPower 环境总数: {len(short_codes)} 个 ({', '.join(short_codes)})")
    print(f"🔌 AdsPower API: {normalize_api_url(ADSPOWER_API_URL)}")
    print("=" * 60)

    playwright_ctx = sync_playwright().start() if not args.no_cdp_check else None

    try:
        for idx, short_code in enumerate(short_codes):
            if short_code not in ENV_MAPPING:
                print(f"⚠️ 跳过: 未知的环境短号 '{short_code}'，请先在 ENV_MAPPING 里配置。")
                print("-" * 60)
                continue

            env_data = ENV_MAPPING[short_code]
            profile_id = str(env_data.get("id") or "").strip()
            proxy_port = env_data.get("port")
            profile_no = env_data.get("profile_no")

            if not profile_id:
                print(f"⚠️ 跳过: 环境短号 '{short_code}' 未配置 id。")
                print("-" * 60)
                continue

            pos_x = args.start_x + idx * args.offset
            pos_y = args.start_y + idx * args.offset

            print(f"▶️ 正在处理 AdsPower 环境短号: [{short_code}] profile_id/user_id={profile_id}")

            if args.stop_first:
                stop_profile(profile_id)
                time.sleep(1.0)

            if not args.no_proxy_update:
                if proxy_port in (None, ""):
                    print("  ⚠️ 未配置代理端口，跳过代理更新。")
                else:
                    proxy_config = build_user_proxy_config(
                        args.proxy_type,
                        args.proxy_host,
                        proxy_port,
                        args.proxy_user,
                        args.proxy_pass,
                    )
                    if update_profile_proxy(profile_id, proxy_config):
                        print(f"  ✅ 代理更新成功! ({args.proxy_type}://{args.proxy_host}:{proxy_port})")
                    else:
                        print("  ⏭️ 代理更新失败，跳过该环境启动。")
                        print("-" * 60)
                        continue

            launch_args = launch_args_for_window(pos_x, pos_y, args.width, args.height)
            start_options: Dict[str, Any] = {}
            if profile_no:
                start_options["profile_no"] = profile_no

            try:
                start_payload = start_profile(profile_id, launch_args=launch_args, start_options=start_options)
                print(f"  ✅ 浏览器进程启动成功! (坐标: {pos_x},{pos_y}, 大小: {args.width}x{args.height})")
            except Exception as exc:
                print(f"  ❌ 启动失败: {exc}")
                print("-" * 60)
                time.sleep(args.delay)
                continue

            cdp_url = resolve_cdp_url(start_payload)
            if not cdp_url:
                print("  ⚠️ 启动成功，但未解析到 CDP 地址。")
                print("-" * 60)
                time.sleep(args.delay)
                continue

            print(f"  🔗 CDP: {cdp_url}")

            if playwright_ctx is not None:
                try:
                    browser = playwright_ctx.chromium.connect_over_cdp(
                        cdp_url,
                        timeout=CDP_CONNECT_TIMEOUT_MS,
                    )
                    if hasattr(browser, "disconnect"):
                        browser.disconnect()
                    else:
                        browser.close()
                    print("  🎉 Playwright CDP 连接测试通过，已释放控制权。")
                except Exception as exc:
                    print(f"  ❌ Playwright CDP 连接失败: {exc}")

            print("-" * 60)
            time.sleep(args.delay)

    finally:
        if playwright_ctx is not None:
            playwright_ctx.stop()

    print("🏁 所有 AdsPower 环境启动任务执行完毕！")


if __name__ == "__main__":
    main()
