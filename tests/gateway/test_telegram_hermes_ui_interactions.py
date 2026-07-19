import json
import os
import time

import pytest

from plugins.platforms.telegram.hermes_ui import (
    apply_control,
    apply_text_reply,
    bind_prompt,
    bind_user,
    load_interaction,
    lookup_prompt,
    mark_dispatched,
    prepare_interaction,
    reopen_submission,
)


def _control(state, kind, **matches):
    for index, control in enumerate(state["controls"]):
        if control.get("kind") == kind and all(control.get(key) == value for key, value in matches.items()):
            return index
    raise AssertionError((kind, matches, state["controls"]))


def test_multi_choice_toggles_persist_across_restart_and_submits_values(tmp_path):
    artifact = {
        "type": "form",
        "id": "areas",
        "fields": [{
            "id": "areas",
            "label": "Areas",
            "type": "multi-choice",
            "required": True,
            "options": [
                {"label": "Work", "value": "work"},
                {"label": "Home", "value": "home"},
            ],
        }],
    }
    state = prepare_interaction(artifact, chat_id="123", root=tmp_path)

    first = apply_control(
        state["token"], state["revision"], _control(state, "toggle-option", option_index=0), root=tmp_path
    )
    assert first.outcome == "edit"
    restored = load_interaction(state["token"], root=tmp_path)
    assert restored["values"] == {"areas": ["work"]}

    second = apply_control(
        restored["token"], restored["revision"], _control(restored, "toggle-option", option_index=1), root=tmp_path
    )
    done_state = second.state
    result = apply_control(
        done_state["token"], done_state["revision"], _control(done_state, "submit-form"), root=tmp_path
    )

    assert result.outcome == "submit"
    envelope = json.loads(result.payload.split("\n", 1)[1])
    assert envelope["type"] == "form-response"
    assert envelope["values"] == {"areas": ["work", "home"]}


def test_single_choice_uses_value_and_terminal_submit_is_idempotent(tmp_path):
    artifact = {
        "type": "form",
        "id": "capacity",
        "fields": [{
            "id": "capacity",
            "label": "Capacity",
            "type": "single-choice",
            "options": [{"label": "High", "value": "high"}],
        }],
    }
    state = prepare_interaction(artifact, chat_id="123", root=tmp_path)
    index = _control(state, "select-option", option_index=0)

    selected = apply_control(state["token"], state["revision"], index, root=tmp_path)
    assert selected.outcome == "edit"
    result = apply_control(
        selected.state["token"],
        selected.state["revision"],
        _control(selected.state, "submit-form"),
        root=tmp_path,
    )
    replay = apply_control(state["token"], state["revision"], index, root=tmp_path)

    assert result.outcome == "submit"
    assert '"capacity":"high"' in result.payload
    assert replay.outcome in {"stale", "resolved"}


def test_required_single_choice_accepts_custom_text_in_existing_form_envelope(tmp_path):
    artifact = {
        "type": "form",
        "id": "next-move",
        "fields": [{
            "id": "next_move",
            "label": "What should happen next?",
            "type": "single-choice",
            "required": True,
            "allowCustomAnswer": True,
            "customAnswerLabel": "My own answer",
            "options": [{"label": "Plan", "value": "plan"}],
        }],
    }
    state = prepare_interaction(artifact, chat_id="123", root=tmp_path)

    prompt = apply_control(
        state["token"], state["revision"], _control(state, "request-custom-answer"), root=tmp_path
    )
    assert prompt.outcome == "prompt"
    assert prompt.prompt == "My own answer"
    answered = apply_text_reply(state["token"], "Call the clinic first", root=tmp_path)
    result = apply_control(
        answered.state["token"],
        answered.state["revision"],
        _control(answered.state, "submit-form"),
        root=tmp_path,
    )

    assert result.outcome == "submit"
    envelope = json.loads(result.payload.split("\n", 1)[1])
    assert envelope["schemaVersion"] == 1
    assert envelope["values"] == {"next_move": "Call the clinic first"}


