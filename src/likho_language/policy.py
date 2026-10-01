"""Which language to decode a call as, given what the speech model detected."""

from collections.abc import Sequence
from dataclasses import dataclass

ANY_LANGUAGE = "*"


@dataclass(frozen=True)
class PolicyRule:
    detected_language: str  # ISO 639-1 code, or "*" for any language
    min_probability: float
    decode_as: str
    transliterate: bool


@dataclass(frozen=True)
class Decision:
    decode_as: str
    transliterate: bool


# Used for a workspace that has not defined its own rules.
#   * Clearly English calls stay English and are not transliterated.
#   * Everything else, including Urdu, is decoded as Hindi: the model then writes Devanagari,
#     which the Hinglish rules understand. Hindi with many English words still counts as Hindi.
DEFAULT_RULES: tuple[PolicyRule, ...] = (
    PolicyRule(detected_language="en", min_probability=0.80, decode_as="en", transliterate=False),
    PolicyRule(detected_language=ANY_LANGUAGE, min_probability=0.0, decode_as="hi", transliterate=True),
)


def resolve(detected: str, probability: float, rules: Sequence[PolicyRule] = DEFAULT_RULES) -> Decision:
    """Apply the first rule that matches; fall back to the default rules if none does."""
    detected = detected.strip().lower()
    for rule in (*rules, *DEFAULT_RULES):
        if rule.detected_language in (ANY_LANGUAGE, detected) and probability >= rule.min_probability:
            return Decision(decode_as=rule.decode_as, transliterate=rule.transliterate)
    raise AssertionError("the default rules end with a catch-all")  # pragma: no cover
