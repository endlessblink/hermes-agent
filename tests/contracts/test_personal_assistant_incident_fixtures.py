from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent.personal_assistant_turn_state import replay_turn_events


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "personal_assistant" / "incidents.json"
REQUIRED_INCIDENTS = {
    "runtime-session-rejected",
    "duplicate-user-submission",
    "stale-actionable-options",
    "stale-context-options",
}
ALLOWED_PHASES = {
    "idle",
    "submitting",
    "restoring",
    "awaiting-context",
    "planning",
    "awaiting-approval",
    "completed",
    "canceled",
    "recoverable-failure",
}


def test_personal_assistant_incidents_are_replayable_contracts() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert payload["schemaVersion"] == 1
    incidents = payload["incidents"]
    assert {incident["id"] for incident in incidents} == REQUIRED_INCIDENTS

    for incident in incidents:
        assert incident["observed"]
        assert incident["violatedInvariants"]
        assert incident["initialState"]["phase"] in ALLOWED_PHASES
        assert incident["events"]
        assert incident["expected"]["finalPhase"] in ALLOWED_PHASES
        assert incident["expected"]["visibleOutcomeCount"] == 1
        assert incident["expected"]["acceptedSubmissionCount"] <= 1


def test_incident_evidence_is_preserved_with_an_exact_hash() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    evidence = payload["evidence"]
    path = ROOT / evidence["path"]

    assert path.is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == evidence["sha256"]


def test_personal_assistant_incidents_replay_to_the_required_outcome() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    for incident in payload["incidents"]:
        actual = replay_turn_events(incident["initialState"], incident["events"])

        for key, expected in incident["expected"].items():
            assert actual[key] == expected, f"{incident['id']}: {key}"