def test_multi_choice_preserves_buttons_and_custom_text_in_one_value_list(tmp_path):
    artifact = {
        "type": "form",
        "id": "areas",
        "fields": [{
            "id": "areas",
            "label": "Areas",
            "type": "multi-choice",
            "required": True,
            "allowCustomAnswer": True,
            "customAnswerLabel": "Add another area",
            "options": [{"label": "Work", "value": "work"}],
        }],
    }
    state = prepare_interaction(artifact, chat_id="123", root=tmp_path)
    selected = apply_control(
        state["token"], state["revision"], _control(state, "toggle-option", option_index=0), root=tmp_path
    )
    prompt = apply_control(
        selected.state["token"],
        selected.state["revision"],
        _control(selected.state, "request-custom-answer"),
        root=tmp_path,
    )
    answered = apply_text_reply(state["token"], "Health", root=tmp_path)
    result = apply_control(
        answered.state["token"],
        answered.state["revision"],
        _control(answered.state, "submit-form"),
        root=tmp_path,
    )

    assert prompt.outcome == "prompt"
    assert result.outcome == "submit"
    envelope = json.loads(result.payload.split("\n", 1)[1])
    assert envelope["values"] == {"areas": ["work", "Health"]}


def test_typed_mixed_form_uses_persistent_prompt_anchor_and_advances(tmp_path):
    artifact = {
        "type": "form",
        "id": "mixed",
        "fields": [
            {"id": "name", "label": "Name", "type": "short-text", "required": True},
            {"id": "when", "label": "When", "type": "time", "required": True},
        ],
    }
    state = prepare_interaction(artifact, chat_id="123", thread_id="7", root=tmp_path)
    prompt = apply_control(
        state["token"], state["revision"], _control(state, "request-text"), root=tmp_path
    )
    assert prompt.outcome == "prompt"
    bind_prompt(state["token"], chat_id="123", message_id="55", root=tmp_path)
    assert lookup_prompt("123", "55", root=tmp_path) == state["token"]

    advanced = apply_text_reply(state["token"], "Noam", root=tmp_path)
    assert advanced.outcome == "edit"
    time_state = advanced.state
    time_prompt = apply_control(
        time_state["token"], time_state["revision"], _control(time_state, "request-text"), root=tmp_path
    )
    invalid = apply_text_reply(state["token"], "24:00", root=tmp_path)
    assert invalid.outcome == "error"
    completed = apply_text_reply(state["token"], "20:00", root=tmp_path)
    assert completed.outcome == "edit"
    result = apply_control(
        completed.state["token"],
        completed.state["revision"],
        _control(completed.state, "submit-form"),
        root=tmp_path,
    )
    assert result.outcome == "submit"
    assert '"name":"Noam"' in result.payload
    assert '"when":"20:00"' in result.payload


def test_text_prompt_cannot_be_opened_twice_from_the_same_revision(tmp_path):
    artifact = {"type": "form", "fields": [{"id": "note", "label": "Note", "type": "long-text"}]}
    state = prepare_interaction(artifact, chat_id="123", root=tmp_path)
    prompt_index = _control(state, "request-text")

    first = apply_control(state["token"], state["revision"], prompt_index, root=tmp_path)
    duplicate = apply_control(state["token"], state["revision"], prompt_index, root=tmp_path)

    assert first.outcome == "prompt"
    assert duplicate.outcome == "stale"
    assert [control["kind"] for control in first.state["controls"]] == ["cancel"]


def test_discussion_prompt_cannot_be_opened_twice(tmp_path):
    state = prepare_interaction({"type": "workload-bars", "bars": []}, chat_id="123", root=tmp_path)
    discuss_index = _control(state, "discuss")

    first = apply_control(state["token"], state["revision"], discuss_index, root=tmp_path)
    duplicate = apply_control(state["token"], state["revision"], discuss_index, root=tmp_path)

    assert first.outcome == "prompt"
    assert duplicate.outcome == "stale"
    assert [control["kind"] for control in first.state["controls"]] == ["cancel"]


