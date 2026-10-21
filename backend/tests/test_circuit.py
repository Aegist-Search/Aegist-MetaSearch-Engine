from __future__ import annotations

from app.services.circuit import CircuitBreaker


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_circuit_stays_closed_below_the_threshold() -> None:
    breaker = CircuitBreaker(failure_threshold=3, clock=Clock())

    breaker.record_failure("brave")
    breaker.record_failure("brave")

    assert breaker.available("brave") is True
    assert breaker.is_open("brave") is False
    assert breaker.failures("brave") == 2


def test_circuit_opens_after_the_threshold_and_recovers_after_the_cooldown() -> None:
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=3, cooldown_s=60, clock=clock)

    for _ in range(3):
        breaker.record_failure("exa")

    assert breaker.available("exa") is False
    assert breaker.is_open("exa") is True

    clock.now += 59
    assert breaker.available("exa") is False

    clock.now += 2
    assert breaker.available("exa") is True


def test_a_success_closes_the_circuit_and_resets_the_counter() -> None:
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=2, cooldown_s=60, clock=clock)

    breaker.record_failure("serper")
    breaker.record_failure("serper")
    assert breaker.available("serper") is False

    clock.now += 61
    breaker.record_success("serper")

    assert breaker.available("serper") is True
    assert breaker.failures("serper") == 0


def test_a_failure_after_the_cooldown_reopens_the_circuit() -> None:
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=2, cooldown_s=60, clock=clock)

    breaker.record_failure("tavily")
    breaker.record_failure("tavily")
    clock.now += 61
    assert breaker.available("tavily") is True

    breaker.record_failure("tavily")

    assert breaker.available("tavily") is False
    assert breaker.is_open("tavily") is True


def test_providers_have_independent_state() -> None:
    breaker = CircuitBreaker(failure_threshold=2, clock=Clock())

    breaker.record_failure("brave")
    breaker.record_failure("brave")

    assert breaker.available("brave") is False
    assert breaker.available("exa") is True
    assert breaker.failures("exa") == 0
