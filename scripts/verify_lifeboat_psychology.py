"""Verify the installed Life-Boat psychology layer and privacy boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

import yaml

from gateway.lifeboat_psychology import (
    build_signal_guidance,
    classify_lifeboat_signals,
    record_lifeboat_trajectory,
)
from gateway.lifeboat_evaluation import build_multiturn_gold_scenarios, scenario_count_by_category


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    source_root = Path(__file__).resolve().parents[1]
    installed_root = Path("/home/endlessblink/.hermes/hermes-agent")
    relative_files = (
        Path("gateway/run.py"),
        Path("gateway/lifeboat_followups.py"),
        Path("gateway/lifeboat_psychology.py"),
        Path("gateway/lifeboat_evaluation.py"),
        Path("gateway/lifeboat_surface.py"),
        Path("gateway/lifeboat_modes.py"),
        Path("gateway/lifeboat_contracts.py"),
        Path("gateway/lifeboat_handoff.py"),
        Path("gateway/lifeboat_status.py"),
        Path("gateway/session_context.py"),
        Path("cron/jobs.py"),
        Path("cron/scheduler.py"),
    )
    # Discover Life-Boat modules on both sides rather than trusting the list
    # above. A module present in only one tree is the most dangerous kind of
    # drift: it never shows up as a content mismatch, because there is nothing
    # to compare it against.
    source_found = {p.name for p in (source_root / "gateway").glob("lifeboat_*.py")}
    installed_found = {p.name for p in (installed_root / "gateway").glob("lifeboat_*.py")}
    for name in sorted(installed_found - source_found):
        print(f"FAIL installed-only Life-Boat module absent from the tree: gateway/{name}")
        return 1
    for name in sorted(source_found - installed_found):
        print(f"FAIL Life-Boat module in the tree was never installed: gateway/{name}")
        return 1
    discovered = tuple(Path("gateway") / name for name in sorted(source_found))

    for relative in tuple(relative_files) + discovered:
        source = source_root / relative
        installed = installed_root / relative
        if not installed.is_file() or _sha256(source) != _sha256(installed):
            print(f"FAIL source/installed mismatch: {relative}")
            return 1

    runtime_text = (source_root / "gateway" / "run.py").read_text(encoding="utf-8")
    for marker in (
        "prepare_lifeboat_inbound_guidance(",
        "event.channel_prompt =",
        "agent.lifeboat_mode = is_lifeboat_source(source)",
        "goal continuation: suppressed for Life-Boat source",
        "arm_lifeboat_prompts(",
        "_streaming_enabled = False",
        "user_text=event.text or \"\"",
        "with use_cron_store(profile_home):",
        "getattr(runner, \"adapters\", {})",
    ):
        if marker not in runtime_text:
            print(f"FAIL Life-Boat runtime integration missing {marker!r}")
            return 1

    live_config_path = Path("/home/endlessblink/.hermes/config.yaml")
    if not live_config_path.is_file():
        print("FAIL live Hermes config is missing")
        return 1
    live_config = yaml.safe_load(live_config_path.read_text(encoding="utf-8")) or {}
    gateway = live_config.get("gateway") or {}
    served = gateway.get("multiplex_served_profiles") or []
    routes = gateway.get("profile_routes") or {}
    topic_route = ((routes.get("telegram") or {}).get("topics") or {}).get(
        "-1004230590253", {}
    )
    if "life-advisor" not in served or topic_route.get("2") != "life-advisor":
        print("FAIL live Telegram topic 2 is not mapped to life-advisor")
        return 1

    profile_config_path = Path("/home/endlessblink/.hermes/profiles/life-advisor/config.yaml")
    profile_config = yaml.safe_load(profile_config_path.read_text(encoding="utf-8")) or {}
    channel_prompts = ((profile_config.get("telegram") or {}).get("channel_prompts") or {})
    if "-1004230590253" not in channel_prompts or "602196268" not in channel_prompts:
        print("FAIL live Life-Boat channel prompts are incomplete")
        return 1
    private_prompt = str(channel_prompts.get("602196268") or "")
    for marker in (
        "emotions are not directly voluntary",
        "Do not use a fixed three-part structure",
        "explicitly approves",
    ):
        if marker not in private_prompt:
            print(f"FAIL Life-Boat prompt missing {marker!r}")
            return 1

    coaching_text = (source_root / "gateway" / "lifeboat_followups.py").read_text(
        encoding="utf-8"
    )
    for forbidden in ("repair_lifeboat_closure", "_CLOSING_INSIGHT_RE"):
        if forbidden in runtime_text or forbidden in coaching_text:
            print(f"FAIL brittle Life-Boat phrase repair remains: {forbidden!r}")
            return 1
    for marker in (
        "mode=",
        "select_lifeboat_turn_policy",
        "one concrete",
        "tentatively",
        "If the user asks for action",
        "proactive_followups_enabled",
        "stale queues created before the opt-in boundary",
        "record_lifeboat_response_fingerprint",
    ):
        if marker not in coaching_text:
            print(f"FAIL Life-Boat coaching contract missing {marker!r}")
            return 1

    cron_path = Path("/home/endlessblink/.hermes/profiles/life-advisor/cron/jobs.json")
    cron_state = json.loads(cron_path.read_text(encoding="utf-8")) if cron_path.is_file() else {}
    morning_jobs = [
        job for job in cron_state.get("jobs", [])
        if isinstance(job, dict) and job.get("name") == "lifeboat-morning-check-in"
    ]
    from gateway.lifeboat_morning import (
        morning_job_problems,
        proactive_delivery_problems,
        resolve_morning_policy,
    )

    morning_policy = resolve_morning_policy()
    morning_job = morning_jobs[0] if morning_jobs else None
    for problem in morning_job_problems(
        morning_job,
        morning_policy,
        topic="telegram:-1004230590253:2",
    ):
        print(f"FAIL {problem}")
        return 1

    # Every Life-Boat proactive job, in every store, must reach the Life-Boat
    # topic. A bare platform target resolves to the home channel, which on this
    # install belongs to another assistant.
    base_cron = Path("/home/endlessblink/.hermes/cron/jobs.json")
    base_state = json.loads(base_cron.read_text(encoding="utf-8")) if base_cron.is_file() else {}
    all_jobs = list(cron_state.get("jobs", [])) + list(base_state.get("jobs", []))
    for problem in proactive_delivery_problems(all_jobs, topic="telegram:-1004230590253:2"):
        print(f"FAIL {problem}")
        return 1

    contract = source_root / "docs" / "lifeboat-psychology-architecture.md"
    contract_text = contract.read_text(encoding="utf-8") if contract.is_file() else ""
    for marker in (
        "## Safety contract",
        "## Memory boundary",
        "## Evaluation contract",
        "usefully unfinished",
        "advice-only/closure rate",
    ):
        if marker not in contract_text:
            print(f"FAIL architecture contract missing {marker}")
            return 1

    cases = {
        "loop": "אני תקוע בלופ של ביקורת עצמית",
        "depressive": "I feel hopeless and have no energy",
        "crisis": "I might hurt myself tonight",
    }
    signals = {name: classify_lifeboat_signals(text) for name, text in cases.items()}
    if not signals["loop"].thought_loop or not signals["loop"].self_criticism:
        print("FAIL Hebrew loop/self-criticism routing")
        return 1
    if not signals["depressive"].depressive_thoughts or not signals["crisis"].possible_crisis:
        print("FAIL depressive/crisis routing")
        return 1

    gold_set = build_multiturn_gold_scenarios()
    if len(gold_set) != 60 or scenario_count_by_category(item.scenario for item in gold_set) != {
        "thought_loop": 12,
        "self_criticism": 10,
        "depressive_low_energy": 10,
        "explicit_safety": 8,
        "proactive_reply": 8,
        "premature_closure": 6,
        "mixed_hebrew_rtl": 6,
    }:
        print("FAIL Life-Boat multi-turn gold-set shape")
        return 1

    with tempfile.TemporaryDirectory() as directory:
        profile_home = Path(directory)
        trajectory = record_lifeboat_trajectory(
            profile_home,
            "verification-session",
            cases["crisis"],
        )
        guidance = build_signal_guidance("yes", trajectory)
        state = json.loads(
            (profile_home / "state" / "lifeboat-psychology.json").read_text(
                encoding="utf-8"
            )
        )
        serialized = json.dumps(state, ensure_ascii=False)
        if "I might hurt myself tonight" in serialized:
            print("FAIL raw user text persisted in trajectory state")
            return 1
        if "safe right now" not in guidance:
            print("FAIL recent safety context was not carried into the next turn")
            return 1

    print("Life-Boat psychology source/installed, routing, trajectory, and privacy checks verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