def test_failed_dispatch_reopens_the_same_card_and_success_is_terminal(tmp_path):
    artifact = {"type": "form", "fields": [{"id": "ok", "label": "OK", "type": "boolean"}]}
    state = prepare_interaction(artifact, chat_id="123", root=tmp_path)
    selected = apply_control(
        state["token"], state["revision"], _control(state, "select-boolean", value=True), root=tmp_path
    )
    submitted = apply_control(
        state["token"], selected.state["revision"], _control(selected.state, "submit-form"), root=tmp_path
    )
    assert submitted.state["status"] == "submitting"

    reopened = reopen_submission(state["token"], root=tmp_path)
    assert reopened["status"] == "active"
    assert _control(reopened, "submit-form") >= 0

    retry = apply_control(
        reopened["token"], reopened["revision"], _control(reopened, "submit-form"), root=tmp_path
    )
    final = mark_dispatched(state["token"], root=tmp_path)
    assert retry.outcome == "submit"
    assert final["status"] == "submitted"


def test_first_group_click_binds_the_interaction_to_one_user(tmp_path):
    state = prepare_interaction({"type": "checklist", "items": []}, chat_id="group", root=tmp_path)
    bound = bind_user(state["token"], "42", root=tmp_path)
    assert bound["user_id"] == "42"
    assert load_interaction(state["token"], root=tmp_path)["user_id"] == "42"


def test_prepare_interaction_removes_expired_files(tmp_path, monkeypatch):
    old = tmp_path / "old-state.json"
    old.write_text("{}", encoding="utf-8")
    ancient = time.time() - 8 * 24 * 60 * 60
    os.utime(old, (ancient, ancient))
    monkeypatch.setattr("plugins.platforms.telegram.hermes_ui._last_cleanup", {})

    prepare_interaction({"type": "checklist", "items": []}, chat_id="123", root=tmp_path)

    assert not old.exists()


def test_checklist_toggles_then_submits_once(tmp_path):
    artifact = {
        "type": "checklist",
        "id": "morning",
        "items": [{"id": "water", "label": "Drink"}, {"id": "walk", "label": "Walk"}],
    }
    state = prepare_interaction(artifact, chat_id="123", root=tmp_path)
    changed = apply_control(
        state["token"], state["revision"], _control(state, "toggle-item", item_index=0), root=tmp_path
    )
    result = apply_control(
        changed.state["token"], changed.state["revision"], _control(changed.state, "submit-checklist"), root=tmp_path
    )

    assert result.outcome == "submit"
    assert "water" in result.payload
    assert "walk" not in result.payload


def test_nested_action_submits_hidden_text_not_visible_label(tmp_path):
    artifact = {
        "type": "task-table",
        "rows": [{
            "id": "1",
            "title": "Task",
            "actions": [{"id": "start", "label": "Start", "submitText": "Start task id=1 safely"}],
        }],
    }
    state = prepare_interaction(artifact, chat_id="123", root=tmp_path)
    action = state["controls"][_control(state, "submit-action")]
    assert "Start task id=1 safely" not in action["callback_data"]

    result = apply_control(
        state["token"], state["revision"], _control(state, "submit-action"), root=tmp_path
    )
    assert result.outcome == "submit"
    assert result.payload == "Start task id=1 safely"


def test_callback_data_stays_short_and_stale_revision_does_not_mutate(tmp_path):
    artifact = {
        "type": "form",
        "fields": [{
            "id": "many",
            "label": "Many",
            "type": "multi-choice",
            "options": [{"label": "x" * 200, "value": str(index)} for index in range(12)],
        }],
    }
    state = prepare_interaction(artifact, chat_id="123", root=tmp_path)
    assert max(len(control["callback_data"].encode()) for control in state["controls"]) <= 64
    changed = apply_control(
        state["token"], state["revision"], _control(state, "toggle-option", option_index=0), root=tmp_path
    )
    stale = apply_control(
        state["token"], state["revision"], _control(state, "toggle-option", option_index=1), root=tmp_path
    )
    assert changed.outcome == "edit"
    assert stale.outcome == "stale"
    assert load_interaction(state["token"], root=tmp_path)["values"] == {"many": ["0"]}


