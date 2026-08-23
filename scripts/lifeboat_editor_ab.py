#!/usr/bin/env python3
"""Run one Life-Boat conversation through three arms and print what he would read.

The existing sandbox replay calls the agent and stops there, so it can only ever
show the model's draft -- never the reply that is actually delivered. The gate,
the reviewer, and the new editing agent all sit after that point, which means
the harness could not see the change under test at all.

This runs the real delivery path on every turn, and varies one thing:

    main-only    the draft the model produces, gate applied, editor off
    both-medium  editing agent on, thinking at medium
    editor-low   editing agent on, thinking at low

Isolation is inherited wholesale from ``lifeboat_sandbox_replay`` (throwaway
HERMES_HOME, copied memory, no plugins, no adapter), and asserted the same way
after the run. Real model calls are made: this costs provider quota, and Noam
asked for it explicitly.

    ~/.hermes/hermes-agent/venv/bin/python scripts/lifeboat_editor_ab.py --out /tmp/ab.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

INSTALLED = Path.home() / ".hermes" / "hermes-agent"
REPLAY = INSTALLED / "scripts" / "lifeboat_sandbox_replay.py"

if str(INSTALLED) not in sys.path:
    sys.path.insert(0, str(INSTALLED))


def _load_replay():
    spec = importlib.util.spec_from_file_location("lifeboat_sandbox_replay", REPLAY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: His sentence, then a conversation around it. The follow-ups are fixed and
#: identical across arms -- reacting to each arm's reply would make the arms
#: incomparable, which is the whole reason for running them side by side.
TURNS = [
    "אני רוצה לעשות דיבריף על הימים האחרונים.",
    "בעיקר העבודה על הבוט, וגם שכמעט לא יצאתי מהבית",
    "לא, זה לא בדיוק מה שאמרתי",
    "אני לא יודע, אולי",
]


def _ephemeral_prompt(
    channel_prompt: str,
    sandbox: Path,
    session_key: str,
    user_text: str,
    identity: str = "",
) -> str:
    """Assemble what the gateway actually puts in front of the model this turn.

    The replay script built only the channel prompt plus the signal guidance,
    and it referenced a constant that no longer exists -- so it had been unable
    to run at all. More importantly it never included
    ``prepare_lifeboat_inbound_guidance``, which is where the material about him
    and the debrief shape are injected. Testing a debrief request without it
    would be testing a different bot.
    """
    from gateway.lifeboat_psychology import build_signal_guidance
    from gateway.lifeboat_followups import prepare_lifeboat_inbound_guidance

    if identity == "stripped":
        # Routing only, plus who is speaking. No shape rules, no length cap, no
        # per-turn guidance at all.
        from gateway.lifeboat_voice import load_voice_text

        voice = load_voice_text() or IDENTITIES["close-friend"]
        return "\n\n".join(part for part in (channel_prompt, voice) if part and part.strip())

    parts = [channel_prompt, build_signal_guidance(user_text)]
    if identity:
        # Stated twice on purpose: once at the front, and again last, closest to
        # his message. A single line at the top of a long bundle is what decays,
        # and it is also outvoted by the coaching document further down.
        parts.insert(0, IDENTITIES[identity])
        parts.append(IDENTITIES[identity])
    try:
        parts.append(prepare_lifeboat_inbound_guidance(sandbox, session_key, user_text))
    except Exception as exc:  # pragma: no cover - reported, never silently dropped
        print(f"  WARNING inbound guidance unavailable: {type(exc).__name__}: {exc}")
    return "\n\n".join(part for part in parts if part and part.strip())


#: Who is speaking. Not a rule about sentences and not an example to copy --
#: both of those have failed here, one by making the bot emptier and one by
#: turning into a template. Nobody had ever told this bot what it is, so it
#: defaulted to the only thing its documents describe: a coach doing
#: "therapy-adjacent" work, instructed to be "analytical and strategic".
#: Who is speaking. Not a rule about sentences and not an example to copy --
#: bans made the bot emptier, and supplied sentences became templates. Nobody
#: had ever told this bot what it is: the topic prompt is pure routing, and the
#: only document describing its manner opens by calling itself "Personal
#: Coaching", names its work "therapy-adjacent", and tells it to be "analytical
#: and strategic". It sounds like a shrink because that is what it was handed.
#:
#: Two candidates, so the choice can be read rather than argued.
IDENTITIES = {
    # The same role it has now, with the clinical register taken out and
    # nothing else changed. Isolates how much of the distance is the *word*
    # coach, and how much is the vocabulary around it.
    "coach-cleaned": (
        "Who you are: someone who helps him think about his life. Not a "
        "therapist, not a clinician, and you do not do therapy-adjacent work. "
        "Drop every professional register: no analysing, no processing, no "
        "naming what he is going through, no strategy. Talk about his life in "
        "the words he uses for it. You are not conducting anything -- you are "
        "thinking about it with him, out loud, plainly."
    ),
    # A different role entirely.
    "close-friend": (
        "Who you are, before anything else: you are not a coach, a therapist, "
        "or a support assistant, and you must not sound like one. You are "
        "someone close to him who has known him for years and is texting him "
        "late at night.\n"
        "\n"
        "You talk the way a close friend texts. Short. Ordinary words -- his "
        "words, not more elevated ones. No professional vocabulary, no naming "
        "of his processes, no describing his experience back to him in "
        "language he would never use himself.\n"
        "\n"
        "You react to what he tells you before you ask anything. You are "
        "allowed to be surprised, to have an opinion, to disagree with him. "
        "Ask him things -- that is what someone close does -- but ask out of "
        "interest in him, the way a friend asks, not the way an assessment "
        "asks."
    ),
}


def _force_reasoning(sandbox: Path, effort: str) -> None:
    """Set the sandbox agent's reasoning effort, whatever the copied profile said.

    The life-advisor profile pins its own ``reasoning_effort``, so a sandbox
    built from it silently ignores the root setting. Every arm here is supposed
    to hold the main model constant, so it is set explicitly rather than
    inherited.
    """
    import yaml

    path = sandbox / "config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("agent", {})["reasoning_effort"] = effort
    # The copied life-advisor profile's session-start hook writes the shared
    # Obsidian turn log.  This replay must exercise reply assembly in a throwaway
    # home without mutating the user's real transcript store.
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        hooks.pop("on_session_start", None)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _make_editor(effort: str):
    """The editing agent, at a named thinking level."""

    def edit(messages):
        from agent.auxiliary_client import call_llm

        completion = call_llm(
            task="lifeboat_editor",
            messages=messages,
            max_tokens=800,
            temperature=0.6,
            timeout=90,
            extra_body={"reasoning": {"effort": effort}},
        )
        return completion.choices[0].message.content or ""

    return edit


def _rewrite(messages):
    from agent.auxiliary_client import call_llm

    completion = call_llm(
        task="lifeboat_editor", messages=messages, max_tokens=800,
        temperature=0.4, timeout=60,
    )
    return completion.choices[0].message.content or ""


def _deliver(sandbox: Path, session_key: str, user_text: str, draft: str, editor):
    """Everything between the model's draft and his screen."""
    from gateway.lifeboat_surface import finalize_outbound
    from gateway.lifeboat_rewrite import resolve_reply
    from gateway.lifeboat_editor import bump_delivery_count
    from gateway.lifeboat_turn_context import build_turn_context

    gated = finalize_outbound(sandbox, session_key, draft, mode="support", user_text=user_text)
    if gated is None:
        return "[suppressed by the gate]", "suppressed"

    try:
        material = build_turn_context()
    except Exception:
        material = ""

    return resolve_reply(
        user_text,
        gated,
        rewrite=_rewrite,
        edit=editor,
        material=material,
        profile_home=sandbox,
        session_key=session_key,
        deliveries=bump_delivery_count(sandbox, session_key),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write the transcripts here")
    parser.add_argument("--depth", type=int, default=0, help="synthetic filler exchanges first")
    parser.add_argument("--main-effort", default="medium")
    parser.add_argument("--arms", default="", help="comma-separated subset of arm names")
    parser.add_argument("--turns", type=Path, help="JSON list of user messages to use instead")
    args = parser.parse_args(argv)

    replay = _load_replay()

    turns = TURNS
    if args.turns:
        loaded = json.loads(args.turns.read_text(encoding="utf-8"))
        if not isinstance(loaded, list) or not all(isinstance(t, str) for t in loaded):
            raise SystemExit("--turns must be a JSON list of strings")
        turns = loaded

    # The overnight question, in Noam's own words: "you are a better bot than it
    # is -- you talked to me all night and I didn't feel like you degraded."
    #
    # It is the same class of model. The difference is the machinery wrapped
    # around it: a page of rules about how to build a sentence, a two-sentence
    # budget, and a reviewer and editor that rewrite the words afterwards. None
    # of that sits between him and this conversation.
    #
    # So "stripped" removes the wrapper and keeps only what an ordinary
    # assistant has -- who it is, and the conversation. Nothing rewrites its
    # words. "stripped-thinking" is the same with room to think, to separate the
    # wrapper's effect from the reasoning budget's.
    arms = (
        ("no-change", None),
        ("stripped", None),
        ("stripped-thinking", None),
    )

    if args.arms:
        wanted = {name.strip() for name in args.arms.split(",") if name.strip()}
        arms = tuple(arm for arm in arms if arm[0] in wanted)
        if not arms:
            raise SystemExit(f"no arm matched {sorted(wanted)}")

    before = {
        "memory_db": replay._memory_fingerprint(),
        "vault": replay._dir_snapshot(replay.VAULT_TURN_LOGS),
    }

    sandbox = replay._build_sandbox_home()
    _force_reasoning(sandbox, args.main_effort)
    os.environ["HERMES_HOME"] = str(sandbox)
    print(f"sandbox home: {sandbox}")

    results: dict[str, list[dict[str, str]]] = {}
    try:
        channel_prompt = replay._channel_prompt()
        if not channel_prompt:
            raise SystemExit("no Life-Boat channel prompt resolved")
        print(f"channel prompt: {len(channel_prompt)} chars")
        print(f"plugins loaded under sandbox home: {replay._loaded_plugin_names() or 'none'}")

        try:
            from dotenv import load_dotenv

            load_dotenv(Path.home() / ".hermes" / ".env", override=False)
        except ImportError:
            pass

        from run_agent import AIAgent
        from gateway.run import _resolve_runtime_agent_kwargs, _resolve_gateway_model

        os.environ["HERMES_HOME"] = str(replay.PROFILE_HOME)
        try:
            runtime_kwargs = _resolve_runtime_agent_kwargs()
            model = runtime_kwargs.pop("model", None) or _resolve_gateway_model()
        finally:
            os.environ["HERMES_HOME"] = str(sandbox)
        print(f"model: {model} | main reasoning effort: {args.main_effort}\n")

        for arm_name, editor in arms:
            print(f"\n{'=' * 70}\n== ARM: {arm_name}\n{'=' * 70}")
            session_key = f"ab-{arm_name}-{int(time.time())}"
            _force_reasoning(sandbox, "high" if arm_name.endswith("thinking") else args.main_effort)
            history = replay._filler_history(args.depth)
            transcript: list[dict[str, str]] = []
            _agent = None

            for index, user_text in enumerate(turns, start=1):
                ephemeral = _ephemeral_prompt(
                    channel_prompt, sandbox, session_key, user_text,
                    identity=("stripped" if arm_name.startswith("stripped")
                              else (arm_name if arm_name in IDENTITIES else "")),
                )
                # One agent per arm, not one per turn. The live gateway keeps a
                # single agent for a session, and rebuilding it every turn gave
                # each turn a fresh session id -- which is both unfaithful and
                # what made the Codex endpoint reject the second request of
                # every conversation, so the rig could never get past turn one.
                agent = _agent or AIAgent(
                    model=model,
                    ephemeral_system_prompt=ephemeral,
                    enabled_toolsets=["skills"],
                    skip_memory=False,
                    quiet_mode=True,
                    platform="telegram",
                    chat_id=replay.LIFEBOAT_CHAT_ID,
                    thread_id=replay.LIFEBOAT_THREAD_ID,
                    session_id=session_key,
                    **runtime_kwargs,
                )
                _agent = agent
                # The identity and per-turn guidance change every turn, so the
                # reused agent has to be told the new bundle rather than keep
                # the one it was built with.
                for attr in ("ephemeral_system_prompt", "_ephemeral_system_prompt"):
                    if hasattr(agent, attr):
                        setattr(agent, attr, ephemeral)
                result = agent.run_conversation(user_text, conversation_history=list(history)) or {}
                draft = str(result.get("final_response") or result.get("response") or "").strip()
                if not draft:
                    draft = f"[no text returned; keys: {sorted(result)}]"

                if arm_name.startswith("stripped"):
                    # Nothing rewrites its words. That is the point of the arm.
                    delivered, outcome = draft, "raw"
                else:
                    delivered, outcome = _deliver(
                        sandbox, session_key, user_text, draft, editor
                    )

                # The conversation continues on what he would actually have read.
                history.append({"role": "user", "content": user_text})
                history.append({"role": "assistant", "content": delivered})
                transcript.append(
                    {"user": user_text, "draft": draft, "delivered": delivered, "outcome": outcome}
                )

                print(f"\n--- turn {index} [{outcome}] ---")
                print(f"user:      {user_text}")
                if delivered.strip() != draft.strip():
                    print(f"draft:     {draft}")
                    print(f"DELIVERED: {delivered}")
                else:
                    print(f"delivered: {delivered}")

            results[arm_name] = transcript

        if args.out:
            args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\ntranscripts written to {args.out}")

        print("\n--- what the editor did ---")
        for arm_name, transcript in results.items():
            outcomes = ", ".join(t["outcome"] for t in transcript)
            print(f"  {arm_name}: {outcomes}")

        problems = replay._check_isolation(replay._loaded_plugin_names(), before)
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
