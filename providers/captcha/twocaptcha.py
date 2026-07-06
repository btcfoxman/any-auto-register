"""2Captcha — cloud Turnstile solver."""
from urllib.parse import urlparse

from core.base_captcha import BaseCaptcha
from providers.registry import register_provider


@register_provider("captcha", "twocaptcha_api")
class TwoCaptcha(BaseCaptcha):
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.api = "https://2captcha.com"

    @classmethod
    def from_config(cls, config: dict) -> 'TwoCaptcha':
        api_key = str(config.get("twocaptcha_key", "") or "")
        if not api_key:
            raise RuntimeError("2Captcha Key 未配置")
        return cls(api_key)

    def solve_turnstile(
        self,
        page_url: str,
        site_key: str,
        *,
        action: str = "",
        cdata: str = "",
        pagedata: str = "",
        proxy: str = "",
    ) -> str:
        import time
        import requests

        task = {
            "type": "TurnstileTaskProxyless",
            "websiteURL": page_url,
            "websiteKey": site_key,
        }
        if action:
            task["action"] = str(action)
        if cdata:
            task["data"] = str(cdata)
        if pagedata:
            task["pagedata"] = str(pagedata)
        proxy_fields = _turnstile_proxy_fields(proxy)
        if proxy_fields:
            task.update(proxy_fields)
            task["type"] = "TurnstileTask"

        create = requests.post(
            "https://api.2captcha.com/createTask",
            json={"clientKey": self.api_key, "task": task},
            timeout=30,
        )
        create.raise_for_status()
        payload = create.json()
        if payload.get("errorId"):
            raise RuntimeError(f"2Captcha 创建任务失败: {payload}")
        task_id = payload.get("taskId")
        if not task_id:
            raise RuntimeError(f"2Captcha 未返回任务 ID: {payload}")

        for _ in range(60):
            time.sleep(3)
            result = requests.post(
                "https://api.2captcha.com/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
                timeout=30,
            )
            result.raise_for_status()
            data = result.json()
            if data.get("errorId"):
                raise RuntimeError(f"2Captcha 错误: {data}")
            if data.get("status") == "ready":
                solution = data.get("solution") or {}
                token = str(solution.get("token") or "")
                if token:
                    return token
                raise RuntimeError(f"2Captcha 未返回 token: {data}")
        raise TimeoutError("2Captcha Turnstile 超时")

    def solve_recaptcha(self, page_url: str, site_key: str, *, enterprise: bool = False, action: str = "") -> str:
        import time
        import requests

        data = {
            "key": self.api_key,
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }
        if enterprise:
            data["enterprise"] = 1
        if action:
            data["action"] = action

        create = requests.post(f"{self.api}/in.php", data=data, timeout=30)
        create.raise_for_status()
        payload = create.json()
        if payload.get("status") != 1:
            raise RuntimeError(f"2Captcha 创建 reCAPTCHA 任务失败: {payload}")
        task_id = payload.get("request")
        if not task_id:
            raise RuntimeError(f"2Captcha 未返回 reCAPTCHA 任务 ID: {payload}")

        for _ in range(60):
            time.sleep(3)
            result = requests.get(
                f"{self.api}/res.php",
                params={
                    "key": self.api_key,
                    "action": "get",
                    "id": task_id,
                    "json": 1,
                },
                timeout=30,
            )
            result.raise_for_status()
            response = result.json()
            if response.get("status") == 1:
                return str(response.get("request") or "")
            if response.get("request") not in {"CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"}:
                raise RuntimeError(f"2Captcha reCAPTCHA 错误: {response}")
        raise TimeoutError("2Captcha reCAPTCHA 超时")

    def solve_image(self, image_b64: str) -> str:
        raise NotImplementedError


def _turnstile_proxy_fields(proxy: str) -> dict[str, object]:
    raw = str(proxy or "").strip()
    if not raw:
        return {}
    if raw.lower().startswith("socks://"):
        raw = f"socks5://{raw.split('://', 1)[1]}"
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        return {}
    scheme = parsed.scheme.lower()
    if scheme == "https":
        scheme = "http"
    if scheme not in {"http", "socks4", "socks5"}:
        return {}
    fields: dict[str, object] = {
        "proxyType": scheme,
        "proxyAddress": parsed.hostname,
        "proxyPort": parsed.port,
    }
    if parsed.username:
        fields["proxyLogin"] = parsed.username
    if parsed.password:
        fields["proxyPassword"] = parsed.password
    return fields
