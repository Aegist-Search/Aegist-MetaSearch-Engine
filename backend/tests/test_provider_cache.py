from __future__ import annotations

from app.services.provider_cache import ProviderCache, cache_key
from app.services.results import BackendResult, RawHit


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def result(url: str = "https://example.com/a") -> BackendResult:
    return BackendResult(hits=[RawHit(url=url, title=url)])


def test_key_normalises_case_and_whitespace() -> None:
    assert cache_key("exa", "  Auto   Usata ", page=1, size=10) == cache_key(
        "exa", "auto usata", page=1, size=10
    )


def test_key_separates_provider_page_and_size() -> None:
    base = cache_key("exa", "auto", page=1, size=10)

    assert base != cache_key("brave", "auto", page=1, size=10)
    assert base != cache_key("exa", "auto", page=2, size=10)
    assert base != cache_key("exa", "auto", page=1, size=5)


def test_cache_hit_and_miss_are_counted() -> None:
    cache = ProviderCache(clock=Clock())
    key = cache_key("exa", "auto", page=1, size=10)

    assert cache.get(key) is None
    cache.put(key, result())
    assert cache.get(key) is not None
    assert cache.hits == 1
    assert cache.misses == 1


def test_entries_expire_after_the_ttl() -> None:
    clock = Clock()
    cache = ProviderCache(ttl_s=180, clock=clock)
    key = cache_key("exa", "auto", page=1, size=10)
    cache.put(key, result())

    clock.now += 179
    assert cache.get(key) is not None

    clock.now += 2
    assert cache.get(key) is None
    assert len(cache) == 0


def test_mapping_is_bounded_and_evicts_the_oldest_entry() -> None:
    clock = Clock()
    cache = ProviderCache(max_entries=2, clock=clock)

    first = cache_key("exa", "one", page=1, size=10)
    second = cache_key("exa", "two", page=1, size=10)
    third = cache_key("exa", "three", page=1, size=10)
    cache.put(first, result())
    cache.put(second, result())

    clock.now += 1
    cache.put(third, result())

    assert len(cache) == 2
    assert cache.get(first) is None
    assert cache.get(second) is not None
    assert cache.get(third) is not None


def test_clear_empties_the_cache() -> None:
    cache = ProviderCache(clock=Clock())
    cache.put(cache_key("exa", "auto", page=1, size=10), result())

    cache.clear()

    assert len(cache) == 0
