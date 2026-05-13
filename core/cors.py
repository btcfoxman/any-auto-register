from __future__ import annotations

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class PrivateNetworkAccessMiddleware(BaseHTTPMiddleware):
    """Allow Chrome extension requests to private-network service URLs."""

    async def dispatch(self, request: Request, call_next):
        if (
            request.method == "OPTIONS"
            and request.headers.get("access-control-request-private-network", "").lower() == "true"
        ):
            origin = request.headers.get("origin") or "*"
            requested_method = request.headers.get("access-control-request-method") or "GET, POST, OPTIONS"
            requested_headers = request.headers.get("access-control-request-headers") or "*"
            return Response(
                content="OK",
                status_code=200,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": requested_method,
                    "Access-Control-Allow-Headers": requested_headers,
                    "Access-Control-Allow-Private-Network": "true",
                    "Access-Control-Max-Age": "600",
                    "Vary": "Origin",
                },
                media_type="text/plain",
            )

        response = await call_next(request)
        if request.headers.get("access-control-request-private-network", "").lower() == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response
