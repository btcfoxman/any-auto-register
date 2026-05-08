from __future__ import annotations

import json
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import unquote


LINGYA_QQ_COOKIE_NAMES: tuple[str, ...] = (
    "_new_next_refresh_time",
    "_qimei_fingerprint",
    "_qimei_h38",
    "_qimei_q36",
    "_qimei_uuid42",
    "avatar",
    "env",
    "last_refresh_second",
    "last_refresh_time",
    "min_expire_time",
    "nick",
    "v_login_time_init",
    "v_main_login",
    "v_next_refresh_time",
    "v_t_access_token",
    "v_t_appid",
    "v_t_openid",
    "v_t_refresh_token",
    "v_vurefresh",
    "v_vuserid",
    "v_vusession",
    "vdevice_guid",
    "video_appid",
    "video_platform",
    "vqq_vuserid",
    "vqq_vusession",
    "vuserid",
    "vusession",
)

LINGYA_QQ_COOKIE_NAME_SET = set(LINGYA_QQ_COOKIE_NAMES)
LINGYA_QQ_COOKIE_INPUT_KEYS = (
    "lingya_qq_cookies",
    "cookies",
    "cookie",
    "cookie_header",
    "lingya_cookies",
    "qq_cookie",
    "lingya_qq_cookie",
)

LINGYA_QQ_SESSION_COOKIE_KEYS = ("v_vusession", "vusession", "vqq_vusession")
LINGYA_QQ_USER_ID_COOKIE_KEYS = ("v_vuserid", "vuserid", "vqq_vuserid")

