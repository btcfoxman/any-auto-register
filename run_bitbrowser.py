import requests
import json
import argparse
import sys
import re
import time
from playwright.sync_api import sync_playwright

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
START_X = 50  # 第一个窗口的 X 坐标
START_Y = 50  # 第一个窗口的 Y 坐标
OFFSET = 40  # 每个新窗口偏移的像素值
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800


# ==============================================

def update_browser_config(browser_id, proxy_type, host, port, pos_x, pos_y, user="", password=""):
    """
    更新浏览器配置（代理 + 窗口坐标）
    """
    detail_url = f"{BIT_API_URL}/browser/detail"
    update_url = f"{BIT_API_URL}/browser/update"

    # 1. 获取当前完整配置
    try:
        detail_response = requests.post(detail_url, json={"id": browser_id}, headers=HEADERS)
        detail_result = detail_response.json()

        if not detail_result.get('success'):
            print(f"  ❌ 获取详情失败: {detail_result.get('msg')}")
            return False
        browser_config = detail_result.get('data', {})
    except Exception as e:
        print(f"  ❌ 请求详情 API 失败: {e}")
        return False

    # 2. 修改代理配置
    browser_config["proxyMethod"] = 2
    browser_config["proxyType"] = proxy_type
    browser_config["host"] = host
    browser_config["port"] = port
    browser_config["proxyUserName"] = user
    browser_config["proxyPassword"] = password

    if "browserFingerPrint" not in browser_config or browser_config["browserFingerPrint"] is None:
        browser_config["browserFingerPrint"] = {}

    # 3. 动态注入窗口坐标和大小（修改 Chromium 原生启动参数）
    args_str = browser_config.get('args', '')
    if not args_str: args_str = ""

    # 使用正则清理可能已存在的旧位置、大小或最大化参数，防止冲突
    args_str = re.sub(r'--window-position=-?\d+,-?\d+', '', args_str)
    args_str = re.sub(r'--window-size=\d+,\d+', '', args_str)
    args_str = re.sub(r'--start-maximized', '', args_str)

    # 追加新的坐标和固定大小参数
    new_args = f"--window-position={pos_x},{pos_y} --window-size={WINDOW_WIDTH},{WINDOW_HEIGHT}"
    browser_config['args'] = f"{args_str} {new_args}".strip()

    # 4. 提交完整数据
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


def open_browser(browser_id):
    """启动浏览器"""
    url = f"{BIT_API_URL}/browser/open"
    try:
        response = requests.post(url, json={"id": browser_id}, headers=HEADERS)
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
    """解析命令行参数，支持多个短号输入"""
    parser = argparse.ArgumentParser(description="通过短号批量启动比特浏览器环境并错开窗口位置")
    parser.add_argument(
        'short_codes',
        nargs='+',  # "+" 表示接受一个或多个参数，存为列表
        help="环境短号列表 (例如: 3 4 5)"
    )
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_arguments()

    # 去重并保持顺序，防止用户手滑输入 "3 3"
    short_codes = list(dict.fromkeys(args.short_codes))

    print(f"🚀 准备启动的环境总数: {len(short_codes)} 个 ({', '.join(short_codes)})")
    print("=" * 50)

    # 复用一个 Playwright 实例进行快速验证
    with sync_playwright() as p:
        for idx, short_code in enumerate(short_codes):
            short_code = str(short_code)

            if short_code not in ENV_MAPPING:
                print(f"⚠️ 跳过: 未知的环境短号 '{short_code}'。")
                print("-" * 50)
                continue

            env_data = ENV_MAPPING[short_code]
            BROWSER_ID = env_data["id"]
            PROXY_PORT = env_data["port"]

            # 计算当前窗口的层叠坐标
            current_x = START_X + (idx * OFFSET)
            current_y = START_Y + (idx * OFFSET)

            print(f"▶️ 正在处理环境短号: [{short_code}]")

            # 更新配置（包含代理和坐标）
            if update_browser_config(BROWSER_ID, PROXY_TYPE, PROXY_HOST, PROXY_PORT, current_x, current_y, PROXY_USER,
                                     PROXY_PASS):

                # 启动浏览器
                browser_data = open_browser(BROWSER_ID)

                if browser_data:
                    ws_endpoint = browser_data.get('ws')
                    http_endpoint = browser_data.get('http')
                    cdp_url = ws_endpoint if ws_endpoint else f"http://{http_endpoint}"

                    print(f"  🔗 验证 CDP: {cdp_url}")
                    try:
                        # 验证连接后立即释放
                        browser = p.chromium.connect_over_cdp(cdp_url)
                        browser.disconnect()
                        print("  🎉 连接测试通过，释放控制权。")
                    except Exception as e:
                        print(f"  ❌ Playwright CDP 连接失败: {e}")

            print("-" * 50)
            # 为防止接口调用过于频繁，稍微停顿一下再启动下一个
            time.sleep(1.5)

    print("🏁 所有环境启动任务执行完毕！")