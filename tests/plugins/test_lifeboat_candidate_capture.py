from datetime import date
from pathlib import Path
import importlib.util
import sys
from unittest.mock import patch

import yaml
import pytest

from hermes_cli.plugins import PluginManager
from tests.agent.test_turn_context import _FakeAgent, _build


PLUGIN_ID = "lifeboat-emotional-candidate-capture"
MODULE_PATH = Path(__file__).parents[2] / "plugins" / PLUGIN_ID / "candidate_capture.py"
SPEC = importlib.util.spec_from_file_location("lifeboat_candidate_capture_test_module", MODULE_PATH)
assert SPEC and SPEC.loader
candidate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = candidate
SPEC.loader.exec_module(candidate)


def test_bundled_plugin_receives_real_hook_message_and_injects_context(tmp_path, monkeypatch):
    queue_path = tmp_path / "queue.md"
    queue_path.write_text("unused", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({
            "plugins": {
                "enabled": [PLUGIN_ID],
                "entries": {PLUGIN_ID: {"queue_path": str(queue_path)}},
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: "life-advisor")

    manager = PluginManager()
    manager.discover_and_load()
    assert manager._plugins[PLUGIN_ID].enabled
    assert "pre_llm_call" in manager._hooks

    current_message = "A volunteer's reply made me feel like I was too much."
    results = manager.invoke_hook(
        "pre_llm_call",
        session_id="session-current",
        task_id="task-current",
        turn_id="turn-current",
        user_message=current_message,
        conversation_history=[],
        is_first_turn=True,
        model="test-model",
        platform="telegram",
        sender_id="sender-current",
    )
    expected_context = "Possible later emotional candidate: A volunteer's reply made me feel like I was too much"
    assert results == [{"context": expected_context}]

    # Exercise the host's real turn prologue: it passes the current message to
    # the plugin and stores its return for API-time user-message injection.
    with patch("hermes_cli.plugins._plugin_manager", manager):
        context = _build(_FakeAgent(), user_message=current_message)
    assert context.plugin_user_context == results[0]["context"]
    api_user_message = current_message + "\n\n" + context.plugin_user_context
    assert current_message in api_user_message
    assert "Possible later emotional candidate" in api_user_message


def test_plugin_is_disabled_by_existing_config_path(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"disabled": [PLUGIN_ID]}}), encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    manager = PluginManager()
    manager.discover_and_load()
    assert not manager._plugins[PLUGIN_ID].enabled
    assert manager._plugins[PLUGIN_ID].error == "disabled via config"


QUEUE = """---
type: active-queue
owner_profile: life-advisor
---

# Emotional Processing Queue

## Queue contract

## Items

- id: `existing-active`
  status: active
  topic: Existing concrete topic.
  next_point: Continue here.

- id: `existing-pending`
  status: pending
  topic: Another preserved topic.
  next_point: Continue later.

## Separate threads

- `other` — remains separate.
"""


def queue_file(tmp_path: Path, contents: str = QUEUE) -> Path:
    path = tmp_path / "Emotional Processing Queue.md"
    path.write_text(contents, encoding="utf-8")
    return path


def request(text: str) -> str:
    return f"Please save this emotional moment for later bully work: {text}"


def test_ordinary_english_message_returns_no_candidate(tmp_path):
    path = queue_file(tmp_path)
    before = path.read_text(encoding="utf-8")
    result = candidate.capture_candidate("I went to the store and bought apples.", profile_name="life-advisor", queue_path=path)
    assert result.status == "none"
    assert path.read_text(encoding="utf-8") == before


def test_ordinary_hebrew_message_returns_no_candidate(tmp_path):
    path = queue_file(tmp_path)
    result = candidate.capture_candidate("היום קניתי תפוחים וחזרתי הביתה.", profile_name="life-advisor", queue_path=path)
    assert result.status == "none"


def test_hebrew_dating_app_jealousy_signal_proposes_without_persisting(tmp_path):
    path = queue_file(tmp_path)
    before = path.read_text(encoding="utf-8")
    result = candidate.capture_candidate(
        "בבוקר פתחתי אפליקציית היכרויות, ראיתי זוגות וקינאתי בהם והרגשתי שאני פחות שווה.",
        profile_name="life-advisor", queue_path=path,
    )
    assert result.status == "proposal"
    assert "Possible later emotional candidate" in result.message
    assert path.read_text(encoding="utf-8") == before


def test_generic_hebrew_feeling_okay_returns_no_candidate(tmp_path):
    path = queue_file(tmp_path)
    result = candidate.capture_candidate("אני מרגיש בסדר היום.", profile_name="life-advisor", queue_path=path)
    assert result.status == "none"


def test_hebrew_technical_message_returns_no_candidate(tmp_path):
    path = queue_file(tmp_path)
    result = candidate.capture_candidate("עדכנתי את הקוד והבדיקות עברו בהצלחה.", profile_name="life-advisor", queue_path=path)
    assert result.status == "none"


def test_narrow_inferred_signal_proposes_without_persisting(tmp_path):
    path = queue_file(tmp_path)
    before = path.read_text(encoding="utf-8")
    result = candidate.capture_candidate(
        "A volunteer's reply made me feel like I was too much.",
        profile_name="life-advisor", queue_path=path,
    )
    assert result.status == "proposal"
    assert "Possible later emotional candidate" in result.message
    assert path.read_text(encoding="utf-8") == before


def test_explicit_capture_is_minimal_redacted_deduped_and_dated(tmp_path):
    path = queue_file(tmp_path)
    text = request("A reply from @private_person at +972-50-123-4567 felt like I was too much. https://private.example/x")
    result = candidate.capture_candidate(
        text, profile_name="life-advisor", queue_path=path, today=date(2026, 8, 18),
    )
    saved = path.read_text(encoding="utf-8")
    assert result.status == "captured"
    assert "added: 2026-08-18" in saved
    assert "@private_person" not in saved and "+972-50-123-4567" not in saved
    assert "https://private.example/x" not in saved
    assert "[redacted]" in saved
    assert saved.index("existing-active") < saved.index("existing-pending") < saved.index(result.candidate_id)
    duplicate = candidate.capture_candidate(text, profile_name="life-advisor", queue_path=path, today=date(2026, 8, 18))
    assert duplicate.status == "duplicate"


def test_missing_configuration_fails_closed(tmp_path):
    assert candidate.capture_candidate("save this emotional moment", profile_name="life-advisor", queue_path=None).status == "disabled"


def test_malformed_queue_does_not_write(tmp_path):
    path = queue_file(tmp_path, "not a queue\n")
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        candidate.capture_candidate(request("A concrete reply felt like I was too much."), profile_name="life-advisor", queue_path=path)
    assert path.read_text(encoding="utf-8") == before


def test_readback_failure_restores_original_state(tmp_path):
    path = queue_file(tmp_path)
    before = path.read_text(encoding="utf-8")
    calls = 0

    def fail_on_readback(text):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("injected read-back failure")
        return candidate._validate_queue(text)

    with pytest.raises(ValueError, match="injected read-back failure"):
        candidate.capture_candidate(
            request("A reply felt like I was too much."), profile_name="life-advisor", queue_path=path,
            validate_queue=fail_on_readback,
        )
    assert path.read_text(encoding="utf-8") == before
