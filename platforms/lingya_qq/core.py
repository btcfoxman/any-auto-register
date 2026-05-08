from __future__ import annotations

import random
import re
import string
import time
import uuid
from datetime import datetime
from typing import Any

import requests


LINGYA_ORIGIN = "https://lingya.qq.com"
PBACCESS_BASE = "https://pbaccess.lingya.qq.com"
FILEACCESS_BASE = "https://fileaccess.lingya.qq.com"
VIDEO_TRANSPOND_HOSTS = (
    "https://videotranspond1.v.qq.com",
    "https://videotranspond2.v.qq.com",
)
VIDEO_APPID = "3000116"
VVERSION_PLATFORM = "2"
PHONE_LOGIN_FROM = "spp_hlw_phone_login"
PUBLISH_BIZ_ID = "1000226"
VIDEO_UPLOAD_CHUNK_SIZE = 1024 * 1024
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def make_vdevice_guid(length: int = 16) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def normalize_area_code(value: str | None = None) -> str:
    raw = str(value or "+86").strip()
    if raw.startswith("+"):
        return raw
    digits = re.sub(r"\D+", "", raw)
    return f"+{digits}" if digits else "+86"


def normalize_lingya_phone(phone: str, area_code: str = "+86") -> str:
    digits = re.sub(r"\D+", "", str(phone or ""))
    country = re.sub(r"\D+", "", area_code or "")
    if country and digits.startswith(country) and len(digits) > 11:
        digits = digits[len(country):]
    return digits


def _int_response_field(value: Any, default: int = -1) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _raise_for_ret(data: dict[str, Any], label: str) -> None:
    if _int_response_field(data.get("ret"), 0) != 0:
        raise RuntimeError(f"{label} failed: {data.get('msg') or data}")
    inner = data.get("data")
    if isinstance(inner, dict) and "error_code" in inner and _int_response_field(inner.get("error_code"), 0) != 0:
        raise RuntimeError(f"{label} rejected: {inner.get('error_msg') or inner}")


def _raise_for_video_code(data: dict[str, Any], label: str) -> None:
    if _int_response_field(data.get("code"), 0) != 0:
        raise RuntimeError(f"{label} failed: {data.get('msg') or data}")


