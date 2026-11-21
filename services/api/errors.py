"""Common error format `{"error": {"code", "message", "details"}}` (contracts/openapi.yaml, `Error`)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

STATUS = {
    "unauthorized": 401,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "expired": 410,
    "invalid": 422,
    "internal": 500,
}
CODE_BY_STATUS = {status: code for code, status in STATUS.items()}


class ApiError(Exception):
    def __init__(self, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    @property
    def status(self) -> int:
        return STATUS[self.code]


def unauthorized(message: str = "login required") -> ApiError:
    return ApiError("unauthorized", message)


def forbidden(message: str) -> ApiError:
    return ApiError("forbidden", message)


def not_found(what: str) -> ApiError:
    return ApiError("not_found", f"{what} not found")


def conflict(message: str) -> ApiError:
    return ApiError("conflict", message)


def invalid(errors: list[dict[str, str]], message: str = "validation failed") -> ApiError:
    return ApiError("invalid", message, errors)


def body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def _loc(loc: tuple[Any, ...]) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path")]
    return ".".join(parts) or "body"


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.code == "unauthorized" else None
        return JSONResponse(body(exc.code, exc.message, exc.details), exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"path": _loc(tuple(e.get("loc", ()))), "message": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(body("invalid", "validation failed", details), 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = CODE_BY_STATUS.get(exc.status_code, "internal" if exc.status_code >= 500 else "invalid")
        message = exc.detail if isinstance(exc.detail, str) else code
        return JSONResponse(body(code, message), exc.status_code)
