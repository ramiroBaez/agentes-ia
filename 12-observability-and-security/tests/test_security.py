"""Seam 3: authentication, authorization and rate limiting.

Specification:
  - an API key is generated once, in plain text, and afterwards only its
    SHA-256 hash exists: neither the database nor the API return the key again;
  - someone else's key, a revoked one or one from a deactivated client does not
    authenticate;
  - the JWT carries the identity, expires quickly and cannot be altered
    without it being noticed;
  - scopes are required per operation (a chat client cannot approve refunds);
  - the browser gets an httpOnly cookie + a readable CSRF cookie, and every
    write from the browser requires the CSRF header to match the cookie;
  - rate limiting reports how many are left, blocks when exceeded and says when
    to retry; it is measured per plan and per client;
  - if Redis goes down, the limit does NOT open: it keeps counting in memory
    and is flagged as degraded so the dashboard can show it.
"""

import time

import jwt
import pytest
import redis

import security
from settings import Settings


class FakeClock:
    """Controllable clock so tests do not wait for minutes."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class DownRedis:
    """Redis that always fails, as if the container were not there."""

    async def incr(self, *args, **kwargs):
        raise redis.ConnectionError("redis is down")

    async def ttl(self, *args, **kwargs):
        raise redis.ConnectionError("redis is down")

    async def expire(self, *args, **kwargs):
        raise redis.ConnectionError("redis is down")

    async def aclose(self):
        return None


# --------------------------------------------------------------------- API keys
def test_a_generated_key_is_shown_once_and_only_its_hash_is_stored(db):
    """The key is seen once on creation; afterwards it can only be hashed to compare."""
    manager = security.KeyManager(db)
    created = manager.generate_key(name="Aurora Labs", scopes=["chat:write"], plan="pro")

    assert created["key"].startswith("ak_")
    assert created["client"]["id"].startswith("cli_")
    # The key text does not appear in any database row.
    assert created["key"] not in str(db.get_client(created["client"]["id"]))
    assert created["key"] not in str(db.list_clients())

    client = manager.authenticate(created["key"])
    assert client["id"] == created["client"]["id"]
    assert client["scopes"] == ["chat:write"]
    assert client["plan"] == "pro"


def test_a_wrong_or_garbage_key_does_not_authenticate(db):
    manager = security.KeyManager(db)
    created = manager.generate_key(name="Aurora Labs", scopes=["chat:write"])

    assert manager.authenticate("ak_not-a-key") is None
    assert manager.authenticate(created["key"][:-4] + "zzzz") is None
    assert manager.authenticate("") is None
    assert manager.authenticate(None) is None


def test_revoking_a_key_blocks_it_immediately(db):
    manager = security.KeyManager(db)
    created = manager.generate_key(name="Aurora Labs", scopes=["chat:write"])
    assert manager.authenticate(created["key"]) is not None

    manager.revoke(created["client"]["id"])
    assert manager.authenticate(created["key"]) is None


def test_a_deactivated_client_cannot_authenticate(db):
    manager = security.KeyManager(db)
    created = manager.generate_key(name="Aurora Labs", scopes=["chat:write"])
    db.deactivate_client(created["client"]["id"])

    assert manager.authenticate(created["key"]) is None


def test_repeated_failures_temporarily_lock_the_origin(db):
    """Brute-force brake: counted per origin (IP), not per key.

    This way an attacker blindly trying keys cannot lock out a legitimate
    client's session, but does throttle themselves.
    """
    clock = FakeClock()
    manager = security.KeyManager(db, clock=clock, max_attempts=3, lock_s=60)
    created = manager.generate_key(name="Aurora Labs", scopes=["chat:write"])
    bad = created["key"][:-4] + "zzzz"
    attacker = "ip:203.0.113.7"

    for _ in range(3):
        assert manager.authenticate(bad, origin=attacker) is None
    # The same origin stays locked for a while, even with the correct key.
    with pytest.raises(security.AuthenticationError) as error:
        manager.authenticate(created["key"], origin=attacker)
    assert error.value.reason == "too_many_attempts"
    assert error.value.code == 429

    # Another IP is not locked and gets in with the good key.
    assert manager.authenticate(created["key"], origin="ip:198.51.100.5") is not None

    clock.advance(61)
    assert manager.authenticate(created["key"], origin=attacker) is not None


# -------------------------------------------------------------------------- JWT
def test_a_jwt_round_trip_carries_the_identity(db):
    issuer = security.JWTIssuer()
    client = db.record_client("Aurora Labs", "hash", ["chat:write"], "pro")
    identity = security.Identity.from_client(client)

    token, ttl = issuer.emit(identity)

    assert ttl == pytest.approx(issuer.settings.JWT_TTL_MINUTES * 60, abs=5)
    verified = issuer.verify(token)
    assert verified.client_id == client["id"]
    assert verified.name == "Aurora Labs"
    assert verified.scopes == ("chat:write",)
    assert verified.plan == "pro"


def test_an_expired_or_tampered_token_is_refused(db):
    issuer = security.JWTIssuer()
    client = db.record_client("Aurora Labs", "hash", ["chat:write"])
    identity = security.Identity.from_client(client)

    expired, _ = issuer.emit(identity, ttl_minutes=-1)
    with pytest.raises(security.AuthenticationError) as error:
        issuer.verify(expired)
    assert error.value.reason == "expired"
    assert error.value.code == 401

    token, _ = issuer.emit(identity)
    signature, body, _ = token.split(".")
    # Altered signature (same length, other content): the header is still valid.
    tampered = f"{signature}.{body}.{signature[:-2]}xx"
    with pytest.raises(security.AuthenticationError) as error:
        issuer.verify(tampered)
    assert error.value.reason == "signature_invalid"

    # And a token signed with another secret does not pass either.
    intruder = jwt.encode(
        {"sub": "cli_intruder", "scopes": ["admin:all"], "exp": time.time() + 600},
        "another-different-secret",
        algorithm="HS256",
    )
    with pytest.raises(security.AuthenticationError) as error:
        issuer.verify(intruder)
    assert error.value.reason == "signature_invalid"

    with pytest.raises(security.AuthenticationError) as error:
        issuer.verify("not-a-jwt")
    assert error.value.reason == "malformed_token"


# ----------------------------------------------------------------- authorization
def test_scopes_are_required_per_operation(db):
    client_chat = db.record_client("Chat", "hash1", ["chat:write"])
    operator = db.record_client("Ops", "hash2", ["chat:write", "refunds:approve"])

    with pytest.raises(security.AuthorizationError) as error:
        security.require_scope(security.Identity.from_client(client_chat), "refunds:approve")
    assert error.value.code == 403
    assert "refunds:approve" in str(error.value)

    # Whoever has the scope passes, and so does the wildcard.
    security.require_scope(security.Identity.from_client(operator), "refunds:approve")
    admin = security.Identity("cli_admin", "Admin", ("admin:all",), "pro")
    security.require_scope(admin, "refunds:approve")


def test_an_anonymous_caller_cannot_use_a_privileged_scope():
    with pytest.raises(security.AuthenticationError) as error:
        security.require_scope(None, "chat:write")
    assert error.value.code == 401


# ---------------------------------------------------------------- cookies + CSRF
def test_the_browser_gets_httponly_cookies_and_writes_need_the_csrf_header(db):
    issuer = security.JWTIssuer()
    cookies = security.SessionCookies(issuer)
    client = db.record_client("Aurora Labs", "hash", ["chat:write"])

    emitted = cookies.set(security.Identity.from_client(client))

    token_cookie, csrf_cookie = emitted[issuer.settings.COOKIE_NAME], emitted["csrf"]
    assert "httponly" in token_cookie.lower()
    assert "samesite=strict" in token_cookie.lower()
    # The CSRF cookie can be read by JS: it is the one copied into the header.
    assert "httponly" not in csrf_cookie.lower()

    # Reading the session from the cookie works without a CSRF header.
    # `read` takes the same dict of values that `request.cookies` gives.
    value = token_cookie.split(";")[0].split("=", 1)[1]
    csrf = csrf_cookie.split(";")[0].split("=", 1)[1]
    browser = {
        issuer.settings.COOKIE_NAME: value,
        issuer.settings.CSRF_COOKIE_NAME: csrf,
    }
    identity = cookies.read({issuer.settings.COOKIE_NAME: value})
    assert identity.client_id == client["id"]

    # Write without the header: rejected.
    with pytest.raises(security.CSRFError) as error:
        cookies.read(browser, write=True, headers={})
    assert error.value.code == 403

    # Write with a mismatching header: rejected.
    with pytest.raises(security.CSRFError):
        cookies.read(browser, write=True, headers={"x-csrf-token": "other"})

    # Write with the correct header: passes.
    identity = cookies.read(
        browser,
        write=True,
        headers={"x-csrf-token": csrf},
    )
    assert identity.client_id == client["id"]


def test_logging_out_clears_the_cookies(db):
    issuer = security.JWTIssuer()
    cookies = security.SessionCookies(issuer)
    client = db.record_client("Aurora Labs", "hash", ["chat:write"])
    emitted = cookies.set(security.Identity.from_client(client))

    deleted = cookies.clean()

    # Logout = the same cookie, with no value and immediate expiry.
    assert deleted[issuer.settings.COOKIE_NAME].startswith("agent_session=;")
    assert "Max-Age=0" in deleted[issuer.settings.COOKIE_NAME]
    assert deleted["csrf"].startswith("csrf=;")
    assert len(set(emitted)) == 2  # we issued two distinct cookies


# --------------------------------------------------------------- rate limiting
def test_the_rate_limit_counts_per_client_and_tells_when_to_retry(db):
    clock = FakeClock()
    config = Settings(
        RATE_LIMIT_ENABLED=True,
        PLANS={
            "free": {"requests_per_minute": 3, "requests_per_day": 100, "tokens_per_day": 1000},
        },
    )
    limiter = security.Limiter(config=config, redis=None, clock=clock)
    client = db.record_client("Aurora Labs", "hash", ["chat:write"], "free")

    for remaining in (2, 1, 0):
        decision = anyio_run(limiter.record, client["id"], client["plan"])
        assert decision.allowed is True
        assert decision.remaining == remaining
        assert decision.headers["X-RateLimit-Remaining"] == str(remaining)
        assert "Retry-After" not in decision.headers

    with pytest.raises(security.LimitError) as error:
        anyio_run(limiter.record, client["id"], client["plan"])
    assert error.value.code == 429
    assert error.value.limit == 3
    assert 0 < error.value.retry_in_s <= 60
    assert error.value.headers["Retry-After"] == str(error.value.retry_in_s)

    # Once the window expires, it gets in again.
    clock.advance(61)
    assert anyio_run(limiter.record, client["id"], client["plan"]).allowed is True


def test_each_client_and_each_plan_has_its_own_budget(db):
    config = Settings(
        PLANS={
            "free": {"requests_per_minute": 1, "requests_per_day": 10, "tokens_per_day": 100},
            "pro": {"requests_per_minute": 5, "requests_per_day": 100, "tokens_per_day": 1000},
        }
    )
    limiter = security.Limiter(config=config, redis=None, clock=FakeClock())
    free_a = db.record_client("A", "h1", ["chat:write"], "free")
    free_b = db.record_client("B", "h2", ["chat:write"], "free")
    pro = db.record_client("C", "h3", ["chat:write"], "pro")

    anyio_run(limiter.record, free_a["id"], free_a["plan"])
    # B does not inherit what A spent.
    assert anyio_run(limiter.record, free_b["id"], free_b["plan"]).allowed is True
    # And pro has 5, not 1.
    for _ in range(5):
        assert anyio_run(limiter.record, pro["id"], pro["plan"]).allowed is True
    with pytest.raises(security.LimitError):
        anyio_run(limiter.record, pro["id"], pro["plan"])


def test_the_daily_quota_also_blocks(db):
    config = Settings(
        PLANS={
            "free": {"requests_per_minute": 100, "requests_per_day": 2, "tokens_per_day": 100},
        }
    )
    limiter = security.Limiter(config=config, redis=None, clock=FakeClock())
    client = db.record_client("Aurora Labs", "hash", ["chat:write"], "free")

    anyio_run(limiter.record, client["id"], client["plan"])
    anyio_run(limiter.record, client["id"], client["plan"])
    with pytest.raises(security.LimitError) as error:
        anyio_run(limiter.record, client["id"], client["plan"])
    assert error.value.window == "day"
    # And the next day it opens again.
    limiter.clock.advance(86_401)
    assert anyio_run(limiter.record, client["id"], client["plan"]).allowed is True


def test_anonymous_traffic_is_limited_by_ip(db):
    config = Settings(
        PLANS={"free": {"requests_per_minute": 2, "requests_per_day": 10, "tokens_per_day": 100}}
    )
    limiter = security.Limiter(config=config, redis=None, clock=FakeClock())

    anyio_run(limiter.record, "ip:203.0.113.9")
    anyio_run(limiter.record, "ip:203.0.113.9")
    with pytest.raises(security.LimitError):
        anyio_run(limiter.record, "ip:203.0.113.9")
    # Another IP is not affected.
    assert anyio_run(limiter.record, "ip:198.51.100.4").allowed is True


def test_when_redis_is_down_the_limit_still_holds_and_is_flagged(db):
    config = Settings(
        PLANS={"free": {"requests_per_minute": 2, "requests_per_day": 10, "tokens_per_day": 100}}
    )
    limiter = security.Limiter(config=config, redis=DownRedis(), clock=FakeClock())
    client = db.record_client("Aurora Labs", "hash", ["chat:write"], "free")

    first = anyio_run(limiter.record, client["id"], client["plan"])
    second = anyio_run(limiter.record, client["id"], client["plan"])

    # A downed Redis does not open the floodgates: the quota keeps counting in memory.
    assert first.allowed and second.allowed
    with pytest.raises(security.LimitError) as error:
        anyio_run(limiter.record, client["id"], client["plan"])
    assert error.value.code == 429
    # And it is on record so it can be alerted on.
    assert limiter.degraded is True
    assert limiter.reason_degradation


class FlakyRedis:
    """Redis with a switch: the test decides when it goes down and when it comes back."""

    def __init__(self, down: bool = True) -> None:
        self.down = down
        self.pings = 0
        self.incrs = 0

    def _require(self) -> None:
        if self.down:
            raise redis.ConnectionError("redis is down")

    async def ping(self, *args, **kwargs):
        self.pings += 1
        self._require()

    async def incr(self, *args, **kwargs):
        self._require()
        self.incrs += 1
        return 1

    async def ttl(self, *args, **kwargs):
        self._require()
        return 60

    async def expire(self, *args, **kwargs):
        self._require()
        return True

    async def decr(self, *args, **kwargs):
        self._require()
        return 0

    async def aclose(self):
        return None


def test_the_limiter_goes_back_to_redis_once_it_comes_back(db):
    """A Redis that went down and came back cannot stay degraded forever.

    Before, `degraded` was only ever set to True and never cleared: the process
    kept counting in memory (the quota was applied anyway, which is fine), but
    with more than one replica each would keep its own counter and what the
    dashboard said was false.
    """
    config = Settings(
        PLANS={"free": {"requests_per_minute": 10, "requests_per_day": 100, "tokens_per_day": 100}}
    )
    clock = FakeClock()
    redis_fake = FlakyRedis(down=True)
    limiter = security.Limiter(config=config, redis=redis_fake, clock=clock)
    client = db.record_client("Aurora Labs", "hash", ["chat:write"], "free")

    anyio_run(limiter.record, client["id"], client["plan"])
    assert limiter.degraded is True, "with Redis down it must report it"

    # Still down: one PING, and it is not retried on every request.
    clock.advance(5)
    anyio_run(limiter.record, client["id"], client["plan"])
    anyio_run(limiter.record, client["id"], client["plan"])
    assert limiter.degraded is True
    assert redis_fake.pings == 1, "polling is bounded in time"

    # Redis comes back: after the interval, one PING and back to the shared counter.
    redis_fake.down = False
    clock.advance(60)
    decision = anyio_run(limiter.record, client["id"], client["plan"])

    assert decision.allowed is True
    assert limiter.degraded is False
    assert limiter.reason_degradation == ""
    assert redis_fake.pings == 2
    assert decision.degraded is False, "the decision tells the client it is no longer degraded"
    assert redis_fake.incrs > 0, "and the counter is Redis's again"


def _counter(limiter, window: str, key: str) -> int:
    """The value of the in-memory counter of a window."""
    return limiter._memory[(f"rl:{window}:{key}", window)].value


def test_a_rejected_request_does_not_burn_the_other_window(db):
    """A 429 must leave the counters exactly as they were.

    The counter has two windows: minute and day. When the daily quota was
    rejected, the minute one had already been charged and stayed incremented:
    the client lost requests from the window it still had free, and when the
    minute restarted it found the number inflated.
    """
    config = Settings(
        PLANS={"free": {"requests_per_minute": 10, "requests_per_day": 2, "tokens_per_day": 100}}
    )
    clock = FakeClock()
    limiter = security.Limiter(config=config, redis=None, clock=clock)
    client = db.record_client("Aurora Labs", "hash", ["chat:write"], "free")

    anyio_run(limiter.record, client["id"], client["plan"])
    anyio_run(limiter.record, client["id"], client["plan"])
    minute, day = 2, 2

    with pytest.raises(security.LimitError) as error:
        anyio_run(limiter.record, client["id"], client["plan"])

    assert error.value.window == "day"
    assert _counter(limiter, "minute", client["id"]) == minute, "the minute window was overcharged"
    assert _counter(limiter, "day", client["id"]) == day

    # There is no way to see it from outside: with the daily quota exhausted, any
    # later request is rejected anyway and the window reset wipes the number. The
    # defect was internal (the counter drifted), so the test looks at the counter:
    # what matters is that a 429 charges nothing.


def test_the_memory_fallback_keeps_its_table_bounded():
    """The in-memory table is pruned: it does not grow unbounded if Redis never returns."""
    config = Settings(
        PLANS={
            "free": {"requests_per_minute": 10, "requests_per_day": 100, "tokens_per_day": 1000}
        }
    )
    clock = FakeClock()
    limiter = security.Limiter(config=config, redis=None, clock=clock, max_keys_memory=10)
    for i in range(50):
        anyio_run(limiter.record, f"cli_{i}")
    assert limiter.keys_in_memory() <= 10

    # When the window expires, old keys go away.
    clock.advance(3_600)
    anyio_run(limiter.record, "cli_0")
    assert limiter.keys_in_memory() <= 10


def test_the_limiter_can_be_switched_off(db):
    config = Settings(RATE_LIMIT_ENABLED=False)
    limiter = security.Limiter(config=config, redis=None, clock=FakeClock())
    for _ in range(500):
        assert anyio_run(limiter.record, "cli_x").allowed is True


def test_configuration_refuses_insecure_production_defaults():
    from settings import SECRET_DEVELOPMENT

    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(ENVIRONMENT="production", JWT_SECRET=SECRET_DEVELOPMENT, COOKIE_SECURE=True)
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings(ENVIRONMENT="production", JWT_SECRET="a-really-long-real-secret-1234567890")

    # With both things right, it starts.
    ok = Settings(
        ENVIRONMENT="production",
        JWT_SECRET="a-really-long-real-secret-1234567890",
        COOKIE_SECURE=True,
    )
    assert ok.ENVIRONMENT == "production"


def test_security_module_never_logs_or_returns_the_key_hash(db):
    manager = security.KeyManager(db)
    created = manager.generate_key(name="Aurora Labs", scopes=["chat:write"])
    assert "key_hash" not in created["client"]
    assert not hasattr(security, "API_KEYS")


def anyio_run(function, *args):
    """Run a coroutine from a synchronous test (no pytest-asyncio)."""
    import anyio

    return anyio.run(function, *args)
