from api import cache


def teardown_function():
    cache.clear()


def test_cached_loads_once_until_it_expires():
    calls = []
    load = lambda: calls.append(1) or len(calls)  # noqa: E731
    assert cache.cached(("state", "L1", 2026), 60, load) == 1
    assert cache.cached(("state", "L1", 2026), 60, load) == 1
    assert len(calls) == 1
    cache.cached(("state", "L2", 2026), -1, load)  # stored already expired
    assert cache.cached(("state", "L2", 2026), 60, load) == 3
    assert len(calls) == 3


def test_forget_drops_one_league_only():
    cache.cached(("state", "L1", 2026), 60, lambda: "a")
    cache.cached(("state", "L2", 2026), 60, lambda: "b")
    cache.forget("state", "L1")
    assert cache.cached(("state", "L1", 2026), 60, lambda: "fresh") == "fresh"
    assert cache.cached(("state", "L2", 2026), 60, lambda: "fresh") == "b"


def test_token_key_hides_the_token():
    key = cache.token_key("secret-token")
    assert "secret" not in key and len(key) == 64
