"""Authentication attempt limits backed by Redis when ``REDIS_URL`` is set.

Without Redis, limits use process-local memory and reset on server restart.
Behind a reverse proxy, configure Uvicorn's proxy headers and trusted proxy
IPs so per-IP limits use the client address rather than the proxy address.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
import uuid
from typing import Any, Protocol

from redis import Redis
from redis.exceptions import RedisError

from api.config import (
    LOGIN_MAX_ATTEMPTS_PER_EMAIL,
    LOGIN_MAX_ATTEMPTS_PER_IP,
    LOGIN_WINDOW_SECONDS,
    RATE_LIMIT_REDIS_URL,
    REGISTER_MAX_PER_IP,
    REGISTER_WINDOW_SECONDS,
)

logger = logging.getLogger(__name__)


class RateLimitStoreError(RuntimeError):
    """The configured shared rate-limit store could not be reached."""


class RateLimiter(Protocol):
    def reserve(self, key: str) -> tuple[str | None, int]: ...

    def complete(
        self,
        key: str,
        reservation: str,
        *,
        retain: bool,
        reset_prior: bool = False,
    ) -> None: ...

    def retry_after(self, key: str) -> int: ...

    def record(self, key: str) -> None: ...

    def reset(self, key: str) -> None: ...


class AttemptLimiter:
    """
    Sliding-window counter: allows at most `max_attempts` recorded
    attempts per key within `window_seconds`.
    """

    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        max_keys: int = 10_000,
    ) -> None:

        if max_attempts <= 0 or window_seconds <= 0 or max_keys <= 0:
            raise ValueError("Limiter settings must be positive numbers.")

        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys

        self._attempts: dict[str, list[tuple[float, str]]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[tuple[float, str]]:
        """Attempts for key still inside the window (lock must be held)."""

        recent = [
            attempt
            for attempt in self._attempts.get(key, [])
            if now - attempt[0] < self.window_seconds
        ]

        if recent:
            self._attempts[key] = recent
        else:
            self._attempts.pop(key, None)

        return recent

    def retry_after(self, key: str) -> int:
        """Seconds until this key may try again, or 0 when not limited."""

        now = time.monotonic()

        with self._lock:
            recent = self._recent(key, now)

            if len(recent) < self.max_attempts:
                return 0

            # The key is free again once the max_attempts-th most
            # recent attempt falls out of the window.
            unblock_at = recent[-self.max_attempts][0] + self.window_seconds

            return max(1, math.ceil(unblock_at - now))

    def reserve(self, key: str) -> tuple[str | None, int]:
        """Atomically count an in-flight attempt before credentials are checked."""

        now = time.monotonic()

        with self._lock:
            recent = self._recent(key, now)

            if len(recent) >= self.max_attempts:
                unblock_at = recent[-self.max_attempts][0] + self.window_seconds
                return None, max(1, math.ceil(unblock_at - now))

            reservation = uuid.uuid4().hex
            recent.append((now, reservation))
            self._attempts[key] = recent

            if len(self._attempts) > self.max_keys:
                self._purge(now)

            return reservation, 0

    def complete(
        self,
        key: str,
        reservation: str,
        *,
        retain: bool,
        reset_prior: bool = False,
    ) -> None:
        """Keep failed attempts or release reservations for successful attempts."""

        if retain and not reset_prior:
            return

        with self._lock:
            attempts_for_key = self._attempts.get(key, [])
            reservation_time = next(
                (
                    attempt[0]
                    for attempt in attempts_for_key
                    if attempt[1] == reservation
                ),
                None,
            )
            attempts = [
                attempt
                for attempt in attempts_for_key
                if attempt[1] != reservation
                and not (
                    reset_prior
                    and reservation_time is not None
                    and attempt[0] < reservation_time
                )
            ]
            if attempts:
                self._attempts[key] = attempts
            else:
                self._attempts.pop(key, None)

    def record(self, key: str) -> None:
        """Count one attempt for this key."""

        now = time.monotonic()

        with self._lock:
            self._recent(key, now)
            self._attempts.setdefault(key, []).append((now, uuid.uuid4().hex))

            if len(self._attempts) > self.max_keys:
                self._purge(now)

    def reset(self, key: str) -> None:
        """Forget this key's attempts (for example after a successful login)."""

        with self._lock:
            self._attempts.pop(key, None)

    def _purge(self, now: float) -> None:
        """Bound memory use (lock must be held)."""

        for key in list(self._attempts):
            self._recent(key, now)

        # Still too many live keys: drop the ones idle the longest.
        overflow = len(self._attempts) - self.max_keys

        if overflow > 0:
            oldest_first = sorted(
                self._attempts,
                key=lambda k: self._attempts[k][-1][0],
            )
            for key in oldest_first[:overflow]:
                self._attempts.pop(key, None)


_RETRY_AFTER_SCRIPT = """
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local max_attempts = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms - window_ms)
local count = redis.call('ZCARD', KEYS[1])
if count < max_attempts then
    return 0
end
local attempts = redis.call('ZRANGE', KEYS[1], 0, max_attempts - 1, 'WITHSCORES')
local expires_at = tonumber(attempts[max_attempts * 2]) + window_ms
return math.max(1, math.ceil((expires_at - now_ms) / 1000))
"""

_RECORD_SCRIPT = """
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms - window_ms)
redis.call('ZADD', KEYS[1], now_ms, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window_ms)
return 1
"""

