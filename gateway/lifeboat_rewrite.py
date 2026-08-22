"""Reject a bad Life-Boat draft, then ask the model for a better one.

An independent reviewer already existed here but was never wired in, and when
it rejected a reply it substituted a sentence of its own -- including a stock
coaching question, the exact behaviour deleted under BUG-6. Its judgement is
worth keeping. Its replacements are not: prose written by the gate is how the
same sentence ended up in the conversation eight times in one afternoon.

So the reviewer returns a verdict and nothing else. A rejected draft goes back
to the model with the specific problem named, once. If that rewrite is
unavailable, empty, or fails review in turn, the model's own words are
delivered and the outcome is recorded. Never a sentence this module invented,
and never silently.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from gateway.lifeboat_reviewer import review_lifeboat_response_with_timeout


logger = logging.getLogger(__name__)

#: One retry only. A second rewrite costs another round trip on every bad draft
#: and, in practice, a model that misses twice is not about to find it.
_MAX_REWRITES = 1

_REWRITE_SYSTEM = (
    "You are revising one reply in an ongoing Hebrew-language emotional support "
    "conversation. A reviewer rejected the draft below for a specific reason. "
    "Write a better reply to the same user message.\n"
    "\n"
    "Keep the user's own words and specifics. Stay with what they actually "
    "raised. Keep any interpretation tentative. Do not summarise, do not close "
    "the topic, and do not offer a menu of options. Reply only with the "
    "revised message -- no preamble, no explanation, no quotation marks."
)

#: What each rejection reason means, in terms a writer can act on. No example
#: wording appears here on purpose: hand the model a sentence and it will use
#: it, which is how a template returns through the back door.
_REASON_GUIDANCE = {
    "premature_closure": "The draft closes the conversation. Leave the thread open.",
    "responsibility_handoff": "The draft hands the work back vaguely instead of staying with it.",
    "bounded_decomposition_not_advanced": "The draft asks for another layer instead of advancing what is already described.",
    "invented_action_stage": "The draft invents an action or stage the user never mentioned.",
    "unsupported_safety_escalation": "The draft escalates to safety language the user's message does not support.",
    "missing_human_safety_support": "There are danger signs and the draft offers no route to human support.",
    "contextless_reentry": "The draft re-enters with a question that names nothing from this conversation.",
    "feared_interpretation_as_fact": "The draft states the user's fear as established fact.",
    "reasked_supplied_relationship_evidence": "The draft asks again for something the user already supplied.",
    "epistemic_caution_erased_grounded_knowledge": "The draft treats what is genuinely known as unknowable.",
    "therapeutic_gibberish_in_repair": "The draft is abstract therapeutic language instead of plain speech.",
    "review_timeout": "The reviewer did not finish in time.",
    "review_error": "The reviewer failed.",
}


@dataclass(frozen=True)
class LifeBoatVerdict:
    """A judgement about one draft. Deliberately carries no replacement text."""

    accepted: bool
    reason: str
    receipt: str


def review_verdict(user_text: str, response: str) -> LifeBoatVerdict:
    """Review a draft and return only the judgement."""
    result = review_lifeboat_response_with_timeout(user_text or "", response or "")
    return LifeBoatVerdict(
        accepted=bool(result.accepted),
        reason=str(result.reason or ""),
        receipt=str(result.receipt or ""),
    )


def build_rewrite_messages(user_text: str, draft: str, reason: str) -> list[dict[str, str]]:
    """Build the request that sends a rejected draft back to the model."""
    guidance = _REASON_GUIDANCE.get(reason, "The draft did not meet the reply contract.")
    return [
        {"role": "system", "content": _REWRITE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"The user wrote:\n{user_text}\n\n"
                f"The rejected draft was:\n{draft}\n\n"
                f"Reviewer reason: {reason}. {guidance}\n\n"
                "Write the revised reply."
            ),
        },
    ]


def resolve_reply(
    user_text: str,
    draft: str,
    *,
    rewrite: Callable[[list[dict[str, str]]], str],
) -> tuple[str, str]:
    """Return the text to deliver and why, after review and at most one rewrite.

    ``rewrite`` is injected so the decision is testable without a model: it
    receives the request messages and returns the model's revised text.
    """
    text = str(draft or "")
    if not text.strip():
        return text, "accepted"

    verdict = review_verdict(user_text, text)
    if verdict.accepted:
        return text, "accepted"

    logger.info("Life-Boat reviewer rejected a draft %s", verdict.receipt)

    for _ in range(_MAX_REWRITES):
        try:
            revised = str(rewrite(build_rewrite_messages(user_text, text, verdict.reason)) or "")
        except Exception as exc:
            logger.warning(
                "Life-Boat rewrite unavailable reason=%s error=%s message_content=redacted",
                verdict.reason,
                type(exc).__name__,
            )
            return text, "rewrite_unavailable"

        if not revised.strip():
            logger.warning(
                "Life-Boat rewrite returned nothing reason=%s message_content=redacted",
                verdict.reason,
            )
            return text, "rewrite_unavailable"

        second = review_verdict(user_text, revised)
        if second.accepted:
            logger.info("Life-Boat rewrite accepted %s", second.receipt)
            return revised, "rewritten"

        # The model was asked and answered. Its words go out, not ours.
        logger.info(
            "Life-Boat rewrite_rejected first=%s second=%s message_content=redacted",
            verdict.reason,
            second.reason,
        )
        return revised, "rewrite_rejected"

    return text, "rewrite_unavailable"
