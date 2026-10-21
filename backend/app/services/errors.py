"""Shared backend exceptions.

Messages only ever contain a reason code so that queries and API keys can
never end up in logs or error payloads.
"""

from __future__ import annotations


class BackendError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
