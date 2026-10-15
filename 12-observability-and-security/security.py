r"""Authentication, authorization and rate limiting for Project 12.

Three layers, in the order a request goes through them:

1. **Authentication**: who you are.
   - `KeyManager`: API keys for machines. The key is generated in plain text
     only once and from then on only its SHA-256 exists (like GitHub or
     Stripe): if someone reads the database, they cannot authenticate with it.
   - `JWTIssuer`: short browser session (30 min), signed with HS256.
   - `SessionCookies`: the session cookie is `httpOnly` (JS cannot see it) and
     a second, JS-readable CSRF cookie must be echoed in a header to write.
     This is the classic CSRF defense, combined with `SameSite=Strict`.

2. **Authorization**: what you can do. `require_scope` checks the token scopes
   (`chat:write`, `refunds:approve`, `admin:all`, ...). By default the agent
   **cannot** move money: sensitive actions go through human approval.

3. **Rate limiting**: `Limiter` counts per client and per plan in two windows
   (minute and day) using Redis, and keeps counting in memory if Redis is not
   available. The degradation is flagged, but the quota is **never opened**: a
   Redis outage degrades counter accuracy, not protection.

None of this logs the key or the token: errors carry a stable code (`reason`)
for machines and a message for people.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jwt

from persistence import Database
from settings import Settings, settings

log = logging.getLogger("aurora.security")

#: API key prefix: makes it obvious what they are when they show up in a log.
PREFIX_KEY = "ak_"
JWT_ALGORITHM = "HS256"
ISSUER_JWT = "aurora-shop-ops"
AUDIENCE_JWT = "aurora-api"
SECOND_DAY = 86_400
SCOPE_ADMIN = "admin:all"

#: The scopes an endpoint can require. They live here and not in `api.py` so
#: the CLI that creates API keys validates against the same list: if each side
#: had its own strings, a key created with `--scopes trazas` would be accepted
#: and the dashboard would later reject it with a 403.
SCOPE_CHAT = "chat:write"
SCOPE_TRACES = "traces:read"
SCOPE_APPROVALS = "approvals:write"
KNOWN_SCOPES = (SCOPE_CHAT, SCOPE_TRACES, SCOPE_APPROVALS, SCOPE_ADMIN)


# ---------------------------------------------------------------------- errors
class SecurityError(Exception):
    """Security error with an HTTP status code, a stable reason and headers."""

    code = 500
    reason = "error"

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if reason:
            self.reason = reason
        if code:
            self.code = code
        self.headers = headers or {}

    def to_response(self) -> dict:
        """JSON body for the client (without leaking internal details)."""
        return {
            "error": {
                "code": self.code,
                "reason": self.reason,
                "message": self.message,
            }
        }


class AuthenticationError(SecurityError):
    """Credential missing, invalid or expired (401)."""

    code = 401
    reason = "unauthenticated"


class AuthorizationError(SecurityError):
    """The credential is valid but not enough (403)."""

    code = 403
    reason = "without_permission"


class CSRFError(AuthorizationError):
    """The CSRF header of a write is missing or does not match (403)."""

    reason = "invalid_csrf"


class LimitError(SecurityError):
    """The plan quota was exceeded (429)."""

    code = 429
    reason = "limit_exceeded"

    def __init__(
        self,
        message: str,
        *,
        limit: int,
        window: str,
        retry_in_s: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        headers = headers or {}
        headers.setdefault("Retry-After", str(max(1, retry_in_s)))
        super().__init__(message, headers=headers)
        self.limit = limit
        self.window = window
        self.retry_in_s = retry_in_s


# -------------------------------------------------------------------- identity
@dataclass(frozen=True)
class Identity:
    """Who is calling: what travels inside the JWT."""

    client_id: str
    name: str = ""
    scopes: tuple[str, ...] = ()
    plan: str = "free"

    @classmethod
    def from_client(cls, client: dict) -> Identity:
        return cls(
            client_id=client["id"],
            name=client.get("name", ""),
            scopes=tuple(client.get("scopes") or ()),
            plan=client.get("plan") or "free",
        )

    def to_claims(self) -> dict:
        return {
            "sub": self.client_id,
            "name": self.name,
            "scopes": list(self.scopes),
            "plan": self.plan,
        }

    @classmethod
    def from_claims(cls, claims: dict) -> Identity:
        return cls(
            client_id=claims["sub"],
            name=claims.get("name", ""),
            scopes=tuple(claims.get("scopes") or ()),
            plan=claims.get("plan") or "free",
        )

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes or SCOPE_ADMIN in self.scopes

    def to_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "name": self.name,
            "scopes": list(self.scopes),
            "plan": self.plan,
        }


def require_scope(identity: Identity | None, scope: str) -> Identity:
    """Raise if the identity lacks the scope; return the identity otherwise."""
    if identity is None:
        raise AuthenticationError("Falta autenticación para esta operación.")
    if not identity.has_scope(scope):
        raise AuthorizationError(
            f"Falta el permiso '{scope}' para esta operación.",
            reason="without_permission",
        )
    return identity


# ------------------------------------------------------------------ API keys
@dataclass
class _Lock:
    """Consecutive failures from one origin, with its lock window."""

    failures: int = 0
    until: float = 0.0


class KeyManager:
    """Creation, verification and revocation of API keys (stored hashed)."""

    def __init__(
        self,
        db: Database,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_attempts: int | None = None,
        lock_s: int | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock
        self.max_attempts = max_attempts or self.settings.MAX_ATTEMPTS_PER_ORIGIN
        self.lock_s = lock_s or self.settings.BRUTE_FORCE_LOCK_S
        self._locks: dict[str, _Lock] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- creation
    def generate_key(
        self,
        *,
        name: str,
        scopes: list[str],
        plan: str = "free",
    ) -> dict:
        """Create a client and return its key **in plain text**, only once."""
        key = f"{PREFIX_KEY}{secrets.token_urlsafe(32)}"
        client = self.db.record_client(name, self._hash(key), scopes, plan)
        log.info("api_key created client=%s plan=%s scopes=%s", client["id"], plan, scopes)
        return {"key": key, "client": client}

    def revoke(self, client_id: str) -> bool:
        """Revoke the key: the client still exists, but the key no longer works."""
        ok = self.db.deactivate_client(client_id)
        log.warning("api_key revoked client=%s", client_id)
        return ok

    # ------------------------------------------------------------ verification
    def authenticate(self, key: str | None, *, origin: str = "unknown") -> dict | None:
        """Return the client if the key is valid, or None.

        `origin` (normally the IP) keeps the count of failed attempts: it is
        what stops brute force without locking out legitimate users.
        """
        if not key or not isinstance(key, str) or not key.startswith(PREFIX_KEY):
            self._notify_failure(origin)
            return None

        with self._lock:
            lock = self._locks.get(origin)
            if lock and lock.until > self.clock():
                raise AuthenticationError(
                    "Demasiados intentos fallidos desde tu origen. Probá más tarde.",
                    reason="too_many_attempts",
                    code=429,
                    headers={"Retry-After": str(int(lock.until - self.clock()))},
                )

        client = self.db.get_client_by_hash(self._hash(key))
        if client is None:
            self._notify_failure(origin)
            return None

        with self._lock:
            self._locks.pop(origin, None)
        log.debug("api_key verified client=%s", client["id"])
        return client

    def _notify_failure(self, origin: str) -> None:
        """Add a failure to the origin and lock it once it passes the maximum."""
        with self._lock:
            lock = self._locks.setdefault(origin, _Lock())
            lock.failures += 1
            if lock.failures >= self.max_attempts:
                lock.until = self.clock() + self.lock_s
                lock.failures = 0
                log.warning("origin locked for brute force origin=%s", origin)

    @staticmethod
    def _hash(key: str) -> str:
        """SHA-256 in hex: the key is never stored (or logged) in plain text."""
        return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------ JWT
class JWTIssuer:
    """Issue and verify the browser session tokens (HS256)."""

    def __init__(self, config: Settings | None = None) -> None:
        self.settings = config or settings

    def emit(self, identity: Identity, *, ttl_minutes: int | None = None) -> tuple[str, int]:
        """Return (token, ttl_in_seconds)."""
        minutes = ttl_minutes if ttl_minutes is not None else self.settings.JWT_TTL_MINUTES
        now = int(time.time())
        claims = {
            **identity.to_claims(),
            "iat": now,
            "nbf": now,
            "exp": now + int(minutes * 60),
            "jti": uuid.uuid4().hex,
            "iss": ISSUER_JWT,
            "aud": AUDIENCE_JWT,
        }
        token = jwt.encode(claims, self.settings.JWT_SECRET, algorithm=JWT_ALGORITHM)
        return token, int(minutes * 60)

    def verify(self, token: str | None) -> Identity:
        """Verify signature and validity. Fails with a stable reason and no detail.

        Telling "not a token" apart from "a tampered token" matters: the latter
        is someone trying to forge a session and must look different in the
        logs and in the dashboard.
        """
        if not token or not isinstance(token, str):
            raise AuthenticationError("Falta el token de sesión.", reason="without_token")
        try:
            jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as e:
            raise AuthenticationError(
                "Token de sesión malformado.", reason="malformed_token"
            ) from e
        try:
            claims = jwt.decode(
                token,
                self.settings.JWT_SECRET,
                algorithms=[JWT_ALGORITHM],
                audience=AUDIENCE_JWT,
                issuer=ISSUER_JWT,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as e:
            raise AuthenticationError("La sesión venció.", reason="expired") from e
        except jwt.InvalidTokenError as e:
            raise AuthenticationError(
                "Token de sesión inválido.", reason="signature_invalid"
            ) from e
        except (TypeError, ValueError) as e:
            raise AuthenticationError(
                "Token de sesión malformado.", reason="malformed_token"
            ) from e
        return Identity.from_claims(claims)


# --------------------------------------------------------------- cookies/CSRF
class SessionCookies:
    """`httpOnly` session cookie + CSRF cookie readable by the browser."""

    def __init__(
        self,
        issuer: JWTIssuer | None = None,
        config: Settings | None = None,
    ) -> None:
        self.issuer = issuer or JWTIssuer(config)
        self.settings = config or issuer.settings if issuer else settings

    # --------------------------------------------------------------- issuing
    def set(self, identity: Identity) -> dict[str, str]:
        """Return the session `Set-Cookie` values (token + CSRF)."""
        token, ttl = self.issuer.emit(identity)
        csrf = secrets.token_urlsafe(32)
        return {
            self.settings.COOKIE_NAME: self._cookie(
                self.settings.COOKIE_NAME, token, http_only=True, max_age=ttl
            ),
            self.settings.CSRF_COOKIE_NAME: self._cookie(
                self.settings.CSRF_COOKIE_NAME, csrf, http_only=False, max_age=ttl
            ),
        }

    def clean(self) -> dict[str, str]:
        """Emptied cookies for logout (same name, no value)."""
        is_csrf = self.settings.CSRF_COOKIE_NAME
        return {
            name: self._cookie(name, "", http_only=name != is_csrf, max_age=0)
            for name in (self.settings.COOKIE_NAME, is_csrf)
        }

    def _cookie(self, name: str, value: str, *, http_only: bool, max_age: int) -> str:
        parts = [
            f"{name}={value}",
            "Path=/",
            f"Max-Age={max_age}",
            f"SameSite={self.settings.COOKIE_SAMESITE.capitalize()}",
        ]
        if self.settings.COOKIE_SECURE:
            parts.append("Secure")
        if http_only:
            parts.append("HttpOnly")
        return "; ".join(parts)

    # --------------------------------------------------------------- reading
    def read(
        self,
        cookies: dict[str, str] | None,
        *,
        write: bool = False,
        headers: dict[str, str] | None = None,
    ) -> Identity:
        """Read the identity from the cookie; writes also require the CSRF header."""
        cookies = cookies or {}
        token = cookies.get(self.settings.COOKIE_NAME)
        if not token:
            raise AuthenticationError("Falta la cookie de sesión.", reason="without_token")
        identity = self.issuer.verify(token)
        if write:
            self._verify_csrf(cookies, headers or {})
        return identity

    def _verify_csrf(self, cookies: dict[str, str], headers: dict[str, str]) -> None:
        expected = cookies.get(self.settings.CSRF_COOKIE_NAME, "")
        sent = {k.lower(): v for k, v in headers.items()}.get(
            self.settings.CSRF_HEADER.lower(), ""
        )
        if not expected or not sent or not secrets.compare_digest(expected, sent):
            raise CSRFError(
                "Falta el token CSRF o no coincide con la cookie.",
                reason="invalid_csrf",
            )


# ------------------------------------------------------------- rate limiting
@dataclass
class Decision:
    """Result of consuming one unit of quota."""

    allowed: bool
    limit: int
    remaining: int
    restart_in_s: int
    window: str
    degraded: bool = False
    headers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "limit": self.limit,
            "remaining": self.remaining,
            "restart_in_s": self.restart_in_s,
            "window": self.window,
            "degraded": self.degraded,
        }


@dataclass
class _Counter:
    value: int
    restarts_in: float


class Limiter:
    """Per-client and per-plan quotas, with Redis and an in-memory fallback.

    The counter is a fixed window (the quota's own): `INCR` and, if the limit
    is exceeded, the `INCR` is undone so the number is not inflated. When Redis
    is unavailable counting continues in memory, so the quota is still applied.
    """

    #: (window name, seconds, limit) according to the plan quota
    def _windows(self, quota) -> list[tuple[str, int, int]]:
        return [
            ("minute", 60, quota.requests_per_minute),
            ("day", SECOND_DAY, quota.requests_per_day),
        ]

    #: How often to check whether Redis is back. A PING per request would punish
    #: healthy traffic because Redis went down once.
    POLL_REDIS_S = 30.0

    def __init__(
        self,
        *,
        config: Settings | None = None,
        redis: Any = None,
        clock: Callable[[], float] = time.monotonic,
        max_keys_memory: int | None = None,
    ) -> None:
        self.settings = config or settings
        self.clock = clock
        self.max_keys_memory = (
            max_keys_memory or self.settings.RATE_LIMIT_MAX_KEYS_MEMORY
        )
        self._memory: dict[tuple[str, str], _Counter] = {}
        self._lock = threading.Lock()
        self.degraded = False
        self.reason_degradation = ""
        self._last_poll = float("-inf")
        self.redis = redis if redis is not None else self._redis_from_settings()

    def _redis_from_settings(self) -> Any:
        if not self.settings.REDIS_URL:
            return None
        import redis.asyncio as redis_asyncio  # lazy import: not needed without Redis

        return redis_asyncio.from_url(self.settings.REDIS_URL, decode_responses=True)

    # ------------------------------------------------------------------ API
    async def record(self, key: str, plan: str = "free") -> Decision:
        """Consume one unit of quota; raise `LimitError` if it was exceeded."""
        if not self.settings.RATE_LIMIT_ENABLED:
            return Decision(True, 0, 0, 0, "disabled", headers={})

        quota = self.settings.quota_of(plan)
        usage: list[tuple[str, int, int, int]] = []  # (window, limit, used, restarts_in)
        if self.degraded and self.redis is not None:
            await self._poll_redis()

        charged_ids: list[str] = []
        for window, seconds, limit in self._windows(quota):
            id_window = f"rl:{window}:{key}"
            used, restarts_in = await self._increment(id_window, seconds)
            charged_ids.append(id_window)
            if used > limit:
                # Undo every window already charged, not just this one. Before,
                # a rejection by the daily quota left the per-minute increment
                # charged: the client lost quota it still had free, and when the
                # minute restarted it found the counter inflated.
                for charged_id in charged_ids:
                    await self._undo(charged_id)
                raise LimitError(
                    f"Pasaste la cuota de {plan} ({limit} req/{window}).",
                    limit=limit,
                    window=window,
                    retry_in_s=max(1, int(restarts_in - self.clock())),
                    headers=self._headers(limit, 0, int(restarts_in - self.clock())),
                )
            usage.append((window, limit, used, int(restarts_in - self.clock())))

        # The header shows the tightest window: the one with the fewest free
        # requests, which is the one the client will hit first.
        window, limit, _, restart = min(usage, key=lambda c: c[1] - c[2])
        remaining = max(0, limit - {c[0]: c[2] for c in usage}[window])
        return Decision(
            allowed=True,
            limit=limit,
            remaining=remaining,
            restart_in_s=max(0, restart),
            window=window,
            degraded=self.degraded,
            headers=self._headers(limit, remaining, restart),
        )

    async def close(self) -> None:
        """Close the Redis connection (on app shutdown)."""
        if self.redis is not None and hasattr(self.redis, "aclose"):
            await self.redis.aclose()

    def keys_in_memory(self) -> int:
        return len(self._memory)

    # ------------------------------------------------------------- internals
    async def _increment(self, key: str, seconds: int) -> tuple[int, float]:
        """Add 1 to the counter and return (value, instant at which it resets)."""
        if self.redis is not None:
            try:
                value = int(await self.redis.incr(key))
                if value == 1:
                    await self.redis.expire(key, seconds)
                restart = self.clock() + int(await self.redis.ttl(key))
                return value, restart
            except Exception as e:  # any Redis failure degrades, it does not break the request
                self._degrade(f"{type(e).__name__}: {e}")
        return self._increment_in_memory(key, seconds)

    async def _undo(self, key: str) -> None:
        if self.redis is not None and not self.degraded:
            try:
                await self.redis.decr(key)
                return
            except Exception as e:  # if Redis fails while undoing, memory wins
                log.debug("redis unavailable while undoing a counter: %s", e)
        with self._lock:
            counter = self._memory.get(self._key_memory(key))
            if counter:
                counter.value = max(0, counter.value - 1)

    def _increment_in_memory(self, key: str, seconds: int) -> tuple[int, float]:
        now = self.clock()
        with self._lock:
            # Prune before inserting: if the counter we are about to increment
            # were evicted, the client would regain quota and could bypass the limit.
            self._prune(now)
            id_memory = self._key_memory(key)
            counter = self._memory.get(id_memory)
            if counter is None or counter.restarts_in <= now:
                counter = _Counter(value=0, restarts_in=now + seconds)
            counter.value += 1
            self._memory[id_memory] = counter
            return counter.value, counter.restarts_in

    def _key_memory(self, key: str) -> tuple[str, str]:
        window = key.split(":")[1] if key.count(":") > 1 else "minute"
        return (key, window)

    def _prune(self, now: float) -> None:
        """Drop expired counters and, if still over the cap, those expiring soonest.

        Leaves a couple of free slots: a request opens up to two windows
        (minute and day), and both must fit after pruning.
        """
        for id_memory in [k for k, v in self._memory.items() if v.restarts_in <= now]:
            del self._memory[id_memory]
        slack = 2
        target = max(1, self.max_keys_memory - slack)
        if len(self._memory) <= target:
            return
        ordered = sorted(self._memory.items(), key=lambda kv: kv[1].restarts_in)
        for id_memory, _ in ordered[: len(self._memory) - target]:
            del self._memory[id_memory]
        if len(self._memory) <= self.max_keys_memory:
            return
        ordered = sorted(self._memory.items(), key=lambda kv: kv[1].restarts_in)
        for id_memory, _ in ordered[: len(self._memory) - self.max_keys_memory]:
            del self._memory[id_memory]

    def _degrade(self, reason: str) -> None:
        if not self.degraded:
            log.error("rate limiting degraded to memory: %s", reason)
        self.degraded = True
        self.reason_degradation = reason

    async def _poll_redis(self) -> bool:
        """Check whether Redis is available again. One PING every 30s.

        Without this, a single Redis failure left the process in memory mode
        forever: the quota kept counting (that is fine), but `degraded` no
        longer told the truth and the shared counter was never used again, so
        with several replicas each one would keep its own.
        """
        if self.redis is None or not self.degraded:
            return False
        if self.clock() - self._last_poll < self.POLL_REDIS_S:
            return False
        self._last_poll = self.clock()
        try:
            await self.redis.ping()
        except Exception as e:  # still down: a later request will retry
            log.debug("redis still not responding: %s", e)
            return False
        self.degraded = False
        self.reason_degradation = ""
        log.info("redis is back: rate limiting counts on the shared counter again")
        return True

    @staticmethod
    def _headers(limit: int, remaining: int, restart_in_s: int) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(max(0, remaining)),
            "X-RateLimit-Reset": str(max(0, restart_in_s)),
        }
