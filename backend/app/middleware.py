"""HTTP middleware for the privacy-first request rate limiter.

Only the endpoints that can consume provider quota (search and suggestions)
are limited. The middleware stores nothing and logs nothing: the address goes
through ``ClientRateLimiter.key_for`` and only the salted digest is kept in
memory. A rejected request gets a generic ``429`` with ``Retry-After``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection

from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.services.rate_limit import ClientRateLimiter

Middleware = Callable[[Request, RequestResponseEndpoint], Awaitable[Response]]


def client_address(request: Request) -> str:
    """The peer address as reported by the ASGI server, never a header.

    ``X-Forwarded-For`` is deliberately ignored: it is client-controlled, so
    trusting it would let a caller rotate fake buckets and would move an
    address into the application. Without a proxy the peer is the browser;
    behind Next.js it is the Next.js process itself.
    """

    return request.client.host if request.client else "unknown"


def rate_limit_middleware(
    limiter: ClientRateLimiter,
    *,
    paths: Collection[str],
) -> Middleware:
    """Build the middleware, limiting only the given exact request paths."""

    limited = frozenset(paths)

    async def middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.url.path in limited:
            allowed, retry_after = limiter.hit(client_address(request))
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Try again shortly."},
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)

    return middleware
