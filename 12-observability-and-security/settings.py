r"""Central configuration for Project 12 (read from the environment, validated at startup).

A single place for everything that can change per deployment: secrets,
per-plan quotas, rate-limiting window, data retention, PII redaction,
per-model prices, allowed origins, etc. It is read with pydantic-settings, so a
badly typed or inconsistent value **fails at startup** and not halfway
through a request.

    from settings import settings

    print(settings.JWT_TTL_MINUTES)

Security rules validated here: with ENVIRONMENT=production the JWT secret must
have been changed (no development defaults) and the session cookie must go
over HTTPS.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Development secret: fine for running locally, but invalid in production
# (that is why `validate_production` rejects it).
SECRET_DEVELOPMENT = "desarrollo-cambiar-en-produccion"


class PlanQuota(BaseModel):
    """Limits of a commercial plan: requests per minute/day and tokens per day."""

    description: str = ""
    requests_per_minute: int = Field(gt=0)
    requests_per_day: int = Field(gt=0)
    tokens_per_day: int = Field(gt=0)


#: Reference prices in USD per million tokens (input, output).
#: Models not in the table have NO known price: they are reported as "not
#: costable" instead of inventing a number (see model.price_per_million).
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


class Settings(BaseSettings):
    """Service configuration, populated from the environment and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------- application
    APP_NAME: str = "Aurora Shop Ops"
    ENVIRONMENT: Literal["development", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    # ------------------------------------------------------------- persistence
    DB_PATH: Path = Path("agent.db")
    CHECKPOINTER_PERSISTENT: bool = True
    RETENTION_DAYS: int = Field(default=30, ge=0)
    REDACT_PII: bool = True

    # ------------------------------------------------------------- authentication
    JWT_SECRET: str = SECRET_DEVELOPMENT
    JWT_TTL_MINUTES: int = Field(default=30, ge=1)
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "strict"
    COOKIE_DOMAIN: str | None = None
    COOKIE_NAME: str = "agent_session"
    CSRF_COOKIE_NAME: str = "csrf"
    CSRF_HEADER: str = "X-CSRF-Token"
    #: Networks from which `X-Forwarded-For` is trusted. From anywhere else the
    #: header can be set by anyone and is ignored: otherwise an attacker picks
    #: their own IP and evades the brute-force lock at will. Defaults to loopback
    #: and private networks, which is what sits behind nginx (the API is not
    #: published). If you publish port 8000, limit this to the real proxy IP.
    TRUSTED_PROXIES: list[str] = Field(
        default_factory=lambda: [
            "127.0.0.1",
            "::1",
            "172.16.0.0/12",
            "10.0.0.0/8",
            "192.168.0.0/16",
        ]
    )
    MAX_ATTEMPTS_PER_ORIGIN: int = Field(default=5, ge=1)
    BRUTE_FORCE_LOCK_S: int = Field(default=300, ge=1)

    # ------------------------------------------------------------- rate limiting
    RATE_LIMIT_ENABLED: bool = True
    #: Cap on keys held in the in-memory fallback (in case Redis never comes back).
    RATE_LIMIT_MAX_KEYS_MEMORY: int = Field(default=10_000, ge=100)
    REDIS_URL: str | None = None
    PLANS: dict[str, PlanQuota] = Field(
        default_factory=lambda: {
            "free": PlanQuota(
                description="Plan de entrada",
                requests_per_minute=10,
                requests_per_day=100,
                tokens_per_day=50_000,
            ),
            "pro": PlanQuota(
                description="Plan de uso intensivo",
                requests_per_minute=60,
                requests_per_day=1_000,
                tokens_per_day=500_000,
            ),
        }
    )

    # ------------------------------------------------------------- observability
    PRICES_USD_PER_MILLION: dict[str, dict[str, float]] = Field(
        default_factory=lambda: dict(DEFAULT_PRICES)
    )

    # ------------------------------------------------------------- guardrails
    CANARY_PROMPT_LEAK: bool = True
    CANARY_SECRET: str = "AURORA-SISTEMA-INTERNO-7f3a"
    BLOCK_INJECTION: bool = False
    REFUND_AMOUNT_LIMIT: float = Field(default=50_000.0, gt=0)
    MAX_MESSAGE_CHARS: int = Field(default=4_000, ge=1)
    MAX_HISTORY_TURNS: int = Field(default=40, ge=2)

    # ------------------------------------------------------------- web
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )

    @model_validator(mode="after")
    def validate_production(self) -> Settings:
        """In production, do not let development values slip through by accident."""
        if self.ENVIRONMENT != "production":
            return self
        if self.JWT_SECRET == SECRET_DEVELOPMENT:
            raise ValueError(
                "JWT_SECRET tiene el valor por defecto de desarrollo: "
                "fijalo en el entorno (o en el gestor de secretos) antes de producción."
            )
        if not self.COOKIE_SECURE:
            raise ValueError(
                "COOKIE_SECURE debe ir en true en producción: la sesión viaja por HTTPS."
            )
        if self.COOKIE_SAMESITE == "none" and not self.COOKIE_SECURE:
            raise ValueError("COOKIE_SAMESITE=none exige COOKIE_SECURE=true.")
        return self

    # ------------------------------------------------------------- helpers
    def quota_of(self, plan: str) -> PlanQuota:
        """Return the quota of a plan; if it does not exist, that of the `free` plan."""
        return self.PLANS.get(plan) or self.PLANS.get("free") or next(iter(self.PLANS.values()))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process configuration (cached: read once)."""
    return Settings()


#: Shortcut `settings.FIELD`, used across the rest of the modules.
settings = get_settings()