class LingYaQQClient:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        vdevice_guid: str | None = None,
        cookies: dict[str, Any] | None = None,
        user_agent: str | None = None,
        timeout: int = 20,
    ):
        cookie_device = ""
        if isinstance(cookies, dict):
            cookie_device = str(cookies.get("vdevice_guid") or "").strip()
        self.vdevice_guid = vdevice_guid or cookie_device or make_vdevice_guid()
        self.timeout = int(timeout or 20)
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.session = requests.Session()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        if cookies:
            self.set_cookies(cookies)

    def set_cookies(self, cookies: dict[str, Any]) -> None:
        for name, value in cookies.items():
            text = str(value or "").strip()
            if not name or not text:
                continue
            domain = ".qq.com" if str(name).startswith("_qimei_") else ".lingya.qq.com"
            self.session.cookies.set(str(name), text, domain=domain, path="/")

    def cookie_dict(self) -> dict[str, str]:
        return {
            str(cookie.name): str(cookie.value)
            for cookie in self.session.cookies
            if getattr(cookie, "name", "")
        }

    def _pbaccess_params(self) -> dict[str, str]:
        return {
            "video_appid": VIDEO_APPID,
            "vversion_platform": VVERSION_PLATFORM,
            "vdevice_guid": self.vdevice_guid,
        }

    def _headers(self, *, json_content: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "application/json" if json_content else "*/*",
            "Origin": LINGYA_ORIGIN,
            "Referer": f"{LINGYA_ORIGIN}/",
            "User-Agent": self.user_agent,
        }
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def _video_upload_headers(self, svr_token: str, *, json_content: bool = True) -> dict[str, str]:
        headers = self._headers(json_content=json_content)
        headers["Accept"] = "application/json, text/plain, */*"
        headers["svr-token"] = str(svr_token or "")
        if not json_content:
            headers["Content-Type"] = "application/octet-stream"
        return headers

    def _post_pbaccess(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.post(
            f"{PBACCESS_BASE}{path}",
            params=self._pbaccess_params(),
            json=payload or {},
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def send_sms(self, *, phone: str, area_code: str = "+86") -> dict[str, Any]:
        response = self.session.post(
            "https://vip.video.qq.com/fcgi-bin/comm_cgi",
            params={"name": PHONE_LOGIN_FROM, "cmd": "25460", "otype": "xjson"},
            json={"area_code": normalize_area_code(area_code), "phone": phone, "from": PHONE_LOGIN_FROM},
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if _int_response_field(data.get("ret")) != 0:
            raise RuntimeError(f"Lingya SMS send failed: {data.get('msg') or data}")
        return data

    def login_with_phone_code(self, *, phone: str, code: str, area_code: str = "+86") -> dict[str, Any]:
        payload = {
            "login_request": {
                "login_type": 2,
                "phone_login_request": {
                    "phone_login_type": 2,
                    "code_login_info": {
                        "phone_number": phone,
                        "verification_code": code,
                        "area_code": normalize_area_code(area_code),
                    },
                },
            }
        }
        data = self._post_pbaccess("/trpc.caotai.account.WebLogin/WebLogin", payload)
        if _int_response_field(data.get("ret")) != 0:
            raise RuntimeError(f"Lingya WebLogin failed: {data.get('msg') or data}")
        inner = data.get("data") or {}
        if _int_response_field(inner.get("error_code")) != 0:
            raise RuntimeError(f"Lingya WebLogin rejected: {inner.get('error_msg') or inner}")
        return data

    def get_user_profile(self, vuid: str) -> dict[str, Any]:
        return self._post_pbaccess(
            "/trpc.caotai.personal_page.PersonalPage/GetUserProfileInfo",
            {"vuid": str(vuid or "")},
        )

    def get_bind_account_info(self, vuid: str) -> dict[str, Any]:
        return self._post_pbaccess(
            "/trpc.caotai.personal_page.PersonalPage/GetBindAccountInfo",
            {"vuid": str(vuid or "")},
        )

    def get_user_quota(self) -> dict[str, Any]:
        return self._post_pbaccess("/trpc.workstation.backend.Space/GetUserQuota", {})

    def hello(self) -> dict[str, Any]:
        return self._post_pbaccess("/trpc.workstation.backend.Space/Hello", {})

    def homepage(self) -> dict[str, Any]:
        return self._post_pbaccess("/trpc.workstation.backend.Space/HomePage", {})

    def get_credits_panel(self, is_first_register: bool = False) -> dict[str, Any]:
        return self._post_pbaccess(
            "/trpc.workstation.backend.TaskAdapter/GetCreditsPanel",
            {"is_first_register": bool(is_first_register)},
        )

    def credits_panel_sign_in(self) -> dict[str, Any]:
        return self._post_pbaccess(
            "/trpc.workstation.backend.TaskAdapter/CreditsPanelSignIn",
            {},
        )

    def check_credits_first_register(self) -> dict[str, Any]:
        return self._post_pbaccess(
            "/trpc.workstation.backend.TaskAdapter/CheckCreditsFirstRegister",
            {},
        )

    def get_video_upload_params(self, seq: str | None = None) -> dict[str, Any]:
        upload_seq = str(seq or uuid.uuid4()).strip()
        data = self._post_pbaccess(
            "/trpc.caotai.publish.PublishService/GetVideoUploadParams",
            {"seq": upload_seq},
        )
        _raise_for_ret(data, "LingYaQQ GetVideoUploadParams")
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        token = str((inner or {}).get("svr_token") or "").strip()
        if not token:
            raise RuntimeError(f"LingYaQQ GetVideoUploadParams did not return svr_token: {data}")
        return {"seq": upload_seq, **(inner or {})}

    def upload_image_bytes(
        self,
        image_bytes: bytes,
        *,
        filename: str = "cover.jpg",
        content_type: str | None = None,
    ) -> str:
        response = self.session.post(
            f"{FILEACCESS_BASE}/upload/image",
            params={
                "channel": "caotai_image",
                "vversion_platform": VVERSION_PLATFORM,
                "video_appid": VIDEO_APPID,
            },
            files={
                "file": (
                    filename or "cover.jpg",
                    image_bytes,
                    content_type or "application/octet-stream",
                )
            },
            data={"filename": "undefined"},
            headers=self._headers(json_content=False),
            timeout=max(self.timeout, 60),
        )
        response.raise_for_status()
        data = response.json()
        _raise_for_ret(data, "LingYaQQ image upload")
        url = str(data.get("url") or (data.get("data") or {}).get("url") or "").strip()
        if not url:
            raise RuntimeError(f"LingYaQQ image upload did not return url: {data}")
        return url

    def _post_video_json(self, host: str, path: str, payload: dict[str, Any], svr_token: str) -> dict[str, Any]:
        response = self.session.post(
            f"{host}{path}",
            json=payload,
            headers=self._video_upload_headers(svr_token, json_content=True),
            timeout=max(self.timeout, 120),
        )
        response.raise_for_status()
        data = response.json()
        _raise_for_video_code(data, f"LingYaQQ video {path}")
        return data

    def upload_video_bytes(
        self,
        video_bytes: bytes,
        *,
        filename: str = "video.mp4",
        vuid: str,
        seq: str | None = None,
        chunk_size: int = VIDEO_UPLOAD_CHUNK_SIZE,
    ) -> dict[str, Any]:
        if not video_bytes:
            raise RuntimeError("LingYaQQ video upload requires non-empty video bytes")
        if not str(vuid or "").strip():
            raise RuntimeError("LingYaQQ video upload requires vuid")
        chunk_size = max(int(chunk_size or VIDEO_UPLOAD_CHUNK_SIZE), 256 * 1024)
        upload_params = self.get_video_upload_params(seq=seq)
        svr_token = str(upload_params.get("svr_token") or "").strip()
        service_id = f"{PUBLISH_BIZ_ID}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
        prepare_payload = {
            "bizId": PUBLISH_BIZ_ID,
            "serviceId": service_id,
            "type": "0",
            "needVid": "1",
            "vUid": str(vuid),
        }
        prepare = self._post_video_json(
            VIDEO_TRANSPOND_HOSTS[0],
            "/v2/video/prepare",
            prepare_payload,
            svr_token,
        )
        prepare_data = prepare.get("data") if isinstance(prepare.get("data"), dict) else {}
        file_id = str(prepare_data.get("fileId") or prepare_data.get("file_id") or "").strip()
        ukey = str(prepare_data.get("ukey") or prepare_data.get("uKey") or "").strip()
        vid = str(prepare_data.get("vid") or "").strip()
        video_id = str(prepare_data.get("videoId") or prepare_data.get("video_id") or "").strip()
        if not file_id or not ukey or not vid or not video_id:
            raise RuntimeError(f"LingYaQQ video prepare response is incomplete: {prepare}")

        finish_parts: list[dict[str, Any]] = []
        for index, offset in enumerate(range(0, len(video_bytes), chunk_size), start=1):
            chunk = video_bytes[offset: offset + chunk_size]
            host = VIDEO_TRANSPOND_HOSTS[(index - 1) % len(VIDEO_TRANSPOND_HOSTS)]
            response = self.session.post(
                f"{host}/v2/upload/uploadpart",
                params={"filename": file_id, "ukey": ukey, "partnum": index},
                data=chunk,
                headers=self._video_upload_headers(svr_token, json_content=False),
                timeout=max(self.timeout, 180),
            )
            response.raise_for_status()
            part_data = response.json()
            _raise_for_video_code(part_data, "LingYaQQ video uploadpart")
            part_sha = str(
                part_data.get("partSha")
                or part_data.get("part_sha")
                or (part_data.get("data") or {}).get("partSha")
                or ""
            ).strip()
            if not part_sha:
                raise RuntimeError(f"LingYaQQ uploadpart did not return partSha: {part_data}")
            finish_parts.append({"partNum": index, "partSha": part_sha})

        finish = self._post_video_json(
            VIDEO_TRANSPOND_HOSTS[0],
            "/v2/upload/finishupload",
            {
                "bizId": PUBLISH_BIZ_ID,
                "fileName": file_id,
                "uKey": ukey,
                "skipAudit": 1,
                "finishParts": finish_parts,
            },
            svr_token,
        )
        notify = self._post_video_json(
            VIDEO_TRANSPOND_HOSTS[0],
            "/v2/video/notifyencode",
            {
                "bizId": PUBLISH_BIZ_ID,
                "serviceId": service_id,
                "type": "1",
                "videoId": video_id,
                "vid": vid,
                "fileId": file_id,
            },
            svr_token,
        )
        return {
            "seq": upload_params.get("seq"),
            "service_id": service_id,
            "vid": vid,
            "video_id": video_id,
            "file_id": file_id,
            "file_name": filename or "video.mp4",
            "part_count": len(finish_parts),
            "prepare": prepare,
            "finish": finish,
            "notify": notify,
        }

    def get_work_generation_status(self, vid: str) -> dict[str, Any]:
        data = self._post_pbaccess(
            "/trpc.caotai.publish.PublishService/GetWorkGenerationStatus",
            {"vid": str(vid or "")},
        )
        _raise_for_ret(data, "LingYaQQ GetWorkGenerationStatus")
        return data

    def content_security_review(self, text: str) -> dict[str, Any]:
        data = self._post_pbaccess(
            "/trpc.caotai.publish.PublishService/ContentSecurityReview",
            {"text": str(text or "")},
        )
        _raise_for_ret(data, "LingYaQQ ContentSecurityReview")
        return data

    def get_cover_color_info(self, *, vid: str, cover_url: str) -> dict[str, Any]:
        data = self._post_pbaccess(
            "/trpc.caotai.publish.FramesService/GetCoverColorInfo",
            {"vid": str(vid or ""), "cover_url": str(cover_url or "")},
        )
        _raise_for_ret(data, "LingYaQQ GetCoverColorInfo")
        return data

    def upload_work(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._post_pbaccess(
            "/trpc.caotai.publish.PublishService/UploadWork",
            payload,
        )
        _raise_for_ret(data, "LingYaQQ UploadWork")
        return data

    def get_my_work_list(
        self,
        *,
        filter_by_status: int = 1,
        page: int = 1,
        page_size: int = 15,
    ) -> dict[str, Any]:
        data = self._post_pbaccess(
            "/trpc.caotai.publish.WorkManagementService/GetMyWorkList",
            {
                "filter_by_status": int(filter_by_status),
                "page_context": {"page": str(page), "page_size": str(page_size)},
            },
        )
        _raise_for_ret(data, "LingYaQQ GetMyWorkList")
        return data

    def refresh_session(self, *, main_login: str = "wx") -> dict[str, Any]:
        data = self._post_pbaccess(
            "/trpc.caotai.account.WebLogin/WebRefresh",
            {"type": str(main_login or "wx")},
        )
        if _int_response_field(data.get("ret")) != 0:
            raise RuntimeError(f"Lingya WebRefresh failed: {data.get('msg') or data}")
        inner = data.get("data") or {}
        if _int_response_field(inner.get("error_code")) != 0:
            raise RuntimeError(f"Lingya WebRefresh rejected: {inner.get('error_msg') or inner}")

        refresh_response = (((inner.get("rsp") or {}).get("refresh_response")) or {})
        vuid = str(refresh_response.get("vuid") or "").strip()
        vusession = str(refresh_response.get("vusession") or "").strip()
        vurefresh = str(refresh_response.get("vurefresh") or "").strip()
        if vusession:
            now = int(time.time())
            expire_in = int(refresh_response.get("vusession_expire_in") or 7200)
            expire_at = int(refresh_response.get("vusession_expire_timestamp") or (now + expire_in))
            self.set_cookies(
                {
                    "v_vuserid": vuid,
                    "vuserid": vuid,
                    "vqq_vuserid": vuid,
                    "v_vusession": vusession,
                    "vusession": vusession,
                    "vqq_vusession": vusession,
                    "v_vurefresh": vurefresh,
                    "last_refresh_second": str(now),
                    "last_refresh_time": str(now),
                    "v_next_refresh_time": str(expire_in),
                    "min_expire_time": str(expire_in),
                    "_new_next_refresh_time": str(max(now + 60, expire_at - 900)),
                }
            )
        return data
