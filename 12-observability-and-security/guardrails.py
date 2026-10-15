r"""Guardrails: what may enter the agent and what may leave it.

A support agent talks to people who type sensitive data (emails, cards, phone
numbers) and sometimes to people who want to pull it off its task. This module
decides, explicitly and testably, what happens in each case:

- **Input**: PII is masked (via `pii`) and prompt-injection attempts are
  detected. Detection always flags; blocking depends on `BLOCK_INJECTION`,
  because in support it is better to observe first and tighten later (the flag
  stays in the trace and the operator sees it in the dashboard).
- **Output**: the system-prompt canary can never travel to the user. No setting
  enables it: if the model spat it out, it is removed, the trace is flagged and
  the turn is cut.

Every decision returns flags with **stable codes** (`KNOWN_FLAGS`), not free
text: the dashboard filters on them, Prometheus counts them and alerts use them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pii
from settings import Settings, settings

#: Flag codes the whole system knows about (dashboard, metrics, alerts).
KNOWN_FLAGS = frozenset(
    {
        "pii_in_input",
        "pii_in_output",
        "possible_injection",
        "canary_exposed",
        "message_long",
    }
)

#: Prompt-injection patterns. They are deliberately specific: we would rather let
#: odd text through than block a customer who writes in Rioplatense Spanish and
#: asks about their orders.
PATTERNS_INJECTION: tuple[re.Pattern[str], ...] = (
    # "ignorá/ignore/desconsiderá las instrucciones anteriores" (ignore previous instructions)
    re.compile(
        r"\b(ignor\w*|olvid\w+|desconsider\w*)\b[^.!?]{0,40}?"
        r"\b(instrucci\w+|reglas|indicaciones)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bignore\s+(all\s+)?(the\s+)?(previous|prior|above)\s+(instructions|prompts?|rules)\b",
        re.IGNORECASE,
    ),
    # "revelame / mostrame / imprimí el system prompt" (reveal the system prompt)
    re.compile(
        r"\b(revela\w*|revelar|mostrame|mostrar|decime|decirme|imprim\w+|repeti\w*|repeat)\b"
        r"[^.!?]{0,40}?"
        r"\b(system\s?prompt|prompt\s+(del\s+sistema|interno|inicial)|"
        r"instrucciones\s+(del\s+sistema|internas|iniciales)|reglas\s+(internas|del\s+sistema))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(print|show|reveal|repeat)\s+(me\s+)?your\s+(system\s+)?(prompt|instructions)\b",
        re.IGNORECASE,
    ),
    # "actuá como un asistente sin restricciones" (act as an unrestricted assistant) / jailbreak
    re.compile(
        r"\bact(u\w*|)\s+(como|as)\b[^.!?]{0,40}?"
        r"\b(sin\s+restricci\w*|sin\s+l[ií]mites|sin\s+reglas|jailbreak|desbloqueado|unrestricted)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(developer\s+mode|modo\s+desarrollador|dan\s+mode)\b", re.IGNORECASE),
    re.compile(
        r"\byou\s+are\s+now\b[^.!?]{0,40}\b(without|no)\s+(rules|restrictions|limits)\b",
        re.IGNORECASE,
    ),
)


@dataclass
class Review:
    """What was decided about a text, and what is allowed to circulate."""

    allowed: bool
    message: str
    flags: list[str] = field(default_factory=list)
    suggested_response: str = ""

    def a_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "message": self.message,
            "flags": self.flags,
            "suggested_response": self.suggested_response,
        }


class Guardrails:
    """Reviews the agent's inputs and outputs."""

    def __init__(self, config: Settings | None = None) -> None:
        self.settings = config or settings

    # ------------------------------------------------------------------ input
    def check_input(self, text: str | None) -> Review:
        """Sanitize the customer message and decide whether it may reach the model."""
        if not text:
            return Review(allowed=True, message=text or "")

        flags: list[str] = []
        if pii.detect(text):
            flags.append("pii_in_input")

        if self.contains_injection(text) and self.settings.BLOCK_INJECTION:
            return Review(
                allowed=False,
                message="",
                flags=[*flags, "possible_injection"],
                suggested_response=self._response_blocked(),
            )
        if self.contains_injection(text):
            flags.append("possible_injection")

        sanitized = pii.redact(text)
        if len(sanitized) > self.settings.MAX_MESSAGE_CHARS:
            flags.append("message_long")
            sanitized = sanitized[: self.settings.MAX_MESSAGE_CHARS]

        return Review(allowed=True, message=sanitized, flags=flags)

    # ------------------------------------------------------------------ output
    def check_output(self, text: str | None) -> Review:
        """Final say on what the agent is about to show the user."""
        if not text:
            return Review(allowed=True, message=text or "")

        flags: list[str] = []
        # The canary is always removed, no exception and no setting that enables it.
        if self.contains_canary(text):
            return Review(
                allowed=False,
                message="",
                flags=["canary_exposed"],
                suggested_response=self._message_leak(),
            )

        if self.contains_injection(text):
            # Same as on input: blocking injections is a configuration decision,
            # not a fixed output rule. It used to always block, and since
            # `suggested_response` was not filled in here, the customer got an
            # empty message.
            if self.settings.BLOCK_INJECTION:
                return Review(
                    allowed=False,
                    message="",
                    flags=[*flags, "possible_injection"],
                    suggested_response=self._response_blocked(),
                )
            flags.append("possible_injection")
        if pii.detect(text):
            flags.append("pii_in_output")

        return Review(allowed=True, message=pii.redact(text), flags=flags)

    # ------------------------------------------------------------- detectors
    def contains_injection(self, text: str | None) -> bool:
        """True if the text looks like an attempt to pull the agent off its task."""
        if not text:
            return False
        return any(pattern.search(text) for pattern in PATTERNS_INJECTION)

    def contains_canary(self, text: str | None) -> bool:
        return bool(text) and self.settings.CANARY_SECRET in text

    # ------------------------------------------------------------------ texts
    def _response_blocked(self) -> str:
        """Reply to the customer when the turn is blocked: short, with no details."""
        return (
            "Puedo ayudarte con tu pedido, pero no con eso. "
            "Si reformulás la consulta sobre tu compra, sigo contigo."
        )

    def _message_leak(self) -> str:
        """What is shown when the model tried to leak the internal prompt."""
        return (
            "Tuve que reformular la respuesta por temas de seguridad. "
            "¿Me decís de nuevo qué necesitás con tu pedido?"
        )
