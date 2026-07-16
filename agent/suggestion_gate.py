"""Suggestion discipline: rejection rules, mood mode, and the daily cap.

Implements the state layer of docs/superpowers/specs/2026-07-15-suggestion-
quality-design.md. Everything is per-profile JSON under the profile's state
directory; no LLM calls, no network. The gate itself is a prompt block
(:func:`build_discipline_block`) injected into every proactive surface — the
model runs the actual check in-turn, this module just supplies the facts.

Rule strength ladder (reason-based, per the approved spec):
- rejection with a generalizable reason      -> permanent immediately
- mood-flavored rejection ("not tonight")    -> mood mute for today only
- no reason                                  -> provisional; permanent on the
  2nd rejection of the same class on a different day
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RULES_FILENAME = "suggestion_rules.json"
MOOD_FILENAME = "suggestion_mood.json"
COUNTER_FILENAME = "suggestion_counter.json"

DAILY_SUGGESTION_CAP = 2
MAX_RULES_IN_BLOCK = 10
MAX_RULE_TEXT = 160


def _today(now: Optional[float] = None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time()).strftime("%Y-%m-%d")


def _read_json(path: Path, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except Exception:
        logger.warning("suggestion_gate: unreadable state file %s — using default", path)
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _clip(text: Any, limit: int = MAX_RULE_TEXT) -> str:
    s = str(text or "").strip()
    return s[:limit]


# ── Rejection rules ─────────────────────────────────────────────────────────

def load_rules(state_dir: Path) -> List[Dict[str, Any]]:
    rules = _read_json(Path(state_dir) / RULES_FILENAME, [])
    return rules if isinstance(rules, list) else []


def save_rejection(
    state_dir: Path,
    rule_class: str,
    reason: str = "",
    mood_flavored: bool = False,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Record a brushed-off suggestion. Returns {action, strength, ack}.

    ``rule_class`` is a short slug for the *idea*, not the wording
    (e.g. "evening-appliance-check"), so the whole class is suppressed.
    """
    state_dir = Path(state_dir)
    rule_class = _clip(rule_class, 80) or "unspecified"
    reason = _clip(reason)
    today = _today(now)

    if mood_flavored:
        set_mood(state_dir, note=reason or rule_class, now=now)
        return {
            "action": "mood_mute",
            "strength": "today",
            "ack": "got it — backing off for today.",
        }

    rules = load_rules(state_dir)
    existing = next((r for r in rules if r.get("class") == rule_class), None)

    if existing is not None:
        existing["hits"] = int(existing.get("hits", 1)) + 1
        existing["last_hit"] = today
        if existing.get("strength") == "provisional" and existing.get("created_day") != today:
            existing["strength"] = "permanent"
        if reason and not existing.get("reason"):
            existing["reason"] = reason
            existing["strength"] = "permanent"
        _write_json(state_dir / RULES_FILENAME, rules)
        strength = existing["strength"]
    else:
        strength = "permanent" if reason else "provisional"
        rules.append({
            "class": rule_class,
            "reason": reason,
            "strength": strength,
            "created_day": today,
            "last_hit": today,
            "hits": 1,
        })
        _write_json(state_dir / RULES_FILENAME, rules)

    ack = f"got it — no more {rule_class.replace('-', ' ')} suggestions."
    if strength == "provisional":
        ack = f"noted — I'll hold off on {rule_class.replace('-', ' ')} suggestions."
    return {"action": "rule_saved", "strength": strength, "ack": ack}


def remove_rule(state_dir: Path, rule_class: str) -> bool:
    state_dir = Path(state_dir)
    rules = load_rules(state_dir)
    kept = [r for r in rules if r.get("class") != rule_class]
    if len(kept) == len(rules):
        return False
    _write_json(state_dir / RULES_FILENAME, kept)
    return True


# ── Mood mode ───────────────────────────────────────────────────────────────

def set_mood(state_dir: Path, state: str = "low", note: str = "", now: Optional[float] = None) -> None:
    _write_json(Path(state_dir) / MOOD_FILENAME, {
        "state": _clip(state, 24) or "low",
        "note": _clip(note),
        "set_day": _today(now),
    })


