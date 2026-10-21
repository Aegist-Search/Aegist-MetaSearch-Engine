"""Minimal HTTP client protocol shared by the external providers.

``httpx.AsyncClient`` satisfies it, and so does
:class:`app.services.proxy_manager.ProxiedHttpClient`. Keeping the protocol
small (only the verbs and keyword arguments the providers actually use) makes
it possible to swap a direct client for a proxy-rotating one without changing
the provider logic or the existing tests.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

import httpx


class HttpRequester(Protocol):
    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response: ...

    async def post(
        self,
        url: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response: ...