LINGYA_QQ_COOKIE_CREDENTIAL_TYPES: dict[str, str] = {
    name: "cookie" for name in LINGYA_QQ_COOKIE_NAMES
}
LINGYA_QQ_COOKIE_CREDENTIAL_TYPES.update(
    {
        "v_vusession": "token",
        "vusession": "token",
        "vqq_vusession": "token",
        "v_vurefresh": "token",
        "v_t_access_token": "token",
        "v_t_refresh_token": "token",
        "v_vuserid": "identifier",
        "vuserid": "identifier",
        "vqq_vuserid": "identifier",
        "vdevice_guid": "identifier",
        "v_t_appid": "identifier",
        "v_t_openid": "identifier",
        "video_appid": "identifier",
        "video_platform": "identifier",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _decode_cookie_value(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        return unquote(text)
    except Exception:
        return text


def _push_cookie(result: dict[str, str], name: Any, value: Any) -> None:
    key = _text(name)
    if key not in LINGYA_QQ_COOKIE_NAME_SET:
        return
    text = _decode_cookie_value(value)
    if text:
        result[key] = text


def _parse_cookie_header(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    text = _text(value)
    if not text or "=" not in text:
        return result
    cookie = SimpleCookie()
    try:
        cookie.load(text)
        for name, morsel in cookie.items():
            _push_cookie(result, name, morsel.value)
        if result:
            return result
    except Exception:
        pass
    for part in text.split(";"):
        if "=" not in part:
            continue
        name, raw_value = part.split("=", 1)
        _push_cookie(result, name, raw_value)
    return result


def _parse_cookie_payload(value: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if value in (None, "", [], {}):
        return result

    if isinstance(value, dict):
        if "name" in value and "value" in value:
            _push_cookie(result, value.get("name"), value.get("value"))
            return result
        for key, item in value.items():
            if isinstance(item, dict) and "value" in item:
                _push_cookie(result, key, item.get("value"))
            else:
                _push_cookie(result, key, item)
        return result

    if isinstance(value, list):
        for item in value:
            result.update(_parse_cookie_payload(item))
        return result

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return result
        if text[:1] in {"{", "["}:
            try:
                return _parse_cookie_payload(json.loads(text))
            except Exception:
                pass
        return _parse_cookie_header(text)

    return result


def extract_lingya_qq_cookies(source: Any) -> dict[str, str]:
    """Extract allowlisted Lingya cookies from extension exports or headers."""
    result = _parse_cookie_payload(source)
    if not isinstance(source, dict):
        return result

    for name in LINGYA_QQ_COOKIE_NAMES:
        if source.get(name) not in (None, ""):
            _push_cookie(result, name, source.get(name))
    for key in LINGYA_QQ_COOKIE_INPUT_KEYS:
        if key in source:
            result.update(_parse_cookie_payload(source.get(key)))
    credentials = source.get("credentials")
    if isinstance(credentials, dict):
        for name in LINGYA_QQ_COOKIE_NAMES:
            if credentials.get(name) not in (None, ""):
                _push_cookie(result, name, credentials.get(name))
        for key in LINGYA_QQ_COOKIE_INPUT_KEYS:
            if key in credentials:
                result.update(_parse_cookie_payload(credentials.get(key)))
    return result


def _first(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _first_key(source: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = _text(source.get(key))
        if text:
            return text
    return ""


def format_lingya_qq_cookie_header(cookies: dict[str, Any]) -> str:
    parts = []
    for name in LINGYA_QQ_COOKIE_NAMES:
        value = _text(cookies.get(name))
        if value:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def build_lingya_qq_account_fields(
    source: dict[str, Any] | None = None,
    *,
    login_response: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    vdevice_guid: str | None = None,
    video_appid: str = "3000116",
    video_platform: str = "2",
) -> dict[str, Any]:
    """Build flat account credential fields from real cookies plus login data."""
    source = _safe_dict(source)
    login_response = _safe_dict(login_response)
    profile = _safe_dict(profile)
    user_info = _safe_dict(login_response.get("user_info"))
    cookies = extract_lingya_qq_cookies(source)

    vusession = _first(
        login_response.get("vusession"),
        _first_key(cookies, LINGYA_QQ_SESSION_COOKIE_KEYS),
        source.get("vusession"),
    )
    vurefresh = _first(login_response.get("vurefresh"), cookies.get("v_vurefresh"), source.get("vurefresh"))
    vuid = _first(
        login_response.get("vuid"),
        _first_key(cookies, LINGYA_QQ_USER_ID_COOKIE_KEYS),
        source.get("vuid"),
    )
    device_guid = _first(vdevice_guid, source.get("vdevice_guid"), cookies.get("vdevice_guid"))
    nick = _first(profile.get("nickname"), user_info.get("user_nick"), source.get("nick"), cookies.get("nick"))
    avatar = _first(profile.get("avatar"), user_info.get("user_head"), source.get("avatar"), cookies.get("avatar"))

    aliases = {
        "v_vusession": vusession,
        "vusession": vusession,
        "vqq_vusession": vusession,
        "v_vurefresh": vurefresh,
        "v_vuserid": vuid,
        "vuserid": vuid,
        "vqq_vuserid": vuid,
        "vdevice_guid": device_guid,
        "nick": nick,
        "avatar": avatar,
        "video_appid": video_appid,
        "video_platform": video_platform,
    }
    forced_aliases: set[str] = set()
    if _text(login_response.get("vusession")):
        forced_aliases.update(LINGYA_QQ_SESSION_COOKIE_KEYS)
    if _text(login_response.get("vurefresh")):
        forced_aliases.add("v_vurefresh")
    if _text(login_response.get("vuid")):
        forced_aliases.update(LINGYA_QQ_USER_ID_COOKIE_KEYS)
    if _text(vdevice_guid):
        forced_aliases.add("vdevice_guid")
    if _text(profile.get("nickname") or user_info.get("user_nick")):
        forced_aliases.add("nick")
    if _text(profile.get("avatar") or user_info.get("user_head")):
        forced_aliases.add("avatar")
    for key, value in aliases.items():
        text = _text(value)
        if text and (key in forced_aliases or not cookies.get(key)):
            cookies[key] = text

    fields: dict[str, Any] = {
        key: value
        for key, value in cookies.items()
        if key in LINGYA_QQ_COOKIE_NAME_SET and value not in (None, "")
    }
    canonical = {
        "vusession": vusession,
        "vurefresh": vurefresh,
        "vuid": vuid,
        "vdevice_guid": device_guid,
        "v_main_login": cookies.get("v_main_login"),
        "nick": nick,
        "avatar": avatar,
    }
    for key, value in canonical.items():
        text = _text(value)
        if text:
            fields[key] = text
    cookie_header = format_lingya_qq_cookie_header(fields)
    if cookie_header:
        fields["cookies"] = cookie_header
    return fields
