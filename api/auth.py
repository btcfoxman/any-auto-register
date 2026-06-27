from __future__ import annotations

import os

from fastapi import APIRouter, Response
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str = ""


@router.get("/check")
def auth_check():
    """Return whether the app requires a password."""
    password = os.environ.get("APP_PASSWORD", "").strip()
    return {"required": bool(password)}


@router.post("/login")
def auth_login(body: LoginRequest, response: Response):
    password = os.environ.get("APP_PASSWORD", "").strip()
    if not password:
        response.delete_cookie("_auth")
        return {"ok": True}
    if body.password == password:
        response.set_cookie(
            "_auth",
            password,
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            samesite="lax",
        )
        return {"ok": True, "token": password}
    response.delete_cookie("_auth")
    return {"ok": False, "error": "密码错误"}
