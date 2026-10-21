"""Minimal synchronous Meilisearch client for the ingestion CLI."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import httpx


class MeiliIngestError(RuntimeError):
    """Admin/ingestion failure with a sanitised message.

    ``status`` is the HTTP status when the failure came from a response, so a
    caller can distinguish a rejected request (400: for example ``id`` not in
    ``filterableAttributes`` yet) from an unreachable server.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# Ids per ``documents/fetch`` call: the filter stays far below any URL/length
# limit and the response never exceeds the 1000-document default.
_LOOKUP_CHUNK = 100


class MeiliClient:
    def __init__(
        self,
        *,
        base_url: str,
        master_key: str,
        index: str,
        timeout_s: float = 30.0,
    ) -> None:
        headers = {"Accept": "application/json"}
        if master_key.strip():
            headers["Authorization"] = f"Bearer {master_key.strip()}"

        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout_s,
        )
        self._index = index
        self._index_path = f"/indexes/{quote(index, safe='')}"

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "MeiliClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- index management --------------------------------------------------
    def ensure_index(self) -> bool:
        """Create the index when missing; return ``True`` when it was created."""

        try:
            response = self._http.get(self._index_path)
        except httpx.HTTPError as exc:
            raise MeiliIngestError(f"Meilisearch unreachable ({type(exc).__name__}).") from exc

        if response.status_code == 200:
            return False
        if response.status_code != 404:
            raise MeiliIngestError(self._error_message(response))

        task = self._request("POST", "/indexes", payload={"uid": self._index, "primaryKey": "id"})
        self.wait_for_task(task.get("taskUid"))
        return True

    def apply_settings(self, settings: dict[str, Any]) -> None:
        task = self._request("PATCH", f"{self._index_path}/settings", payload=settings)
        self.wait_for_task(task.get("taskUid"))

    def delete_all_documents(self) -> None:
        try:
            response = self._http.delete(f"{self._index_path}/documents")
        except httpx.HTTPError as exc:
            raise MeiliIngestError(f"Meilisearch unreachable ({type(exc).__name__}).") from exc
        if response.status_code == 404:
            return
        if response.status_code >= 400:
            raise MeiliIngestError(self._error_message(response))
        self.wait_for_task(response.json().get("taskUid"))

    # -- documents ---------------------------------------------------------
    def add_documents(self, documents: list[dict[str, Any]]) -> int:
        if not documents:
            return 0
        task = self._request(
            "POST",
            f"{self._index_path}/documents?primaryKey=id",
            payload=documents,
        )
        self.wait_for_task(task.get("taskUid"))
        return len(documents)

    def document_hashes(self, ids: Sequence[str]) -> dict[str, str]:
        """Stored ``content_hash`` values for ``ids``, keyed by primary key.

        Used by the incremental ingestion to compare a batch with the index
        before uploading anything. Ids that are not stored yet are simply
        missing from the result, so the caller uploads them.

        The lookup needs ``id`` in ``filterableAttributes``. On an index
        created by an older version Meilisearch rejects the filter with HTTP
        400: the error is raised with ``status = 400`` and the pipeline decides
        how to degrade (it uploads the batch and calls it unchanged next run).
        Every failure is surfaced: this method never hides a connectivity or
        auth problem.
        """

        unique = [value for value in dict.fromkeys(ids) if value]
        hashes: dict[str, str] = {}

        for start in range(0, len(unique), _LOOKUP_CHUNK):
            chunk = unique[start : start + _LOOKUP_CHUNK]
            payload = {
                "filter": "id IN [" + ", ".join(json.dumps(value) for value in chunk) + "]",
                "fields": ["id", "content_hash"],
                "limit": len(chunk),
            }
            body = self._request(
                "POST",
                f"{self._index_path}/documents/fetch",
                payload=payload,
            )

            results = body.get("results")
            if not isinstance(results, list):
                continue
            for item in results:
                if not isinstance(item, dict):
                    continue
                document_id = item.get("id")
                fingerprint = item.get("content_hash")
                if isinstance(document_id, str) and isinstance(fingerprint, str):
                    hashes[document_id] = fingerprint

        return hashes

    def wait_for_task(self, task_uid: int | None, *, timeout_s: float = 240.0) -> dict[str, Any]:
        if task_uid is None:
            raise MeiliIngestError("Meilisearch did not return a task id.")

        deadline = time.monotonic() + timeout_s
        delay = 0.15
        while True:
            task = self._request("GET", f"/tasks/{task_uid}")
            status = task.get("status")
            if status == "succeeded":
                return task
            if status in {"failed", "canceled"}:
                error = task.get("error") if isinstance(task.get("error"), dict) else {}
                message = error.get("message") or "unknown error"
                raise MeiliIngestError(f"Meilisearch task {task_uid} {status}: {message}")
            if time.monotonic() > deadline:
                raise MeiliIngestError(f"Timed out waiting for Meilisearch task {task_uid}.")
            time.sleep(delay)
            delay = min(delay * 1.6, 2.0)

    # -- internals ---------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: object | None = None,
        attempts: int = 3,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self._http.request(method, path, json=payload)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(0.5 * attempt)
                    continue
                raise MeiliIngestError(f"Meilisearch unreachable ({type(exc).__name__}).") from exc

            if response.status_code >= 500 and attempt < attempts:
                time.sleep(0.5 * attempt)
                continue
            if response.status_code >= 400:
                raise MeiliIngestError(
                    self._error_message(response),
                    status=response.status_code,
                )

            try:
                body = response.json()
            except ValueError as exc:
                raise MeiliIngestError("Meilisearch returned an invalid JSON response.") from exc
            if not isinstance(body, dict):
                raise MeiliIngestError("Meilisearch returned an unexpected payload.")
            return body

        raise MeiliIngestError(f"Meilisearch request failed ({last_error!r}).")

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        message = payload.get("message") if isinstance(payload, dict) else None
        detail = message or response.reason_phrase or "unknown error"
        return f"Meilisearch HTTP {response.status_code}: {detail}"
