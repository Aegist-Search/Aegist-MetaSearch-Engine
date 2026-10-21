from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import client_address, rate_limit_middleware
from app.services.rate_limit import ClientRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build_app(
    limiter: ClientRateLimiter,
    *,
    paths: tuple[str, ...] = ("/search",),
) -> FastAPI:
    app = FastAPI()
    app.middleware("http")(rate_limit_middleware(limiter, paths=paths))

    @app.get("/search")
    async def search() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_requests_above_the_burst_get_429_with_retry_after() -> None:
    clock = FakeClock()
    limiter = ClientRateLimiter(burst=2, refill_per_minute=60, clock=clock)
    client = TestClient(build_app(limiter))

    assert client.get("/search").status_code == 200
    assert client.get("/search").status_code == 200

    response = client.get("/search")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"
    assert response.json() == {"detail": "Too many requests. Try again shortly."}


def test_a_token_refilled_after_retry_after_unblocks_the_client() -> None:
    clock = FakeClock()
    limiter = ClientRateLimiter(burst=1, refill_per_minute=60, clock=clock)
    client = TestClient(build_app(limiter))

    assert client.get("/search").status_code == 200
    assert client.get("/search").status_code == 429

    clock.advance(1.0)

    assert client.get("/search").status_code == 200


def test_paths_outside_the_limited_set_are_never_throttled() -> None:
    limiter = ClientRateLimiter(burst=1, refill_per_minute=60)
    client = TestClient(build_app(limiter))

    assert client.get("/search").status_code == 200
    assert client.get("/search").status_code == 429

    for _ in range(5):
        assert client.get("/health").status_code == 200


def test_client_addresses_are_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    limiter = ClientRateLimiter(burst=1, refill_per_minute=60)
    client = TestClient(build_app(limiter))

    with caplog.at_level(logging.DEBUG):
        client.get("/search")
        client.get("/search")

    assert "testclient" not in caplog.text
    assert "127.0.0.1" not in caplog.text


def test_bucket_keys_are_salted_digests_not_addresses() -> None:
    first = ClientRateLimiter(salt=b"a" * 16)
    second = ClientRateLimiter(salt=b"b" * 16)

    key = first.key_for("203.0.113.7")

    assert key != "203.0.113.7"
    assert "203.0.113.7" not in key
    assert len(key) == 32
    assert second.key_for("203.0.113.7") != key


def test_the_number_of_buckets_is_bounded() -> None:
    limiter = ClientRateLimiter(burst=2, refill_per_minute=60, max_clients=2)

    for identifier in ("one", "two", "three"):
        limiter.hit(identifier)

    assert limiter.clients == 2


def test_missing_peer_address_uses_a_constant_placeholder() -> None:
    class NoClientRequest:
        client = None

    assert client_address(NoClientRequest()) == "unknown"  # type: ignore[arg-type]
