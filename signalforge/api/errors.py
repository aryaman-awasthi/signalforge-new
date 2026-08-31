"""Domain-error to HTTP mapping. One place, so every router fails the same
way: clean status codes, generic auth messages, Retry-After on rate limits."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from signalforge.domain.identity.errors import (
    AuthenticationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthenticationError)
    async def _auth(request: Request, exc: AuthenticationError):
        return JSONResponse(
            status_code=401,
            content={"detail": "authentication failed"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(PermissionDeniedError)
    async def _forbidden(request: Request, exc: PermissionDeniedError):
        detail = {"detail": "permission denied"}
        if exc.decision is not None:
            detail["permission"] = exc.decision.permission.value
            detail["reason"] = exc.decision.reason
        return JSONResponse(status_code=403, content=detail)

    @app.exception_handler(RateLimitedError)
    async def _rate_limited(request: Request, exc: RateLimitedError):
        return JSONResponse(
            status_code=429,
            content={"detail": "too many attempts"},
            headers={"Retry-After": str(exc.retry_after_s)},
        )

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def _conflict(request: Request, exc: ConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(DomainValidationError)
    async def _invalid(request: Request, exc: DomainValidationError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})