def test_long_checklist_is_paginated_and_mark_all_is_persistent(tmp_path):
    artifact = {
        "type": "checklist",
        "id": "long",
        "items": [{"id": str(index), "label": f"Item {index}"} for index in range(20)],
    }
    state = prepare_interaction(artifact, chat_id="123", root=tmp_path)
    assert len([control for control in state["controls"] if control["kind"] == "toggle-item"]) == 8

    marked = apply_control(
        state["token"], state["revision"], _control(state, "select-all-items"), root=tmp_path
    )
    assert len(marked.state["selected_items"]) == 20
    next_page = apply_control(
        marked.state["token"],
        marked.state["revision"],
        _control(marked.state, "page", page=1),
        root=tmp_path,
    )
    assert next_page.state["page"] == 1
    assert any(control["text"].endswith("Item 8") for control in next_page.state["controls"])


def test_task_batch_cycles_each_decision_then_submits_preview_only(tmp_path):
    artifact = {
        "type": "flowstate-task-batch",
        "id": "batch",
        "tasks": [{"id": "a", "title": "Alpha"}, {"id": "b", "title": "Beta"}],
    }
    state = prepare_interaction(artifact, chat_id="123", root=tmp_path)
    changed = apply_control(
        state["token"], state["revision"], _control(state, "cycle-task", task_index=0), root=tmp_path
    )
    assert changed.state["task_decisions"] == {"a": "today"}
    result = apply_control(
        changed.state["token"],
        changed.state["revision"],
        _control(changed.state, "submit-task-batch"),
        root=tmp_path,
    )
    assert result.outcome == "submit"
    assert '"a":"today"' in result.payload
    assert "do not mutate FlowState" in result.payload


@pytest.mark.parametrize(
    "artifact",
    [
        {"type": "checklist", "items": [{"id": "a", "label": "A"}]},
        {"type": "questionnaire", "questions": [{"name": "a", "question": "A"}]},
        {"type": "form", "fields": [{"id": "a", "label": "A", "type": "boolean"}]},
        {"type": "task-triage", "task": {"id": "a", "title": "A"}},
        {"type": "flowstate-task-batch", "tasks": [{"id": "a", "title": "A"}]},
        {"type": "flowstate-planning-session", "categories": [], "tasks": [{"id": "a", "title": "A"}]},
        {"type": "flowstate-next-block", "task": {"id": "a", "title": "A"}, "actions": []},
        {"type": "planning-funnel", "steps": [{"id": "a", "label": "A"}]},
        {"type": "task-breakdown", "task": {"id": "a", "title": "A"}, "steps": []},
        {"type": "task-context", "task": {"id": "a", "title": "A"}, "actions": []},
        {"type": "task-table", "columns": ["task"], "rows": [{"id": "a", "title": "A"}]},
        {"type": "mini-kanban", "lanes": [{"id": "a", "title": "A", "tasks": []}]},
        {"type": "day-timeline", "date": "2026-07-19", "blocks": []},
        {"type": "mutation-preview", "changes": [], "actions": []},
        {"type": "urgency-energy-matrix", "xAxis": "energy", "yAxis": "urgency", "cells": []},
        {"type": "workload-bars", "bars": [{"id": "a", "label": "A", "value": 1}]},
        {"type": "task-graph", "nodes": [{"id": "a", "label": "A"}], "edges": []},
    ],
)
def test_every_desktop_artifact_has_native_controls_or_safe_discussion(artifact, tmp_path):
    state = prepare_interaction(artifact, chat_id="123", root=tmp_path)

    assert state["controls"], artifact["type"]
    assert all(control["callback_data"].startswith("hu:") for control in state["controls"])
