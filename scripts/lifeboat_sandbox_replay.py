#!/usr/bin/env python3
"""Replay a Life-Boat conversation against the real agent, in an isolated sandbox.

Nothing here touches the live Telegram thread, the life-advisor memory store, or the
Dropbox-synced Obsidian vault:

* ``HERMES_HOME`` is repointed at a throwaway directory holding a copy of the
  life-advisor ``config.yaml`` and skills.  Plugins load from ``$HERMES_HOME/plugins``
  (``hermes_cli/plugins.py``), so the ``obsidian-source-of-truth`` archive hook that
  writes the vault turn log is simply not present.
* ``skip_memory=True`` and no memory database is copied, so nothing is read or written.
* No toolsets are enabled, so the agent cannot write files or send messages.
* No platform adapter is constructed, so no Telegram request can be made.

The default mode is a dry run that builds everything and proves the isolation without
calling a model.  ``--live`` makes real model calls on the configured provider account,
which is why it is opt-in.

Usage::

    ~/.hermes/hermes-agent/venv/bin/python scripts/lifeboat_sandbox_replay.py
    ~/.hermes/hermes-agent/venv/bin/python scripts/lifeboat_sandbox_replay.py --live
    ~/.hermes/hermes-agent/venv/bin/python scripts/lifeboat_sandbox_replay.py --live --turns my_turns.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import time


LIFEBOAT_CHAT_ID = "-1004230590253"
LIFEBOAT_THREAD_ID = "2"
PROFILE_HOME = Path.home() / ".hermes" / "profiles" / "life-advisor"
LIVE_MEMORY_DB = PROFILE_HOME / "memory.db"
VAULT_TURN_LOGS = (
    Path("/media/endlessblink/data/app-data/sync/Dropbox/OBSIDIAN_SYNCED/MAIN VULT")
    / "_System" / "Hermes Turn Logs"
)

# The shape that produced the 2026-08-13 collapse: short, vulnerable messages, a
# correction, and a question about the assistant's own state.  Paraphrased, not quoted.
DEFAULT_TURNS = [
    "היה לי יום ממש קשה היום",
    "אני מרגיש שאני מאכזב את כולם",
    "לא, זה לא מה שאמרתי",
    "אתה כועס עליי?",
    "אני לא יודע",
]


# The failure Noam reports comes from long conversations, and instruction adherence
# decays with conversation depth.  A five-turn scenario with no history looks healthy
# while production is broken, so the probe below is run at increasing depth against
# synthetic filler history.  Everything here is written from scratch to match the
# *shape* of his messages - several threads at once, a self-verdict, comparison to
# others - and never copied from his real conversations.
PROBE_TURNS = [
    "אז זה מסובך. אני רוצה להמשיך לעבד את מה שדיברנו עליו אתמול, וגם את זה שהגשתי "
    "מועמדות למשהו והיה שקט מאז, ועכשיו יש חרדה שזה לא יקרה בכלל, ואיך שאני שוב הופך "
    "את זה לגזר דין על עצמי. וגם הגיל, וגם שכולם סביבי כאילו זזים ואני עומד במקום",
]

_FILLER_USER = [
    "היום היה בסדר, אבל משהו נשאר כבד ברקע",
    "לא הצלחתי להתרכז כל אחר הצהריים",
    "דיברתי עם חבר ואחר כך הרגשתי ריק",
    "יש לי הרבה דברים פתוחים ואני לא יודע במה להתחיל",
    "קמתי מאוחר ואז כל היום הרגיש אבוד",
    "משהו במה שאמרתי קודם לא מדויק",
    "אני עייף אבל לא מצליח לנוח",
    "יש לי תחושה שאני חוזר לאותו מקום שוב",
]

_FILLER_ASSISTANT = [
    "אני שומע. מה הכי נוכח מזה עכשיו?",
    "בוא נישאר רגע עם זה בלי לפרק בכוח.",
    "זה נשמע כמו משהו שיושב מתחת לכל היום.",
    "אני איתך בזה.",
    "מה עוד היה שם, חוץ ממה שאמרת?",
    "לא צריך לסדר את זה עכשיו.",
]


def _filler_history(depth: int) -> list[dict[str, str]]:
    """Synthetic prior conversation, ``depth`` exchanges long."""
    history: list[dict[str, str]] = []
    for index in range(max(0, depth)):
        history.append({"role": "user", "content": _FILLER_USER[index % len(_FILLER_USER)]})
        history.append(
            {"role": "assistant", "content": _FILLER_ASSISTANT[index % len(_FILLER_ASSISTANT)]}
        )
    return history


def _snapshot(path: Path) -> tuple[bool, float, int]:
    try:
        stat = path.stat()
        return True, stat.st_mtime, stat.st_size
    except OSError:
        return False, 0.0, 0


def _memory_fingerprint() -> tuple[int, float]:
    """Row count and newest event time in the live store.

    Deliberately not an mtime check: the running gateway and the profile's cron
    ticker open this database constantly, so mtime changes during any run and says
    nothing about whether the sandbox wrote to it.  Content is what matters.
    """
    import sqlite3

    try:
        connection = sqlite3.connect(f"file:{LIVE_MEMORY_DB}?mode=ro", uri=True)
    except Exception:
        return (-1, -1.0)
    try:
        count = connection.execute("select count(*) from memory_events").fetchone()[0]
        newest = connection.execute(
            "select coalesce(max(created_at), 0) from memory_events"
        ).fetchone()[0]
        return (int(count), float(newest))
    except Exception:
        return (-1, -1.0)
    finally:
        connection.close()


def _dir_snapshot(path: Path) -> dict[str, tuple[bool, float, int]]:
    if not path.is_dir():
        return {}
    return {str(child): _snapshot(child) for child in sorted(path.rglob("*")) if child.is_file()}


def _build_sandbox_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="lifeboat-sandbox-"))
    shutil.copy2(PROFILE_HOME / "config.yaml", home / "config.yaml")
    skills = PROFILE_HOME / "skills"
    if skills.is_dir():
        shutil.copytree(skills, home / "skills")
    (home / "plugins").mkdir(exist_ok=True)  # deliberately empty
    (home / "state").mkdir(exist_ok=True)
    # A *copy* of the memory store, so recall behaves as it does in production while
    # every write lands in the throwaway directory and the real store is untouched.
    for name in ("memory.db", "memory_store.db"):
        source = PROFILE_HOME / name
        if source.is_file():
            shutil.copy2(source, home / name)
    # Provider credentials live in the real root auth store and are looked up via
    # HERMES_HOME.  Link rather than copy, so an OAuth token refresh during the run
    # updates the real store exactly as it would in normal operation instead of
    # silently diverging.  Nothing else from the root is exposed.
    for name in ("auth.json", ".env"):
        source = Path.home() / ".hermes" / name
        if source.exists():
            (home / name).symlink_to(source)
    return home


def _channel_prompt() -> str:
    from gateway.config import Platform, load_gateway_config
    from gateway.platforms.base import resolve_channel_prompt

    config = load_gateway_config()
    for platform, platform_config in (getattr(config, "platforms", {}) or {}).items():
        if getattr(platform, "value", platform) == Platform.TELEGRAM.value:
            extra = dict(getattr(platform_config, "extra", {}) or {})
            prompt = resolve_channel_prompt(extra, LIFEBOAT_THREAD_ID, LIFEBOAT_CHAT_ID)
            return (prompt or "").replace("\\n", "\n")
    return ""


def _ephemeral_prompt(channel_prompt: str, user_text: str, baseline: bool = False, no_layout: bool = False) -> str:
    """Assemble the same layers ``gateway/run.py`` combines for a Life-Boat turn.

    ``baseline`` strips the two closing-anchor rules out of the assembled bundle so
    the same scenario can be run with and without them.  Without that control an
    "every reply ends on a question" result proves nothing - the model might have
    done that anyway.
    """
    from gateway.lifeboat_psychology import _CLOSING_ANCHOR, build_signal_guidance

    guidance = build_signal_guidance(user_text)
    if baseline:
        guidance = guidance.replace(_CLOSING_ANCHOR, "").strip()
        channel_prompt = "\n".join(
            line for line in channel_prompt.splitlines()
            if "THE LAST THING YOU SAY" not in line.upper()
        )
    if no_layout:
        # The layout rules ask for several short bubbles of 2-6 lines each.  That is a
        # compression instruction, and compression of someone's experience is a list.
        # Stripping them isolates whether the diagram-shaped replies come from here.
        channel_prompt = "\n".join(
            line for line in channel_prompt.splitlines()
            if "bubble" not in line.lower() and "<<<SPLIT>>>" not in line
        )
    parts = [part for part in (channel_prompt, guidance) if part]
    return "\n\n".join(parts)


def _check_isolation(loaded_plugins: list[str], before: dict) -> list[str]:
    problems = []
    if loaded_plugins:
        problems.append(f"plugins loaded in sandbox: {loaded_plugins}")
    if _memory_fingerprint() != before["memory_db"]:
        problems.append("the live life-advisor memory store gained or changed rows")
    if _dir_snapshot(VAULT_TURN_LOGS) != before["vault"]:
        problems.append("the Obsidian turn-log folder changed")
    return problems


def _report_endings(transcript: list[dict[str, str]]) -> None:
    """Summarise how each reply ends - the behaviour under test.

    Two failure modes, and they pull in opposite directions: a reply that lands on
    a conclusion closes the conversation, and a reply that mechanically ends on a
    question every single time is a formula rather than a conversation.  Both are
    worth seeing, so the report counts them separately instead of scoring a pass.
    """
    print("\n--- endings ---")
    questions = 0
    for entry in transcript:
        reply = entry["assistant"]
        last = reply.split("<<<SPLIT>>>")[-1].strip().splitlines()
        tail = (last[-1] if last else "").strip()
        is_question = tail.endswith("?")
        questions += is_question
        marker = "?" if is_question else "."
        print(f"  [{marker}] {tail[:88]}")
    total = len(transcript) or 1
    print(f"  {questions}/{total} replies end on a question back to the user")
    if questions == total and total > 2:
        print("  NOTE every single reply ends on a question - check it does not read "
              "as a formula rather than a conversation")


def _loaded_plugin_names() -> list[str]:
    try:
        from hermes_cli.plugins import get_plugin_manager

        manager = get_plugin_manager()
        return [str(name) for name in getattr(manager, "plugins", {}) or {}]
    except Exception:
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="make real model calls (costs provider quota)")
    parser.add_argument("--turns", type=Path, help="JSON list of user messages")
    parser.add_argument("--out", type=Path, help="write the transcript here")
    parser.add_argument("--layers", choices=["none", "skills", "memory", "both"],
                        default="both",
                        help="which production layers to include. The coaching skill only "
                             "loads when the skills toolset is enabled, and with 'none' the "
                             "sandbox is testing a materially different bot")
    parser.add_argument("--probe", action="store_true",
                        help="use the long multi-thread probe message instead of the short "
                             "scenario - this is the shape that actually fails")
    parser.add_argument("--depth", type=int, default=0,
                        help="prepend N synthetic filler exchanges as prior conversation; "
                             "instruction adherence decays with depth, so sweep 0/10/20/40")
    parser.add_argument("--no-layout", action="store_true",
                        help="strip the Telegram bubble/line-length layout rules")
    parser.add_argument("--baseline", action="store_true",
                        help="strip the closing-anchor rules, to measure what the model "
                             "does without them")
    parser.add_argument("--repeat", type=int, default=1,
                        help="run the scenario N times; replies are nondeterministic, "
                             "so one sample cannot show whether a change is reliable")
    args = parser.parse_args(argv)

    turns = PROBE_TURNS if args.probe else DEFAULT_TURNS
    if args.turns:
        loaded = json.loads(args.turns.read_text(encoding="utf-8"))
        if not isinstance(loaded, list) or not all(isinstance(t, str) for t in loaded):
            raise SystemExit("--turns must be a JSON list of strings")
        turns = loaded

    before = {"memory_db": _memory_fingerprint(), "vault": _dir_snapshot(VAULT_TURN_LOGS)}

    sandbox = _build_sandbox_home()
    os.environ["HERMES_HOME"] = str(sandbox)
    print(f"sandbox home: {sandbox}")

    try:
        channel_prompt = _channel_prompt()
        if not channel_prompt:
            raise SystemExit("no Life-Boat channel prompt resolved - check the profile config")
        print(f"channel prompt: {len(channel_prompt)} chars")

        plugins = _loaded_plugin_names()
        print(f"plugins loaded under sandbox home: {plugins or 'none'}")

        if not args.live:
            for index, user_text in enumerate(turns, start=1):
                ephemeral = _ephemeral_prompt(channel_prompt, user_text, args.baseline, args.no_layout)
                print(f"[dry] turn {index}: {len(ephemeral)} chars of instruction, "
                      f"user message {len(user_text)} chars")
            problems = _check_isolation(_loaded_plugin_names(), before)
            print("\n--- isolation ---")
            for problem in problems:
                print(f"  FAIL {problem}")
            if problems:
                return 1
            print("  live memory store unchanged, vault turn logs unchanged, no plugins loaded")
            print("\ndry run only - no model was called. Re-run with --live to generate replies.")
            return 0

        # The gateway loads the root .env at startup; a bare script does not, and
        # provider credentials are resolved from it.  Load it only on the live path.
        try:
            from dotenv import load_dotenv

            load_dotenv(Path.home() / ".hermes" / ".env", override=False)
        except ImportError:
            pass

        from run_agent import AIAgent
        from gateway.run import _resolve_runtime_agent_kwargs, _resolve_gateway_model

        # Provider credentials are resolved through HERMES_HOME, and the sandbox home
        # deliberately holds none of the real store.  Resolve them once against the
        # real profile home, then hand the resulting api_key/base_url to the agent so
        # the rest of the run stays inside the sandbox.
        os.environ["HERMES_HOME"] = str(PROFILE_HOME)
        try:
            runtime_kwargs = _resolve_runtime_agent_kwargs()
            model = runtime_kwargs.pop("model", None) or _resolve_gateway_model()
        finally:
            os.environ["HERMES_HOME"] = str(sandbox)
        print(f"model: {model}")

        transcript: list[dict[str, str]] = []
        history: list[dict[str, str]] = []

        for sample in range(1, max(1, args.repeat) + 1):
            if args.repeat > 1:
                print(f"\n========== sample {sample} of {args.repeat} ==========")
            history = _filler_history(args.depth)
            for index, user_text in enumerate(turns, start=1):
                ephemeral = _ephemeral_prompt(channel_prompt, user_text, args.baseline, args.no_layout)
                agent = AIAgent(
                    model=model,
                    ephemeral_system_prompt=ephemeral,
                    # The coaching skill is a large part of what shapes a reply, and it
                    # only loads if the agent can read skills.  With no toolsets at all
                    # the sandbox was testing a materially different bot.  Nothing here
                    # can write outside the sandbox: files resolve under HERMES_HOME and
                    # no platform adapter exists.
                    enabled_toolsets=["skills"] if args.layers in {"skills", "both"} else [],
                    skip_memory=args.layers not in {"memory", "both"},
                    quiet_mode=True,
                    platform="telegram",
                    chat_id=LIFEBOAT_CHAT_ID,
                    thread_id=LIFEBOAT_THREAD_ID,
                    session_id=f"lifeboat-sandbox-{int(time.time())}",
                    **runtime_kwargs,
                )
                result = agent.run_conversation(user_text, conversation_history=list(history)) or {}
                reply = str(
                    result.get("final_response") or result.get("response") or ""
                ).strip()
                if not reply:
                    reply = f"[no text returned; result keys: {sorted(result)}]"
                transcript.append({"sample": str(sample), "user": user_text, "assistant": reply})
                history.append({"role": "user", "content": user_text})
                history.append({"role": "assistant", "content": reply})
                print(f"\n--- sample {sample} turn {index} ---")
                print(f"user:      {user_text}")
                print(f"assistant: {reply}")

        # Write before asserting isolation: a failed assertion must not cost the
        # transcript, which is the whole point of the run.
        if args.out:
            args.out.write_text(
                json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\ntranscript written to {args.out}")

        _report_endings(transcript)

        problems = _check_isolation(_loaded_plugin_names(), before)
        print("\n--- isolation ---")
        if problems:
            for problem in problems:
                print(f"  FAIL {problem}")
            return 1
        print("  live memory store unchanged, vault turn logs unchanged, no plugins loaded")
        return 0
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
