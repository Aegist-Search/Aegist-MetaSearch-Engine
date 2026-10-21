from __future__ import annotations

import pytest

from app.services.rate_limit import TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_burst_is_allowed_then_rejected() -> None:
    bucket = TokenBucket(capacity=2, refill_per_minute=60, clock=FakeClock())

    assert bucket.take() is True
    assert bucket.take() is True
    assert bucket.take() is False
    assert bucket.available == pytest.approx(0.0)


def test_tokens_refill_at_the_configured_rate() -> None:
    clock = FakeClock()
    # 60 starts per minute = 1 token per second, capacity 2.
    bucket = TokenBucket(capacity=2, refill_per_minute=60, clock=clock)
    assert bucket.take(2) is True

    clock.advance(1.0)

    assert bucket.available == pytest.approx(1.0)
    assert bucket.take() is True
    assert bucket.take() is False


def test_refill_never_exceeds_capacity() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=2, refill_per_minute=60, clock=clock)
    assert bucket.take(2) is True

    clock.advance(3600)

    assert bucket.available == pytest.approx(2.0)


def test_rejected_take_does_not_consume_tokens() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=1, refill_per_minute=60, clock=clock)
    assert bucket.take() is True

    clock.advance(0.25)

    assert bucket.take() is False
    assert bucket.available == pytest.approx(0.25)


@pytest.mark.parametrize(
    ("capacity", "rate"),
    [(0, 60), (1, 0), (1, -1)],
)
def test_invalid_configuration_is_rejected(capacity: int, rate: float) -> None:
    with pytest.raises(ValueError):
        TokenBucket(capacity=capacity, refill_per_minute=rate)
