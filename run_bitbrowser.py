import requests
import json
import argparse
import sys
import re
import time
import os
from playwright.sync_api import sync_playwright

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ================= 基础配置区 =================
BIT_API_URL = "http://127.0.0.1:54345"
HEADERS = {'Content-Type': 'application/json'}

# 环境短号到 真实环境 ID 和 代理端口 的映射表
ENV_MAPPING = {
    "3": {"id": "c100ade220ea4ef5b8f37cdfb035539e", "port": 20003},
    "4": {"id": "9e567805001c493fb7bae305332d1c2a", "port": 20005},
    "5": {"id": "4c417fd9e5fa4cf085fac3e4eb02f7dd", "port": 20013},
    "6": {"id": "7a046e29b9964f41b0d023956c8d62e5", "port": 20014}
}

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
DEFAULT_PROXY_BYPASS = os.getenv(
    "BROWSER_PROXY_BYPASS",
    "localhost;127.0.0.1;::1;192.168.3.5;192.168.3.*;<local>",
)


# ==============================================

def build_launch_args(pos_x, pos_y, width, height, proxy_bypass=""):
    launch_args = [
        f"--window-position={int(pos_x)},{int(pos_y)}",
        f"--window-size={int(width)},{int(height)}",
    ]
    bypass = str(proxy_bypass or "").strip()
    if bypass:
        launch_args.append(f"--proxy-bypass-list={bypass}")
    return launch_args


def merge_chrome_args(args_str, launch_args):
    args_str = str(args_str or "")
    args_str = re.sub(r'--window-position=-?\d+,-?\d+', '', args_str)
    args_str = re.sub(r'--window-size=\d+,\d+', '', args_str)
    args_str = re.sub(r'--start-maximized', '', args_str)
    args_str = re.sub(r'--proxy-bypass-list=(?:"[^"]*"|\'[^\']*\'|\S+)', '', args_str)
    return f"{args_str} {' '.join(launch_args)}".strip()


def update_browser_config(browser_id, proxy_type, host, port, pos_x, pos_y, user="", password="", proxy_bypass=""):
    """更新浏览器配置（代理 + 窗口坐标）"""
    detail_url = f"{BIT_API_URL}/browser/detail"
    update_url = f"{BIT_API_URL}/browser/update"

    try:
        detail_response = requests.post(detail_url, json={"id": str(browser_id)}, headers=HEADERS)
        detail_result = detail_response.json()

        if not detail_result.get('success'):
            print(f"  ❌ 获取详情失败: {detail_result.get('msg')}")
            return False
        browser_config = detail_result.get('data', {})
    except Exception as e:
        print(f"  ❌ 请求详情 API 失败: {e}")
        return False

    # 严格类型转换，彻底杜绝隐式类型相加报错
    browser_config["proxyMethod"] = 2
    browser_config["proxyType"] = str(proxy_type)
    browser_config["host"] = str(host)
    browser_config["port"] = int(port)  # API 期望整型
    browser_config["proxyUserName"] = str(user)
    browser_config["proxyPassword"] = str(password)

    if "browserFingerPrint" not in browser_config or browser_config["browserFingerPrint"] is None:
        browser_config["browserFingerPrint"] = {}

    args_str = str(browser_config.get('args', ''))
    launch_args = build_launch_args(pos_x, pos_y, WINDOW_WIDTH, WINDOW_HEIGHT, proxy_bypass)
    browser_config['args'] = merge_chrome_args(args_str, launch_args)

    try:
        update_response = requests.post(update_url, json=browser_config, headers=HEADERS)
        update_result = update_response.json()

        if update_result.get('success'):
            print(f"  ✅ 配置更新成功! (端口: {port}, 坐标: {pos_x},{pos_y})")
            return True
        else:
            print(f"  ❌ 配置更新失败: {update_result.get('msg')}")
            return False
    except Exception as e:
        print(f"  ❌ 请求更新 API 失败: {e}")
        return False


def close_browser(browser_id):
    url = f"{BIT_API_URL}/browser/close"
    try:
        response = requests.post(url, json={"id": str(browser_id)}, headers=HEADERS)
        result = response.json()
        if result.get('success'):
            print("  ✅ 已先关闭浏览器环境。")
            return True
        print(f"  ⚠️ 关闭环境失败或环境原本未打开: {result.get('msg')}")
        return False
    except Exception as e:
        print(f"  ⚠️ 请求关闭 API 失败，继续启动: {e}")
        return False


def open_browser(browser_id, launch_args=None):
    url = f"{BIT_API_URL}/browser/open"
    try:
        payload = {"id": str(browser_id)}
        if launch_args:
            payload["args"] = list(launch_args)
        response = requests.post(url, json=payload, headers=HEADERS)
        result = response.json()
        if result.get('success'):
            print("  ✅ 浏览器进程启动成功！")
            return result['data']
        else:
            print(f"  ❌ 启动失败: {result.get('msg')}")
            return None
    except Exception as e:
        print(f"  ❌ 请求打开 API 失败: {e}")
        return None


