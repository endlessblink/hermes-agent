"""Does this runtime actually enforce the Life-Boat gates?

Every gate on the delivery path is wrapped in a try/except so that a fault in
Life-Boat can never take down message delivery for other profiles. That is the
right trade, but it has a failure mode: when the modules disagree with each
other, the exception is swallowed on every message and the gates simply stop
applying. That happened in production on 2026-08-22, where the installed
classifier was missing two functions the gate imported. The bot kept talking,
ungated, and nothing said a word.

This check runs once at startup and asks the question directly: can the gate
load, and does it still do its job on a known input? A runtime that cannot must
announce it, because silence here looks exactly like health.
"""

from __future__ import annotations

import importlib
import tempfile


#: Module to the names the delivery path imports from it. Anything the gate
#: reaches for at import time belongs here, because a name missing at import
#: time is the failure this check exists to catch.
REQUIRED_SYMBOLS: dict[str, tuple[str, ...]] = {
    "gateway.lifeboat_psychology": (
        "classify_lifeboat_signals",
        "record_lifeboat_response_fingerprint",
        "select_lifeboat_turn_policy",
    ),
    "gateway.lifeboat_surface": (
        "finalize_outbound",
        "should_suppress_notice",
        "is_banned_generic_response",
    ),
    "gateway.lifeboat_modes": (
        "advance_mode",
        "load_mode_state",
        "save_mode_state",
    ),
    "gateway.lifeboat_reentry": (
        "is_contextless_reentry",
    ),
    "gateway.lifeboat_contracts": (
        "contract_for",
        "contract_violations",
    ),
    "gateway.lifeboat_followups": (
        "is_lifeboat_source",
    ),
}

#: A notice that must always be recognised as engine plumbing, and a sentence
#: that must always be refused. If either stops holding, the gate is not doing
#: its job however cleanly it imported.
_CANARY_NOTICE = "⚡ Interrupting current task. I'll respond to your message shortly."
_CANARY_BANNED = "מה הכי חי אצלך עכשיו, אם בכלל?"


def lifeboat_runtime_problems() -> tuple[str, ...]:
    """Return every reason this runtime cannot enforce the Life-Boat gates.

    Returns an empty tuple when the path is healthy. Never raises: a broken
    runtime has to produce a report a caller can log, not an exception during
    gateway start.
    """
    problems: list[str] = []
    modules = {}

    for module_name, names in REQUIRED_SYMBOLS.items():
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            problems.append(f"{module_name} failed to import: {type(exc).__name__}: {exc}")
            continue
        modules[module_name] = module
        for name in names:
            if not hasattr(module, name):
                problems.append(f"{module_name} is missing {name}")

    surface = modules.get("gateway.lifeboat_surface")
    if surface is None:
        return tuple(problems)

    # Importing cleanly is not proof of enforcement. Run the gate once.
    try:
        if not surface.should_suppress_notice(_CANARY_NOTICE, mode="support"):
            problems.append("the gate did not suppress a known engine notice")
    except Exception as exc:
        problems.append(f"suppression check raised: {type(exc).__name__}: {exc}")

    try:
        with tempfile.TemporaryDirectory() as scratch:
            if surface.finalize_outbound(scratch, "runtime-check", _CANARY_BANNED) is not None:
                problems.append("the gate did not refuse a known canned sentence")
    except Exception as exc:
        problems.append(f"delivery check raised: {type(exc).__name__}: {exc}")

    return tuple(problems)
