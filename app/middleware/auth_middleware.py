import json
import logging
import secrets
from typing import Annotated

from pathlib import Path
from starlette.responses import FileResponse

from app.state import state

from fastapi import Request, HTTPException, Header, status
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse, RedirectResponse


class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.static_dir = Path(__file__).parent.parent / "web"

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/admin"):
            return self.admin_handler(request)

        info = await self.verify_api_key(request)
        if info:
            try:
                return await call_next(request)
            # except HTTPException:
            #     raise
            except Exception as exc:
                return self.global_exception(request, exc)

        return JSONResponse(
            status_code=403,
            content={"detail": "Forbidden"}
        )

    async def verify_api_key(self, request: Request):
        # ?table=123
        api_key = request.headers.get("X-API-KEY")
        if not api_key:
            return None

        data = None

        for stored_key, info in state.API_KEYS.items():
            if secrets.compare_digest(api_key, stored_key):
                request.state.key_info = info

                if info.get("_"):
                    allowed_methods = info.get("_")
                    if allowed_methods == ["*"] or request.url.path in allowed_methods:
                        return info

                if data is None:
                    try:
                        data = await request.json()
                    except json.JSONDecodeError:
                        return None
                table = data.get('table')
                if not table:
                    return None

                if info.get(table):
                    allowed_methods = info.get(table)
                    if allowed_methods == ["*"] or request.url.path in allowed_methods:
                        return info

        return None

    def admin_handler(self, request: Request):
        if request.url.path == "/admin":
            if request.query_params.get("set_admin"):
                response = RedirectResponse(url="/admin", status_code=302)
                response.set_cookie(key="admin_show", value="1", max_age=365 * 24 * 60 * 60, httponly=True, samesite="lax")
                return response

            if request.cookies.get("admin_show") == "1":
                return FileResponse(
                    self.static_dir / "index.html",
                    media_type="text/html"
                )

        if request.url.path == "/admin/app.js":
            if request.cookies.get("admin_show") == "1":
                return FileResponse(
                    self.static_dir / "app.js",
                    media_type="application/javascript"
                )

        return JSONResponse(
            status_code=403,
            content={"detail": "Forbidden"}
        )

    def global_exception(self, request: Request, exc: Exception):
        logging.error(
            f"Unhandled exception: {exc}",
            exc_info=True,
            extra={
                "path": request.url.path,
                "method": request.method,
            }
        )
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc)}
        )