def get_mood(state_dir: Path, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Active mood for today, or None (auto-expires at local midnight)."""
    mood = _read_json(Path(state_dir) / MOOD_FILENAME, None)
    if not isinstance(mood, dict) or mood.get("set_day") != _today(now):
        return None
    return mood


# ── Daily cap ───────────────────────────────────────────────────────────────

def suggestions_today(state_dir: Path, now: Optional[float] = None) -> int:
    counter = _read_json(Path(state_dir) / COUNTER_FILENAME, {})
    if not isinstance(counter, dict) or counter.get("day") != _today(now):
        return 0
    return int(counter.get("count", 0))


def record_suggestion(state_dir: Path, now: Optional[float] = None) -> int:
    state_dir = Path(state_dir)
    today = _today(now)
    count = suggestions_today(state_dir, now) + 1
    _write_json(state_dir / COUNTER_FILENAME, {"day": today, "count": count})
    return count


# ── The discipline block ────────────────────────────────────────────────────

def build_discipline_block(
    state_dir: Path,
    now: Optional[float] = None,
    cap: int = DAILY_SUGGESTION_CAP,
    precise_time: bool = True,
) -> str:
    """Compact per-turn prompt block (aim: under ~1.2KB) for every proactive
    surface. Supplies the facts; the model applies the checks in-turn.

    ``precise_time=False`` emits a date-only stamp — required when the block
    lands in the session-cached system prompt, which must stay byte-stable
    for the day (see agent/system_prompt.py timestamp rationale). Per-turn
    surfaces (personal-assistant trigger prompts, cron jobs) use the default
    minute-precision stamp.
    """
    ts = now if now is not None else time.time()
    fmt = "%A %Y-%m-%d %H:%M" if precise_time else "%A %Y-%m-%d"
    stamp = datetime.fromtimestamp(ts).strftime(fmt)
    mood = get_mood(Path(state_dir), now)
    used = suggestions_today(Path(state_dir), now)
    rules = [r for r in load_rules(Path(state_dir)) if r.get("strength") in ("permanent", "provisional")]
    rules = sorted(rules, key=lambda r: (r.get("strength") != "permanent", -int(r.get("hits", 1))))

    lines = [
        "# Suggestion discipline",
        f"Local time: {stamp}. Suggestions voiced today: {used}/{cap}."
        + ("" if precise_time else " Run `date` before any time-sensitive suggestion."),
    ]
    if mood is not None:
        note = f" ({mood['note']})" if mood.get("note") else ""
        lines.append(
            f"MOOD: the user is not feeling well today{note}. Make NO unsolicited "
            "suggestions for the rest of the day. If the user asks for help, switch "
            "to small-wins mode: offer the smallest doable next step or a 2-3 item "
            "batch, framed so the day still counts."
        )
    if rules:
        lines.append("Rejected suggestion classes (never re-suggest; the reason generalizes):")
        for r in rules[:MAX_RULES_IN_BLOCK]:
            reason = f" — {r['reason']}" if r.get("reason") else ""
            lines.append(f"- {r.get('class')}{reason}")
        if len(rules) > MAX_RULES_IN_BLOCK:
            lines.append(f"- (+{len(rules) - MAX_RULES_IN_BLOCK} more; when unsure, stay silent)")
    lines.append(
        "Before voicing ANY unsolicited suggestion, silently check: does it fit the "
        "time of day and the user's routine? does it violate a rejected class or "
        "today's mood? is the daily cap reached? is it grounded in the user's real "
        "tasks? If any check fails or you are unsure, say nothing — silence is "
        "correct. Suggest only at natural transition moments, one concrete small "
        "action at a time. Direct answers to what the user asked are always exempt. "
        "When the user brushes off a suggestion, call suggestion_rule_save and "
        "append its one-line acknowledgment."
    )
    return "\n".join(lines)


def active_profile_state_dir() -> Optional[Path]:
    """State dir for the active profile's suggestion files, or None."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home()) / "state" / "personal-assistant"
    except Exception:
        return None


def discipline_block_for_active_profile(precise_time: bool = True) -> str:
    """Best-effort block for injection call sites. Never raises; returns ""
    when the profile home cannot be resolved — the gate fails open (no block)
    rather than breaking a prompt build.
    """
    try:
        state_dir = active_profile_state_dir()
        if state_dir is None:
            return ""
        return build_discipline_block(state_dir, precise_time=precise_time)
    except Exception:
        logger.warning("suggestion_gate: discipline block build failed", exc_info=True)
        return ""
