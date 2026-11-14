import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from api import rate_limit


def test_test_environment_does_not_load_local_redis_configuration():
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
    assert "REDIS_URL" not in os.environ
    assert isinstance(rate_limit.LOGIN_IP_LIMITER, rate_limit.AttemptLimiter)


def test_in_memory_attempt_limiter_still_limits_and_resets():
    limiter = rate_limit.AttemptLimiter(max_attempts=2, window_seconds=60)

    assert limiter.retry_after("client") == 0
    limiter.record("client")
    assert limiter.retry_after("client") == 0
    limiter.record("client")
    assert limiter.retry_after("client") > 0
    limiter.reset("client")
    assert limiter.retry_after("client") == 0


def test_in_memory_attempt_reservations_cannot_exceed_limit_concurrently():
    limiter = rate_limit.AttemptLimiter(max_attempts=1, window_seconds=60)
    barrier = Barrier(2)

    def reserve():
        barrier.wait()
        return limiter.reserve("client")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: reserve(), range(2)))

    assert sum(reservation is not None for reservation, _ in results) == 1
    assert sum(retry_after > 0 for _, retry_after in results) == 1


def test_in_memory_success_releases_only_its_reservation():
    limiter = rate_limit.AttemptLimiter(max_attempts=2, window_seconds=60)
    first, _ = limiter.reserve("client")
    second, _ = limiter.reserve("client")

    limiter.complete("client", first, retain=False)
    limiter.complete("client", second, retain=True)

    assert limiter.retry_after("client") == 0
    assert limiter.reserve("client")[0] is not None
    assert limiter.reserve("client")[0] is None


def test_success_reset_preserves_attempts_started_after_the_success():
    limiter = rate_limit.AttemptLimiter(max_attempts=3, window_seconds=60)
    earlier, _ = limiter.reserve("client")
    successful, _ = limiter.reserve("client")
    later, _ = limiter.reserve("client")

    limiter.complete("client", earlier, retain=True)
    limiter.complete(
        "client",
        successful,
        retain=False,
        reset_prior=True,
    )
    limiter.complete("client", later, retain=True)

    assert limiter.retry_after("client") == 0
    assert limiter.reserve("client")[0] is not None
    assert limiter.reserve("client")[0] is not None
    assert limiter.reserve("client")[0] is None


def test_redis_limiter_uses_hashed_keys_and_shared_scripts():
    class FakeRedis:
        def __init__(self):
            self.calls = []
            self.deleted = []

        def eval(self, script, key_count, key, *args):
            self.calls.append((script, key_count, key, args))
            if "return {ARGV[4], 0}" in script:
                return [args[3], 0]
            return 0 if "ZRANGE" in script else 1

        def delete(self, key):
            self.deleted.append(key)

    client = FakeRedis()
    limiter = rate_limit.RedisAttemptLimiter(
        client,
        "auth-rate-limit:test",
        max_attempts=3,
        window_seconds=60,
    )

    assert limiter.retry_after("email:user@example.com") == 0
    reservation, retry_after = limiter.reserve("email:user@example.com")
    assert retry_after == 0
    assert reservation
    limiter.complete("email:user@example.com", reservation, retain=False)
    limiter.record("email:user@example.com")
    limiter.reset("email:user@example.com")

    assert len(client.calls) == 4
    assert client.calls[0][1] == 1
    assert "user@example.com" not in client.calls[0][2]
    assert client.calls[0][3][1:] == (60_000, 3)
    assert client.deleted == [client.calls[0][2]]


def test_redis_limiter_fails_closed_when_store_is_unavailable():
    class UnavailableRedis:
        def eval(self, *_args):
            raise RedisConnectionError("offline")

    limiter = rate_limit.RedisAttemptLimiter(
        UnavailableRedis(),
        "auth-rate-limit:test",
        max_attempts=3,
        window_seconds=60,
    )

    with pytest.raises(rate_limit.RateLimitStoreError, match="unavailable"):
        limiter.retry_after("client")
    with pytest.raises(rate_limit.RateLimitStoreError, match="unavailable"):
        limiter.reserve("client")


def test_configured_redis_url_selects_shared_limiters(monkeypatch):
    import importlib

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from api import config

    importlib.reload(config)
    importlib.reload(rate_limit)

    assert isinstance(rate_limit.LOGIN_EMAIL_LIMITER, rate_limit.RedisAttemptLimiter)
    assert isinstance(rate_limit.LOGIN_IP_LIMITER, rate_limit.RedisAttemptLimiter)
    assert isinstance(rate_limit.REGISTER_IP_LIMITER, rate_limit.RedisAttemptLimiter)
