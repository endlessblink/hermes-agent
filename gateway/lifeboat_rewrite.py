"""Decide which words the Life-Boat topic actually delivers.

An independent reviewer already existed here but was never wired in, and when
it rejected a reply it substituted a sentence of its own -- including a stock
coaching question, the exact behaviour deleted under BUG-6. Its judgement is
worth keeping. Its replacements were not: prose written by the gate is how the
same sentence ended up in the conversation eight times in one afternoon.

For a while the conclusion drawn from that was "never write anything", and it
left the gate able to reject and unable to improve. Worse, the replies that
failed him were never rejected at all -- a gentle question about his life in
general breaks no rule and carries no thought about him. The reviewer now
inspects every draft, while the editor writes only after a concrete failure.

The protection is kept and moved rather than dropped. This module holds no
sentence of any reply. An edit that fails review never replaces a draft that
passed. When nothing survives, this module returns an empty result so the
delivery boundary can suppress it rather than leak a rejected draft.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Callable

from gateway.lifeboat_reviewer import review_lifeboat_response_with_timeout


logger = logging.getLogger(__name__)

#: Bound repair work to two attempts total. Every attempt is re-reviewed before
#: it can become the delivered reply.
_MAX_REWRITES = 2
_BARE_ACKS = frozenset({
    "כן", "אוקיי", "אוקי", "בסדר", "טוב", "sure", "ok", "okay", "yes",
    "👍", "👌", "✅", "🙏", "🙂", "🙌",
})

_REWRITE_SYSTEM = (
    "You are revising one reply in an ongoing Hebrew-language emotional support "
    "conversation. A reviewer rejected the draft below for a specific reason. "
    "Write a better reply to the same user message.\n"
    "\n"
    "Keep the user's own words and specifics. Stay with what they actually "
    "raised. Keep any interpretation tentative. Do not summarise, do not close "
    "the topic, and do not offer a menu of options. Do the work either by making "
    "a modest hedged read or by choosing one concrete next step yourself; do not "
    "hand the choice back to him. Reply only with the "
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
    "absence_of_care_treated_as_proof": "The draft treats not having received care as proof that none exists. Those are different claims.",
    "asked_user_to_justify_their_worth": "The draft asks the user to produce a reason they deserve connection.",
    "asserted_another_persons_inner_state": "The draft states what someone else thinks or feels as established fact. Their motives are not observable.",
    "handed_his_decision_to_someone_else": "The draft hands his decision to another person to make or confirm.",
    "mirrored_the_feeling_without_moving": "The draft names the feeling and stops, which confirms the state and opens nothing.",
    "asked_him_to_rate_himself": "The draft asks for a rating or a number. A number is not a feeling, and asking for one turns a check-in into a form.",
    "offered_a_menu_of_support_options": "The draft offers a choice between ways of being supported. An open door is an invitation to continue, not a pair of buttons.",
    "decomposed_his_experience_into_a_list": "The draft breaks what he said into a numbered or bulleted list, which turns his experience into a diagram of itself.",
    "reasked_supplied_relationship_evidence": "The draft asks again for something the user already supplied.",
    "epistemic_caution_erased_grounded_knowledge": "The draft treats what is genuinely known as unknowable.",
    "therapeutic_gibberish_in_repair": "The draft is abstract therapeutic language instead of plain speech.",
    "interior_interrogation": (
        "The draft asks him to report what went on inside him without offering "
        "any guess of its own. His interior is what you are supposed to think "
        "about. Say what you believe was going on in him, hedged so one word "
        "from him can knock it down, and ask him to confirm that instead."
    ),
    "unsupported_temporal_anchor": (
        "The draft names a time he did not mention. Use his own anchor, or none."
    ),
    "therapist_handoff": (
        "The draft substitutes relational filler for a reply that does something."
    ),
    "review_timeout": "The reviewer did not finish in time.",
    "review_error": "The reviewer failed.",
    "repeated_request": "The draft asks again for information already present in the recent conversation.",
    "invented_user_goal": "The draft assigns the user a goal or topic they did not state.",
    "responsibility_handoff": "The draft makes the user choose the direction instead of taking a reasonable next step.",
    "not_a_concrete_continuation": "The draft does not continue the latest event with a useful next step.",
    "semantic_review_unavailable": "The semantic continuity check was unavailable; do not deliver an unchecked repair.",
}


@dataclass(frozen=True)
class LifeBoatVerdict:
    """A judgement about one draft. Deliberately carries no replacement text."""

    accepted: bool
    reason: str
    receipt: str


def review_verdict(
    user_text: str,
    response: str,
    *,
    material: str = "",
    debrief_active: bool = False,
) -> LifeBoatVerdict:
    """Review a draft and return only the judgement."""
    result = review_lifeboat_response_with_timeout(
        user_text or "",
        response or "",
        evidence_text=material,
        debrief_active=debrief_active,
    )
    return LifeBoatVerdict(
        accepted=bool(result.accepted),
        reason=str(result.reason or ""),
        receipt=str(result.receipt or ""),
    )


def build_rewrite_messages(user_text: str, draft: str, reason: str) -> list[dict[str, str]]:
    """Build the request that sends a rejected draft back to the model.

    This is the third writer in the chain, and it was the one left speaking as
    nobody. The turn guidance and the editor were both given an identity; this
    path was not, so a rejected draft came back in the careful register of a
    generic support assistant no matter who the bot was supposed to be -- and
    on a rejected draft, this is the voice that reaches him.
    """
    guidance = _REASON_GUIDANCE.get(reason, "The draft did not meet the reply contract.")
    system = _REWRITE_SYSTEM
    try:
        from gateway.lifeboat_voice import load_voice_text

        voice = load_voice_text()
    except Exception:
        voice = ""
    if voice:
        system = f"{voice}\n\n{system}"
    return [
        {"role": "system", "content": system},
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
    edit: Callable[[list[dict[str, str]]], str] | None = None,
    material: str = "",
    profile_home=None,
    session_key: str = "",
    deliveries: int = 1,
    semantic_checker: Callable[[list[dict[str, str]]], str] | None = None,
    semantic_enforce: bool = False,
    recent_turns: list[dict[str, str]] | tuple[dict[str, str], ...] = (),
    trusted_state: str = "",
) -> tuple[str, str]:
    """Return the text to deliver and why.

    The order matters and is the whole point of this module. The editor runs on
    *every* draft, not only on one the reviewer rejected, because the replies
    that actually failed him passed every rule: a gentle, well-formed question
    about his life in general breaks nothing and says nothing. A gate that only
    blocks cannot reach that reply, and each rule added to make it try pushed
    the model further toward emptiness.

    Two guards keep an editor from being worse than no editor. It never
    replaces a draft that passed review with one that does not -- so the worst
    case of editing a good reply is the good reply. And when nothing survives
    review, the bot says plainly that it has no read rather than delivering a
    third failed attempt; that admission is rate limited so it cannot become a
    repeated line.

    ``rewrite`` and ``edit`` are injected so every branch is testable without a
    model call.
    """
    text = str(draft or "")
    if not text.strip():
        return text, "accepted"

    def semantic_failure(candidate: str) -> str:
        normalized_user = re.sub(r"[.!?؟]+$", "", str(user_text or "").strip()).casefold()
        normalized_candidate = re.sub(r"[.!?؟]+$", "", str(candidate or "").strip()).casefold()
        if normalized_user in _BARE_ACKS and normalized_candidate in _BARE_ACKS:
            return "not_a_concrete_continuation"
        if semantic_checker is None:
            return ""
        from gateway.lifeboat_semantic_gate import run_semantic_shadow

        shadow = run_semantic_shadow(
            semantic_checker,
            user_text,
            candidate,
            recent_turns=recent_turns,
            trusted_state=trusted_state or material,
        )
        if shadow.verdict is None:
            logger.warning("Life-Boat semantic shadow failed error=%s enforce=%s", shadow.error or "unknown", semantic_enforce)
            return "semantic_review_unavailable" if semantic_enforce else ""
        semantic_verdict = shadow.verdict
        logger.info(
            "Life-Boat semantic shadow pass=%s failures=%s evidence_turn_ids=%s enforce=%s",
            semantic_verdict.passed,
            ",".join(semantic_verdict.failures) or "none",
            ",".join(semantic_verdict.evidence_turn_ids) or "none",
            semantic_enforce,
        )
        if not semantic_enforce:
            return ""
        return semantic_verdict.failures[0] if semantic_verdict.failures else ""

    semantic_reason = semantic_failure(text)

    from gateway.lifeboat_debrief import (
        DebriefState,
        is_broad_debrief,
        load_debrief_state,
        save_debrief_state,
    )

    debrief_active = is_broad_debrief(user_text)
    if profile_home is not None:
        prior_state = load_debrief_state(profile_home, session_key)
        debrief_active = debrief_active or prior_state.active
        if debrief_active and not prior_state.active:
            save_debrief_state(profile_home, session_key, DebriefState(active=True))

    verdict = review_verdict(
        user_text,
        text,
        material=material,
        debrief_active=debrief_active,
    )
    unsafe_reason = ""
    if verdict.accepted:
        from gateway.lifeboat_editor import unsafe_draft_reason

        unsafe_reason = unsafe_draft_reason(text, user_text)

    # An accepted draft is already on the safe side of the contract. Do not
    # invite a second model to make it more fluent: that is how a good draft
    # became a therapist-like handback and how unsupported anchors entered a
    # conversation. Editing is reserved for a concrete review failure or a
    # deterministic unsafe-draft finding; the original remains the fallback if
    # the edit is unavailable or still fails review.
    should_edit = edit is not None and (
        not verdict.accepted or bool(unsafe_reason) or bool(semantic_reason)
    )
    if should_edit:
        from gateway.lifeboat_editor import edit_reply, unsafe_draft_reason

        candidate = text
        for edit_round in range(2):
            result = edit_reply(
                user_text,
                candidate,
                edit=edit,
                material=material,
                reason=semantic_reason or unsafe_reason or verdict.reason,
            )
            if not result.available or not result.changed:
                break
            candidate = result.text
            edited_verdict = review_verdict(
                user_text,
                candidate,
                material=material,
                debrief_active=debrief_active,
            )
            edited_unsafe = unsafe_draft_reason(candidate, user_text, material)
            edited_semantic = semantic_failure(candidate)
            if edited_verdict.accepted and not edited_unsafe and not edited_semantic:
                logger.info(
                    "Life-Boat editor rewrote a draft round=%s draft_accepted=%s %s",
                    edit_round + 1,
                    verdict.accepted,
                    edited_verdict.receipt,
                )
                return candidate, "edited"
            verdict = edited_verdict
            unsafe_reason = edited_unsafe
            semantic_reason = edited_semantic

        # If the independent semantic check still passes and no deterministic
        # safety guard fired, preserve the main model's reply rather than make
        # the bot disappear because a repair model missed twice. The reviewer
        # remains observable in the outcome; delivery stays responsive.
        # Never turn a nonempty answer into silence. The model's original text
        # is the last-resort answer; the failure remains visible in telemetry.
        if semantic_reason == "not_a_concrete_continuation":
            try:
                repaired = str(
                    rewrite(build_rewrite_messages(user_text, text, semantic_reason)) or ""
                ).strip()
            except Exception as exc:
                logger.warning(
                    "Life-Boat non-answer rewrite unavailable error=%s message_content=redacted",
                    type(exc).__name__,
                )
                repaired = ""
            if repaired:
                repaired_verdict = review_verdict(
                    user_text,
                    repaired,
                    material=material,
                    debrief_active=debrief_active,
                )
                repaired_semantic = semantic_failure(repaired)
                if repaired_verdict.accepted and not repaired_semantic:
                    return repaired, "rewritten"
        return text, "editor_rejected_fallback"

    if verdict.accepted and not unsafe_reason and not semantic_reason:
        return text, "accepted"

    rewrite_reason = semantic_reason or unsafe_reason or verdict.reason
    logger.info("Life-Boat draft rejected reason=%s %s", rewrite_reason, verdict.receipt)

    revised = text
    for rewrite_round in range(_MAX_REWRITES):
        try:
            revised = str(rewrite(build_rewrite_messages(user_text, revised, rewrite_reason)) or "")
        except Exception as exc:
            logger.warning(
                "Life-Boat rewrite unavailable reason=%s error=%s message_content=redacted",
                rewrite_reason,
                type(exc).__name__,
            )
            return text, "rewrite_unavailable"

        if not revised.strip():
            logger.warning(
                "Life-Boat rewrite returned nothing reason=%s message_content=redacted",
                rewrite_reason,
            )
            return text, "rewrite_unavailable"

        second = review_verdict(
            user_text,
            revised,
            material=material,
            debrief_active=debrief_active,
        )
        from gateway.lifeboat_editor import unsafe_draft_reason
        revised_unsafe_reason = unsafe_draft_reason(revised, user_text)
        revised_semantic_reason = semantic_failure(revised)
        if second.accepted and not revised_unsafe_reason and not revised_semantic_reason:
            logger.info("Life-Boat rewrite accepted %s", second.receipt)
            return revised, "rewritten"

        rewrite_reason = revised_semantic_reason or revised_unsafe_reason or second.reason

        logger.info(
            "Life-Boat rewrite_rejected first=%s second=%s message_content=redacted",
            rewrite_reason,
            second.reason,
        )
        if rewrite_round + 1 < _MAX_REWRITES:
            continue

    # A failed rewrite must not make the bot silent when the original has no
    # semantic or deterministic safety failure. Keep the main model's reply
    # and expose the rejected-repair outcome for telemetry.
    # Never turn a nonempty answer into silence. A failed repair is a quality
    # signal, not permission to drop the user's turn.
    return text, "rewrite_rejected_fallback"
