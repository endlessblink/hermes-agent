#!/usr/bin/env python3
"""Print and lint the instruction bundle a Life-Boat turn would receive.

No agent is created and no model is called, so this is free and cannot touch the
user's Telegram, memory store or Obsidian vault.  It exists because the 2026-08-13
regression was a *contradiction between two instruction layers* - the Telegram topic
prompt prescribed a one-line verdict while the conversation contract forbade closing -
and that is visible here without anyone having to live through a bad reply.

Usage::

    ~/.hermes/hermes-agent/venv/bin/python scripts/lifeboat_prompt_probe.py
    ~/.hermes/hermes-agent/venv/bin/python scripts/lifeboat_prompt_probe.py --quiet
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
from types import SimpleNamespace


LIFEBOAT_CHAT_ID = "-1004230590253"
LIFEBOAT_THREAD_ID = "2"
LIFEBOAT_DM_ID = "602196268"
PROFILE_HOME = Path.home() / ".hermes" / "profiles" / "life-advisor"

# Sample user messages, chosen to exercise each signal branch.  These are
# paraphrases, not quotes from a real conversation.
SAMPLES = (
    ("neutral", "יש לי משהו שמסתובב לי בראש מהבוקר"),
    ("short-vulnerable", "אני מרגיש די גרוע"),
    ("self-criticism", "אני פשוט כישלון בזה"),
    ("thought-loop", "אני לא מפסיק לחשוב על זה, זה בלולאה"),
)

# Directives that dictate the *shape* of a reply.  These are what collide with a
# stance contract that asks the assistant to stay open.
_SHAPE_PATTERNS = (
    (r"verdict", "prescribes a verdict"),
    (r"one[- ]line", "prescribes a one-line opener"),
    (r"\btrap\b", "prescribes naming a trap"),
    (r"\b\d+-part\b", "prescribes an N-part structure"),
    (r"first .{0,30}\bthen\b", "prescribes a fixed first/then order"),
    (r"revised draft", "prescribes a revision block"),
    (r"third bubble", "prescribes a third bubble"),
    (r"checklist", "prescribes a checklist"),
    (r"numbered", "mentions numbered output"),
)

# Directives that ask the assistant to stay open / not close.
_OPEN_PATTERNS = (
    r"leave the thread alive",
    r"do not close",
    r"not close on",
    r"do not over-summarize",
    r"stay with",
    r"invites? (?:him|them|the user) to keep going",
    r"wait for",
)

_SCOPING_PATTERNS = (
    r"only (?:when|where) .{0,60}(?:asked|asks|explicitly)",
    r"unless (?:they|he|the user) (?:explicitly )?ask",
    r"belongs only",
)


def _load_config():
    os.environ["HERMES_HOME"] = str(PROFILE_HOME)
    from gateway.config import load_gateway_config

    return load_gateway_config()


def _telegram_extra(config) -> dict:
    from gateway.config import Platform

    for platform, platform_config in (getattr(config, "platforms", {}) or {}).items():
        name = getattr(platform, "value", platform)
        if name == Platform.TELEGRAM.value:
            return dict(getattr(platform_config, "extra", {}) or {})
    return {}


def _resolve_prompt(extra: dict, channel_id: str, parent_id: str | None):
    """Resolve exactly the way the adapter does, and report which key matched."""
    from gateway.platforms.base import resolve_channel_prompt

    prompt = resolve_channel_prompt(extra, channel_id, parent_id)
    prompts = extra.get("channel_prompts") or {}
    matched = None
    for key in (channel_id, parent_id):
        if key and str(prompts.get(key) or "").strip():
            matched = key
            break
    return prompt, matched


def _lint(bundle: str) -> tuple[list[str], dict[str, int]]:
    text = bundle.lower()
    findings: list[str] = []

    shape_hits = [label for pattern, label in _SHAPE_PATTERNS if re.search(pattern, text)]
    open_hits = [p for p in _OPEN_PATTERNS if re.search(p, text)]
    scoped = [p for p in _SCOPING_PATTERNS if re.search(p, text)]

    if shape_hits and open_hits:
        if scoped:
            findings.append(
                "OK  shape rules present but scoped to an explicit request "
                f"({', '.join(shape_hits)})"
            )
        else:
            findings.append(
                "CONTRADICTION  the bundle both prescribes a reply shape "
                f"({', '.join(shape_hits)}) and asks the assistant to stay open "
                f"({len(open_hits)} open-stance rules), with no scoping clause. "
                "This is the 2026-08-13 regression."
            )
    elif shape_hits and not open_hits:
        findings.append(
            f"WARN  reply-shape rules with no open-stance counterweight: {', '.join(shape_hits)}"
        )
    elif not open_hits:
        findings.append("WARN  no open-stance rule found at all")
    else:
        findings.append("OK  no reply-shape prescription found")

    # Prohibition vs commission balance.  Research the user supplied: prohibitions
    # decay with conversation depth, commissions hold.  A bundle that is almost
    # all "do not" is fragile even when every individual rule is correct.
    prohibitions = len(re.findall(r"\b(?:do not|don't|never|avoid)\b", text))
    commissions = len(re.findall(r"\b(?:always|end with|stay|reflect|keep|offer|answer)\b", text))
    counts = {"prohibitions": prohibitions, "commissions": commissions, "chars": len(bundle)}
    if prohibitions > commissions * 2:
        findings.append(
            f"WARN  prohibition-heavy ({prohibitions} prohibitions vs {commissions} "
            "positive directives) - the prohibition half is what decays over a long "
            "conversation; prefer positive anchors and examples."
        )
    return findings, counts


def _asymmetry(topic_prompt: str, dm_prompt: str) -> list[str]:
    """Rules present in one Telegram surface but not the other.

    The 08-13 bug was exactly this: every anti-closure rule lived in the DM prompt
    and none of them in the topic prompt, where the support conversation actually
    happens.
    """
    keys = {
        "over-summarize": r"over-summarize",
        "emotions not voluntary": r"emotions are not directly voluntary",
        "one clarifying question": r"one (?:useful )?(?:tentative )?(?:opening |clarifying )?question",
        "wait for the user": r"wait for",
        "leave the thread alive": r"leave the thread alive",
        "no closing verdict": r"closing verdict|do not close",
    }
    topic = topic_prompt.lower()
    dm = dm_prompt.lower()
    out = []
    for label, pattern in keys.items():
        in_topic = bool(re.search(pattern, topic))
        in_dm = bool(re.search(pattern, dm))
        if in_topic != in_dm:
            where = "topic only" if in_topic else "DM only"
            out.append(f"{label}: {where}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="print the lint report only")
    args = parser.parse_args(argv)

    failures: list[str] = []

    # 1. Identity.  A profile-name-only check here silently disables every
    #    Life-Boat behaviour in production - see docs/runtime/live-tree-and-deploy.md.
    from gateway.lifeboat_followups import is_lifeboat_source

    real = SimpleNamespace(
        platform=SimpleNamespace(value="telegram"),
        profile="default",
        chat_id=LIFEBOAT_CHAT_ID,
        thread_id=LIFEBOAT_THREAD_ID,
    )
    other_thread = SimpleNamespace(
        platform=SimpleNamespace(value="telegram"),
        profile="default",
        chat_id=LIFEBOAT_CHAT_ID,
        thread_id="7",
    )
    other_chat = SimpleNamespace(
        platform=SimpleNamespace(value="telegram"),
        profile="default",
        chat_id="-1009999999999",
        thread_id=LIFEBOAT_THREAD_ID,
    )
    if not is_lifeboat_source(real):
        failures.append("is_lifeboat_source is False for the real Life-Boat chat/thread")
    if is_lifeboat_source(other_thread) or is_lifeboat_source(other_chat):
        failures.append("is_lifeboat_source matches an unrelated chat or thread")
    print(f"identity: real={is_lifeboat_source(real)} "
          f"other_thread={is_lifeboat_source(other_thread)} "
          f"other_chat={is_lifeboat_source(other_chat)}")

    # 2. The Telegram channel prompt actually in force for the support topic.
    config = _load_config()
    extra = _telegram_extra(config)
    topic_prompt, topic_key = _resolve_prompt(extra, LIFEBOAT_THREAD_ID, LIFEBOAT_CHAT_ID)
    dm_prompt, _ = _resolve_prompt(extra, LIFEBOAT_DM_ID, None)
    topic_prompt = topic_prompt or ""
    dm_prompt = dm_prompt or ""
    if not topic_prompt:
        failures.append("no channel prompt resolved for the Life-Boat topic")
    print(f"topic prompt: {len(topic_prompt)} chars (matched key {topic_key!r})")
    print(f"dm prompt:    {len(dm_prompt)} chars")

    # 3. The per-turn stance guidance.
    from gateway.lifeboat_psychology import build_signal_guidance

    guidance = {label: build_signal_guidance(text) for label, text in SAMPLES}

    if not args.quiet:
        print("\n--- telegram topic prompt ---")
        print(topic_prompt.replace("\\n", "\n"))
        for label, text in guidance.items():
            print(f"\n--- signal guidance [{label}] ---")
            print(text)

    # 4. Lint the bundle the model would see for the hardest case: a short,
    #    vulnerable message, which is the shape that triggered the collapse.
    bundle = "\n\n".join([topic_prompt.replace("\\n", "\n"), guidance["short-vulnerable"]])
    findings, counts = _lint(bundle)
    print("\n--- lint ---")
    print(f"bundle: {counts['chars']} chars, "
          f"{counts['prohibitions']} prohibitions, {counts['commissions']} positive directives")
    for finding in findings:
        print(f"  {finding}")
        if finding.startswith("CONTRADICTION"):
            failures.append(finding)

    asym = _asymmetry(topic_prompt, dm_prompt)
    print("\n--- topic vs DM asymmetry ---")
    if asym:
        for line in asym:
            print(f"  {line}")
    else:
        print("  none")

    print("\n--- result ---")
    if failures:
        for failure in failures:
            print(f"  FAIL {failure}")
        return 1
    print("  pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
