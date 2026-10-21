"""Protocol shared by the external search providers."""

from __future__ import annotations

from typing import Protocol

from app.services.results import BackendResult


class SearchProvider(Protocol):
    """A server-side web search backend used as a fallback."""

    name: str

    @property
    def configured(self) -> bool:
        """Whether the provider has everything it needs to run."""
        ...

    async def search(self, query: str, *, count: int, offset: int) -> BackendResult:
        """Return at most ``count`` results, skipping ``offset`` results."""
        ...
