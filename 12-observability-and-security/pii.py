r"""Detection and masking of personal data (PII).

A single implementation for the two places where it matters: what the **model
sees** and what is **saved to disk**. With two different rule sets we would end
up with sensitive data leaking on one side and not on the other.

The policy is deliberately conservative: we prefer masking too much to leaving
a sensitive value loose. The text stays readable for the agent
(`[email:oculto]`), so it can keep serving the customer, but the real value
never leaves this module.
"""

from __future__ import annotations

import re

#: (name, pattern) in the order they are applied: the most specific first.
#: Otherwise a 16-digit card would be read as a phone number.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email",
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    ),
    (
        "tarjeta",
        # 13 to 19 digits, with optional spaces or hyphens as separators.
        re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)"),
    ),
    (
        "dni",
        # Argentine DNI: 7-8 digits + letter.
        re.compile(r"(?<!\d)\d{7,8}[A-Z](?![A-Za-z])"),
    ),
    (
        "telefono",
        re.compile(r"(?<!\d)\+?\d[\d \-]{8,14}\d(?!\d)"),
    ),
)


def redact(text: str | None) -> str | None:
    """Mask PII while keeping the rest of the text."""
    if not text:
        return text
    masked = text
    for name, pattern in PATTERNS:
        masked = pattern.sub(f"[{name}:oculto]", masked)
    return masked


def detect(text: str | None) -> list[str]:
    """Names of the PII types present in the text (for alerting and measuring)."""
    if not text:
        return []
    return [name for name, pattern in PATTERNS if pattern.search(text)]