def parse_arguments():
    parser = argparse.ArgumentParser(description="通过短号批量启动比特浏览器环境并错开窗口位置")
    parser.add_argument('short_codes', nargs='+', help="环境短号列表 (例如: 3 4 5)")
    parser.add_argument("--proxy-bypass", default=DEFAULT_PROXY_BYPASS, help="Proxy bypass list")
    parser.add_argument("--no-proxy-bypass", action="store_true", help="No proxy bypass")
    parser.add_argument("--stop-first", action="store_true", help="Close before open")
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_arguments()
    short_codes = list(dict.fromkeys(args.short_codes))

    print(f"🚀 准备启动的环境总数: {len(short_codes)} 个 ({', '.join(short_codes)})")
    proxy_bypass = "" if args.no_proxy_bypass else args.proxy_bypass
    print("=" * 50)

    with sync_playwright() as p:
        for idx, short_code in enumerate(short_codes):
            short_code = str(short_code)

            if short_code not in ENV_MAPPING:
                print(f"⚠️ 跳过: 未知的环境短号 '{short_code}'。")
                print("-" * 50)
                continue

            env_data = ENV_MAPPING[short_code]
            BROWSER_ID = str(env_data["id"])
            PROXY_PORT = int(env_data["port"])

            current_x = START_X + (idx * OFFSET)
            current_y = START_Y + (idx * OFFSET)

            print(f"▶️ 正在处理环境短号: [{short_code}]")
            launch_args = build_launch_args(current_x, current_y, WINDOW_WIDTH, WINDOW_HEIGHT, proxy_bypass)

            if args.stop_first:
                close_browser(BROWSER_ID)
                time.sleep(1.0)

            # 更新配置
            if update_browser_config(BROWSER_ID, PROXY_TYPE, PROXY_HOST, PROXY_PORT, current_x, current_y, PROXY_USER,
                                     PROXY_PASS, proxy_bypass=proxy_bypass):

                # 启动浏览器
                browser_data = open_browser(BROWSER_ID, launch_args=launch_args)

                if browser_data:
                    ws_endpoint = browser_data.get('ws')
                    http_endpoint = browser_data.get('http')
                    cdp_url = ws_endpoint if ws_endpoint else f"http://{http_endpoint}"

                    print(f"  🔗 验证 CDP 并尝试注入扩展参数: {cdp_url}")
                    try:
                        # 1. 连接到浏览器
                        browser = p.chromium.connect_over_cdp(cdp_url)
                        context = browser.contexts[0]

                        # 数据准备
                        inject_data = {
                            "env_code": str(short_code),
                            "envCode": str(short_code),
                            "proxy_port": str(PROXY_PORT),
                            "proxy_url": "socks5://xray:" + str(PROXY_PORT),
                            "proxyUrl": "socks5://xray:" + str(PROXY_PORT),
                            "api_key": "Zw27426048ai!",
                            "apiKey": "Zw27426048ai!"
                        }

                        # 2. 【核心突破】利用 Playwright 官方公开 API 获取当前环境中真实的扩展 ID 列表
                        # 完美避开了硬编码和跨会话协议通信的问题
                        background_pages = [page for page in context.background_pages]
                        service_workers = [worker for worker in context.service_workers]

                        ext_id = None
                        # 尝试从 Service Worker 或 Background Page 的 URL 中提取随机生成的 Extension ID
                        if service_workers:
                            match = re.search(r"chrome-extension://([^/]+)", service_workers[0].url)
                            if match: ext_id = match.group(1)
                        if not ext_id and background_pages:
                            match = re.search(r"chrome-extension://([^/]+)", background_pages[0].url)
                            if match: ext_id = match.group(1)

                        if ext_id:
                            print(f"  🎯 成功识别到当前环境中的扩展 ID: {ext_id}")

                            # 3. 创建一个隐藏的特权页面直接访问该扩展的资源
                            # 只要在这个域下，Playwright 的 page.evaluate 就能无缝调用 chrome 扩展 API
                            privilege_page = context.new_page()
                            privilege_page.goto(f"chrome-extension://{ext_id}/manifest.json")

                            # 4. 直接原生注入，Playwright 会自动帮我们处理 arguments 传参，极度安全稳定
                            res = privilege_page.evaluate("""(data) => {
                                                    return new Promise((resolve) => {
                                                        if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
                                                            chrome.storage.local.set(data, () => {
                                                                chrome.storage.local.get(['env_code', 'envCode', 'proxy_url', 'proxyUrl', 'api_key', 'apiKey'], (stored) => {
                                                                    resolve({
                                                                        ok: true,
                                                                        env_code: stored.env_code || stored.envCode || '',
                                                                        proxy_url: stored.proxy_url || stored.proxyUrl || '',
                                                                        api_key_written: Boolean(stored.api_key || stored.apiKey)
                                                                    });
                                                                });
                                                            });
                                                        } else {
                                                            resolve("❌ 特权页面未检测到 chrome.storage API");
                                                        }
                                                    });
                                                }""", inject_data)

                            print(f"  注入结果: {res}")
                            privilege_page.close()  # 随手关闭，不留痕迹
                        else:
                            # 5. 兜底方案：如果没能识别到扩展 ID，则在当前活动的用户网页上下文中尝试注入
                            print("  ⚠️ 未能自动获取扩展 ID，尝试在活动页面注入...")
                            page = context.pages[0]
                            res = page.evaluate("""(data) => {
                                if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
                                    chrome.storage.local.set(data);
                                    return "✅ 页面上下文注入成功";
                                }
                                return "❌ 页面上下文无 chrome.storage 特权";
                            }""", inject_data)
                            print(f"  注入结果: {res}")
                            browser.close()
                            print("  🎉 流程顺利结束。")
                    except Exception as e:
                        print(f"  ❌ Playwright 操作失败: {e}")

            print("-" * 50)
            time.sleep(1.5)

    print("🏁 所有环境启动任务执行完毕！")