_RESERVE_SCRIPT = """
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local max_attempts = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms - window_ms)
local count = redis.call('ZCARD', KEYS[1])
if count >= max_attempts then
    local attempts = redis.call('ZRANGE', KEYS[1], 0, max_attempts - 1, 'WITHSCORES')
    local expires_at = tonumber(attempts[max_attempts * 2]) + window_ms
    return {'', math.max(1, math.ceil((expires_at - now_ms) / 1000))}
end
redis.call('ZADD', KEYS[1], now_ms, ARGV[4])
redis.call('PEXPIRE', KEYS[1], window_ms)
return {ARGV[4], 0}
"""

_COMPLETE_SCRIPT = """
if ARGV[3] == '1' then
    local reservation_score = redis.call('ZSCORE', KEYS[1], ARGV[1])
    if reservation_score then
        redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', '(' .. reservation_score)
    end
end
if ARGV[2] == '0' or ARGV[3] == '1' then
    redis.call('ZREM', KEYS[1], ARGV[1])
end
return 1
"""


class RedisAttemptLimiter:
    """Sliding-window limiter backed by a shared Redis sorted set."""

    def __init__(
        self,
        client: Redis,
        key_prefix: str,
        max_attempts: int,
        window_seconds: int,
    ) -> None:
        if max_attempts <= 0 or window_seconds <= 0:
            raise ValueError("Limiter settings must be positive numbers.")

        self.client = client
        self.key_prefix = key_prefix
        self.max_attempts = max_attempts
        self.window_ms = window_seconds * 1000

    def _key(self, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"{self.key_prefix}:{digest}"

    def _eval(self, script: str, key: str, *args: object) -> Any:
        try:
            return self.client.eval(script, 1, key, *args)
        except RedisError as exc:
            raise RateLimitStoreError(
                "The shared rate-limit store is unavailable."
            ) from exc

    def reserve(self, key: str) -> tuple[str | None, int]:
        now_ms = time.time_ns() // 1_000_000
        member = f"{now_ms}:{uuid.uuid4().hex}"
        result = self._eval(
            _RESERVE_SCRIPT,
            self._key(key),
            now_ms,
            self.window_ms,
            self.max_attempts,
            member,
        )
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise RateLimitStoreError(
                "The shared rate-limit store returned an invalid response."
            )
        reservation = result[0]
        try:
            retry_after = int(result[1])
            if isinstance(reservation, bytes):
                reservation = reservation.decode("utf-8")
        except (UnicodeDecodeError, TypeError, ValueError) as exc:
            raise RateLimitStoreError(
                "The shared rate-limit store returned an invalid response."
            ) from exc
        if retry_after < 0:
            raise RateLimitStoreError(
                "The shared rate-limit store returned an invalid response."
            )
        return (str(reservation) if reservation else None), retry_after

    def complete(
        self,
        key: str,
        reservation: str,
        *,
        retain: bool,
        reset_prior: bool = False,
    ) -> None:
        self._eval(
            _COMPLETE_SCRIPT,
            self._key(key),
            reservation,
            "1" if retain else "0",
            "1" if reset_prior else "0",
        )

    def retry_after(self, key: str) -> int:
        now_ms = time.time_ns() // 1_000_000
        try:
            result = int(self._eval(
                _RETRY_AFTER_SCRIPT,
                self._key(key),
                now_ms,
                self.window_ms,
                self.max_attempts,
            ))
        except (TypeError, ValueError) as exc:
            raise RateLimitStoreError(
                "The shared rate-limit store returned an invalid response."
            ) from exc
        if result < 0:
            raise RateLimitStoreError(
                "The shared rate-limit store returned an invalid response."
            )
        return result

    def record(self, key: str) -> None:
        now_ms = time.time_ns() // 1_000_000
        self._eval(
            _RECORD_SCRIPT,
            self._key(key),
            now_ms,
            self.window_ms,
            f"{now_ms}:{uuid.uuid4().hex}",
        )

    def reset(self, key: str) -> None:
        try:
            self.client.delete(self._key(key))
        except RedisError as exc:
            raise RateLimitStoreError(
                "The shared rate-limit store is unavailable."
            ) from exc


def _create_limiter(
    client: Redis | None,
    name: str,
    max_attempts: int,
    window_seconds: int,
) -> RateLimiter:
    if client is None:
        return AttemptLimiter(max_attempts, window_seconds)
    return RedisAttemptLimiter(
        client,
        f"auth-rate-limit:{name}",
        max_attempts,
        window_seconds,
    )


_redis_client = (
    Redis.from_url(RATE_LIMIT_REDIS_URL, decode_responses=True)
    if RATE_LIMIT_REDIS_URL
    else None
)

if _redis_client is None:
    logger.warning(
        "REDIS_URL is unset; authentication rate limits are process-local. "
        "Configure Redis for multi-worker or multi-instance deployments."
    )

# Failed logins are counted per email and per IP; account creations per IP.
LOGIN_EMAIL_LIMITER = _create_limiter(
    _redis_client,
    "login-email",
    LOGIN_MAX_ATTEMPTS_PER_EMAIL,
    LOGIN_WINDOW_SECONDS,
)
LOGIN_IP_LIMITER = _create_limiter(
    _redis_client,
    "login-ip",
    LOGIN_MAX_ATTEMPTS_PER_IP,
    LOGIN_WINDOW_SECONDS,
)
REGISTER_IP_LIMITER = _create_limiter(
    _redis_client,
    "register-ip",
    REGISTER_MAX_PER_IP,
    REGISTER_WINDOW_SECONDS,
)