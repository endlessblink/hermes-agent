"""Post-loop turn finalization for ``run_conversation``.

Extracted from ``agent/conversation_loop.py`` as part of the god-file
decomposition campaign (``~/.hermes/plans/god-file-decomposition.md``, Phase 1
step 4 — the post-loop ``TurnFinalizer`` seam). ``run_conversation``'s tail
(everything after the main tool-calling ``while`` loop) is lifted here verbatim:
budget-exhaustion summary, trajectory save, session persist, turn diagnostics,
response transforms, result-dict assembly, steer drain, and the memory/skill
review trigger.

Behavior-neutral: the body is moved unchanged. All ``agent.*`` side effects fire
exactly as before; only the post-loop *locals* are passed in as keyword args, and
the assembled ``result`` dict is returned to ``run_conversation`` which returns it
to the caller. The function is synchronous with a single return — mirroring the
region it replaces (no awaits, no early returns).

Module ``logger`` is imported lazily inside the body (``from
agent.conversation_loop import logger``) so this module never imports
``agent.conversation_loop`` at import time -> no import cycle, and the log records
keep the exact logger name (``"agent.conversation_loop"``).
"""

from __future__ import annotations

import json
import os
import re

from agent.codex_responses_adapter import _summarize_user_message_for_log


_SUPERSESSION_RE = re.compile(
    r"\b(stop|undo|rollback|roll back|never mind|nevermind|new topic|change topic|just verify|don't do that anymore)\b",
    re.I,
)
_PATH_MENTION_RE = re.compile(r"(?:/|~/?|[A-Za-z]:\\|\.{1,2}/)[^\s`'\")\]}<>]+")
_CONTINUATION_WORDS = frozenset(
    {
        "continue",
        "resume",
        "proceed",
        "go",
        "on",
        "keep",
        "going",
        "and",
        "please",
        "the",
        "goal",
        "this",
        "it",
        "working",
        "המשך",
        "תמשיך",
        "קדימה",
    }
)
_CONTINUATION_VERBS = frozenset(
    {"continue", "resume", "proceed", "go", "keep", "המשך", "תמשיך", "קדימה"}
)


def _is_checkpoint_continuation(user_text, existing_state):
    if str(existing_state.get("status") or "") != "checkpoint":
        return False
    words = re.findall(r"[\w\u0590-\u05ff]+", str(user_text or "").lower())
    return (
        0 < len(words) <= 6
        and any(word in _CONTINUATION_VERBS for word in words)
        and all(word in _CONTINUATION_WORDS for word in words)
    )


def _checkpoint_tool_arguments(raw_arguments):
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _checkpoint_is_preview(tool_name, arguments):
    name = str(tool_name or "").strip().lower()
    if name in {"notion_mutation", "notion_flowstate_activate"}:
        return arguments.get("mode", "preview") != "apply"
    if "flowstate" not in name:
        return False
    is_read = any(token in name for token in ("list", "search", "read", "get", "health"))
    return not is_read and arguments.get("preview", True) is not False


def _checkpoint_phase_for_tool(tool_name, arguments=None):
    """Return a bounded user-facing phase for a completed tool result."""
    name = str(tool_name or "").strip().lower()
    arguments = arguments or {}
    if not name:
        return "Completed one tool step"
    if _checkpoint_is_preview(name, arguments):
        if name == "notion_mutation":
            return "Prepared a Notion change preview"
        return "Prepared a FlowState change preview"
    if name == "notion_flowstate_activate":
        return "Activated a Notion task in FlowState"
    if "flowstate" in name and any(token in name for token in ("list", "search", "read", "get")):
        return "Read FlowState tasks"
    if "flowstate" in name and "preview" in name:
        return "Prepared a FlowState change preview"
    if "notion" in name and any(token in name for token in ("list", "search", "read", "get")):
        return "Read Notion tasks"
    if "notion" in name and "preview" in name:
        return "Prepared a Notion change preview"
    readable = re.sub(r"[_\-.]+", " ", name)
    readable = re.sub(r"\s+", " ", readable).strip()
    return f"Completed {readable}" if readable else "Completed one tool step"


def _checkpoint_pending_phase(last_tool_name, arguments=None, *, failed=False):
    name = str(last_tool_name or "").strip().lower()
    arguments = arguments or {}
    if failed and "flowstate" in name and "search" in name:
        return "Correct or retry the failed FlowState task search, then continue the requested work."
    if failed and "flowstate" in name:
        return "Correct or retry the failed FlowState step, then continue the requested work."
    if failed and "notion" in name:
        return "Correct or retry the failed Notion step, then continue the requested work."
    if failed:
        return "Correct or retry the latest failed tool step, then continue the requested work."
    if _checkpoint_is_preview(name, arguments):
        system = "Notion" if name == "notion_mutation" else "FlowState"
        verification = "remote" if system == "Notion" else "canonical"
        return (
            f"Review the {system} change preview; apply it only if it is already approved, "
            f"then verify the {verification} result."
        )
    if "flowstate" in name and any(token in name for token in ("list", "search", "read", "get")):
        return "Review the latest FlowState task list and continue the requested work."
    if "notion" in name and any(token in name for token in ("list", "search", "read", "get")):
        return "Review the latest Notion task data and continue the requested work."
    if name:
        phase = _checkpoint_phase_for_tool(name, arguments).removeprefix("Completed ").lower()
        return f"Review the latest {phase} result and continue the requested work."
    return "Continue the requested work from this checkpoint."


def _checkpoint_tool_result_failed(message):
    content = message.get("content") if isinstance(message, dict) else None
    from agent.tool_guardrails import classify_tool_failure

    payload = content
    tool_name = str(message.get("name") or "") if isinstance(message, dict) else ""
    rendered = content if isinstance(content, str) else json.dumps(content, default=str)
    classified_failed, _ = classify_tool_failure(tool_name, rendered)
    if classified_failed:
        return True
    if isinstance(content, str):
        stripped = content.strip()
        if re.search(r"(?im)^\s*(?:[-*]\s*)?(?:tool\s+)?error\s*:", stripped):
            return True
        status_match = re.search(r"(?im)^\s*(?:[-*]\s*)?status\s*:\s*(\d{3})\b", stripped)
        if status_match and int(status_match.group(1)) >= 400:
            return True
        try:
            payload = json.loads(stripped)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
    if not isinstance(payload, dict):
        return False
    if payload.get("error") not in (None, "", False, {}):
        return True
    if payload.get("ok") is False or payload.get("success") is False:
        return True
    status = payload.get("status")
    return isinstance(status, int) and status >= 400


def _build_iteration_checkpoint(messages, current_turn_user_idx=None, turn_id=None):
    """Build a deterministic resume contract without another model call."""
    marked_index = next(
        (
            index
            for index in range(len(messages or []) - 1, -1, -1)
            if isinstance(messages[index], dict)
            and turn_id
            and messages[index].get("_turn_id") == turn_id
        ),
        None,
    )
    if marked_index is not None:
        current_turn_user_idx = marked_index
    elif current_turn_user_idx is None:
        current_turn_user_idx = next(
            (
                index
                for index in range(len(messages or []) - 1, -1, -1)
                if isinstance(messages[index], dict) and messages[index].get("role") == "user"
            ),
            -1,
        )
    try:
        start_index = max(0, int(current_turn_user_idx) + 1)
    except (TypeError, ValueError):
        start_index = 0
    turn_messages = list(messages or [])[start_index:]
    completed = []
    completed_seen = set()
    last_tool_name = ""
    last_tool_arguments = {}
    unresolved_failures = {}
    call_details = {}
    for message in turn_messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id") or "")
                function = call.get("function") or {}
                name = str(function.get("name") or "") if isinstance(function, dict) else ""
                if call_id and name:
                    call_details[call_id] = {
                        "name": name,
                        "arguments": _checkpoint_tool_arguments(function.get("arguments")),
                    }
        if message.get("role") != "tool":
            continue
        detail = call_details.get(str(message.get("tool_call_id") or ""), {})
        name = str(message.get("name") or detail.get("name") or "")
        if not name:
            continue
        arguments = detail.get("arguments") or {}
        last_tool_name = name
        last_tool_arguments = arguments
        if _checkpoint_tool_result_failed(message):
            unresolved_failures.pop(name, None)
            unresolved_failures[name] = arguments
            continue
        unresolved_failures.pop(name, None)
        phase = _checkpoint_phase_for_tool(name, arguments)
        if phase not in completed_seen:
            completed_seen.add(phase)
            completed.append(phase)

    last_failed_tool_name = next(reversed(unresolved_failures), "")
    last_failed_tool_arguments = unresolved_failures.get(last_failed_tool_name, {})
    pending = _checkpoint_pending_phase(
        last_failed_tool_name or last_tool_name,
        last_failed_tool_arguments if last_failed_tool_name else last_tool_arguments,
        failed=bool(last_failed_tool_name),
    )
    checkpoint = {
        "version": 1,
        "reason": "iteration_budget_exhausted",
        "resumable": True,
        "completed_phases": completed[:8],
        "pending_phase": pending,
    }
    completed_text = (
        ", ".join(completed[:8])
        if completed
        else "No durable phase was completed before the pause"
    )
    response = (
        "I paused at a resumable checkpoint after reaching this turn's work limit.\n\n"
        f"Completed: {completed_text}.\n"
        f"Next: {pending}\n\n"
        "Send **Continue** to resume in this conversation."
    )
    return checkpoint, response


def _state_text(value, limit=1200):
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        text = "\n".join(part for part in parts if part)
    elif isinstance(value, dict):
        text = str(value.get("text") or value.get("content") or value)
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 15].rstrip() + " ...[truncated]"


def _state_relevant_files(messages, limit=12):
    seen = {}
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        chunks = [_state_text(msg.get("content"), 2000), str(msg.get("tool_calls") or "")]
        for chunk in chunks:
            for match in _PATH_MENTION_RE.findall(chunk):
                candidate = match.rstrip(".,:;")
                if candidate and candidate not in seen:
                    seen[candidate] = None
                    if len(seen) >= limit:
                        return list(seen)
    return list(seen)


def _lane_for_turn(agent, messages):
    """Best-effort workspace record for this turn: repo, branch, agent jobs.

    Derived from git and from the arguments of commands actually run, never
    from a regex over prose. Returns ``None`` when nothing is identifiable, so
    a chatty turn cannot erase a lane an earlier turn established.
    """
    try:
        from agent.lane_resolver import resolve_lane
        from agent.runtime_cwd import resolve_context_cwd

        cwd = resolve_context_cwd() or ""
        try:
            from tools.process_registry import process_registry

            processes = process_registry.list_sessions()
        except Exception:
            processes = []

        def _is_repo(path):
            try:
                from tui_gateway import git_probe

                return bool(git_probe.repo_root(path))
            except Exception:
                return False

        def _branch(path):
            try:
                from tui_gateway import git_probe

                return git_probe.branch(path) or None
            except Exception:
                return None

        lane = resolve_lane(
            messages or [],
            session_cwd=cwd,
            process_sessions=processes,
            is_repo=_is_repo,
            branch_probe=_branch,
        )
        if lane is None:
            return None
        record = {"repo_path": lane.repo_path, "source": lane.source}
        for key in ("branch", "external_job", "prompt_file"):
            value = getattr(lane, key, None)
            if value:
                record[key] = value
        return record
    except Exception:
        return None


def _patch_completed_turn_working_state(
    agent,
    *,
    original_user_message,
    final_response,
    messages,
    interrupted,
    failed,
    continuation_checkpoint=None,
):
    db = getattr(agent, "_session_db", None)
    session_id = getattr(agent, "session_id", "") or ""
    if db is None or not session_id:
        return
    user_text = _state_text(original_user_message, 800)
    try:
        # Gateway-owned intentional Stop clears its marker synchronously before
        # interrupting the agent. Preserve any marker that remains here when an
        # infrastructure watchdog interrupted the turn, so restart recovery can
        # replay the exact accepted prompt instead of silently losing it.
        if interrupted:
            return
        db.patch_working_state(
            session_id,
            {"pending_turn": None},
            source="turn_finalizer",
        )
        if user_text and _SUPERSESSION_RE.search(user_text):
            db.patch_working_state(
                session_id,
                {
                    "active_task": "",
                    "status": "superseded",
                    "superseded_reason": user_text,
                },
                source="turn_supersession",
            )
            return
        if not final_response or failed:
            return
        if continuation_checkpoint:
            existing = db.get_working_state(session_id)
            active_task = user_text
            is_continuation = _is_checkpoint_continuation(user_text, existing)
            if is_continuation:
                active_task = str(existing.get("active_task") or user_text)
            completed_actions = []
            prior_actions = list(existing.get("completed_actions") or []) if is_continuation else []
            for action in prior_actions + list(
                continuation_checkpoint.get("completed_phases") or []
            ):
                if action and action not in completed_actions:
                    completed_actions.append(action)
            db.patch_working_state(
                session_id,
                {
                    "active_task": active_task,
                    "status": "checkpoint",
                    "phase": continuation_checkpoint.get("pending_phase"),
                    "completed_actions": completed_actions[:16],
                    "blockers": [
                        "This turn reached its iteration budget before the workflow finished."
                    ],
                    "last_verified_state": (
                        "The resumable checkpoint and completed tool results were persisted "
                        "to this conversation."
                    ),
                },
                source="turn_iteration_checkpoint",
            )
            return
        patch = {
            "active_task": user_text,
            "status": "answered",
            "phase": "ready for next user turn",
            "relevant_files": _state_relevant_files(messages),
            "completed_actions": [_state_text(final_response, 800)],
            "last_verified_state": "Last completed assistant response was persisted to the session transcript.",
        }
        lane = _lane_for_turn(agent, messages)
        if lane is not None:
            # Only ever *set* a lane, never clear one: a turn that mentions no
            # workspace does not mean the workspace changed. The next session
            # reads this back (agent/lane_recall.py) instead of guessing.
            patch["lane"] = lane
        db.patch_working_state(session_id, patch, source="turn_finalizer")
    except Exception as exc:
        try:
            from agent.conversation_loop import logger
            logger.debug("working-state turn update failed: %s", exc)
        except Exception:
            pass


def finalize_turn(
    agent,
    *,
    final_response,
    api_call_count,
    interrupted,
    failed,
    messages,
    conversation_history,
    effective_task_id,
    turn_id,
    user_message,
    original_user_message,
    _should_review_memory,
    _turn_exit_reason,
    _pending_verification_response=None,
    current_turn_user_idx=None,
):
    """Run the post-loop finalization and return the turn ``result`` dict.

    Lifted verbatim from ``run_conversation`` (the region after the main agent
    loop). See module docstring.
    """
    from agent.conversation_loop import logger

    budget_exhausted = (
        api_call_count >= agent.max_iterations
        or agent.iteration_budget.remaining <= 0
    )
    budget_fallback_eligible = (
        budget_exhausted
        and not interrupted
        and not failed
        and str(_turn_exit_reason) in {"unknown", "budget_exhausted"}
    )
    continuation_budget_exhausted = (
        final_response is None
        and bool(_pending_verification_response)
        and budget_fallback_eligible
    )

    iteration_limit_fallback = False
    preserved_verification_fallback = False
    continuation_checkpoint = None
    if continuation_budget_exhausted:
        # A verification/continuation gate deliberately withheld a composed
        # answer, then consumed the remaining budget before producing a newer
        # one. Preserve that exact answer instead of replacing it with another
        # fallible model call. The explicit pending value is the provenance
        # guard: unrelated error/recovery exits can never enter this branch.
        final_response = _pending_verification_response
        _turn_exit_reason = f"max_iterations_reached({api_call_count}/{agent.max_iterations})"
        iteration_limit_fallback = True
        preserved_verification_fallback = True
    elif (
        final_response is None
        and budget_fallback_eligible
        and bool(getattr(agent, "_resumable_iteration_checkpoint", False))
    ):
        _turn_exit_reason = f"max_iterations_reached({api_call_count}/{agent.max_iterations})"
        continuation_checkpoint, final_response = _build_iteration_checkpoint(
            messages,
            current_turn_user_idx=current_turn_user_idx,
            turn_id=turn_id,
        )
        iteration_limit_fallback = True
    elif final_response is None and budget_fallback_eligible:
        # Budget exhausted — ask the model for a summary via one extra
        # API call with tools stripped.  _handle_max_iterations injects a
        # user message and makes a single toolless request.
        _turn_exit_reason = f"max_iterations_reached({api_call_count}/{agent.max_iterations})"
        agent._emit_status(
            f"⚠️ Iteration budget exhausted ({api_call_count}/{agent.max_iterations}) "
            "— asking model to summarise"
        )
        if not agent.quiet_mode:
            agent._safe_print(
                f"\n⚠️  Iteration budget exhausted ({api_call_count}/{agent.max_iterations}) "
                "— requesting summary..."
            )
        final_response = agent._handle_max_iterations(messages, api_call_count)
        iteration_limit_fallback = True

    if iteration_limit_fallback:
        # If running as a kanban worker, signal the dispatcher that the
        # worker could not complete (rather than treating it as a
        # protocol violation). This applies whether the user-facing fallback
        # came from the summary call or an explicitly pending continuation;
        # both exhausted the task budget and must advance the failure circuit.
        #
        # We route through ``_record_task_failure(outcome="timed_out")``
        # rather than ``kanban_block`` so this counts toward the dispatcher's
        # consecutive-failure circuit breaker (#29747 gap 2).
        _kanban_task = os.environ.get("HERMES_KANBAN_TASK")
        if _kanban_task:
            try:
                from hermes_cli import kanban_db as _kb
                _conn = _kb.connect()
                try:
                    _kb._record_task_failure(
                        _conn,
                        _kanban_task,
                        error=(
                            f"Iteration budget exhausted "
                            f"({api_call_count}/{agent.max_iterations}) — "
                            "task could not complete within the allowed "
                            "iterations"
                        ),
                        outcome="timed_out",
                        release_claim=True,
                        end_run=True,
                        event_payload_extra={
                            "budget_used": api_call_count,
                            "budget_max": agent.max_iterations,
                        },
                    )
                    logger.info(
                        "recorded budget-exhausted failure for task %s (%d/%d)",
                        _kanban_task, api_call_count, agent.max_iterations,
                    )
                finally:
                    try:
                        _conn.close()
                    except Exception:
                        pass
            except Exception:
                logger.warning(
                    "Failed to record budget-exhausted failure for task %s",
                    _kanban_task,
                    exc_info=True,
                )

    # Determine if conversation completed successfully
    normal_text_response = str(_turn_exit_reason).startswith("text_response(")
    completed = (
        final_response is not None
        and not failed
        and (
            api_call_count < agent.max_iterations
            or normal_text_response
        )
    )

    # Post-loop cleanup must never lose the response.  Trajectory save,
    # resource teardown, and session persistence all touch fallible
    # surfaces — file I/O / JSON serialization (_save_trajectory), remote
    # VM/browser teardown over the network (_cleanup_task_resources), and
    # SQLite writes (_persist_session).  A raise from any of them used to
    # propagate straight out of run_conversation, discarding the partial
    # final_response the caller is waiting for (subprocess wrappers saw an
    # empty stdout with no traceback — #8049).  Each step is now guarded
    # independently so one failure can't skip the others, and any errors
    # are surfaced on the result dict via ``cleanup_errors`` rather than
    # killing the turn.
    _cleanup_errors = []

    # Save trajectory if enabled.  ``user_message`` may be a multimodal
    # list of parts; the trajectory format wants a plain string.
    try:
        agent._save_trajectory(messages, _summarize_user_message_for_log(user_message), completed)
    except Exception as _save_err:
        _cleanup_errors.append(f"save_trajectory: {_save_err}")
        logger.error("finalize_turn: _save_trajectory failed: %s", _save_err, exc_info=True)

    # Clean up VM and browser for this task after conversation completes
    try:
        agent._cleanup_task_resources(effective_task_id)
    except Exception as _cleanup_err:
        _cleanup_errors.append(f"cleanup_task_resources: {_cleanup_err}")
        logger.error("finalize_turn: _cleanup_task_resources failed: %s", _cleanup_err, exc_info=True)

    # Persist session to both JSON log and SQLite only after private retry
    # scaffolding has been removed. Otherwise a later user "continue" turn
    # can replay assistant("(empty)") / recovery nudges and fall into the
    # same empty-response loop again.
    try:
        agent._drop_trailing_empty_response_scaffolding(messages)

        # When the turn was interrupted and the last message is a tool
        # result, append a synthetic assistant message to close the
        # tool-call sequence. Without this, the session persists a
        # ``tool → user`` alternation that strict providers (Gemini,
        # Claude) reject, causing them to hallucinate a continuation of
        # the user's message on the next turn (#48879).
        #
        # ``_drop_trailing_empty_response_scaffolding`` only rewinds the
        # tool tail when an empty-response scaffolding flag is present; a
        # clean ``/stop`` interrupt after a successful tool sets no such
        # flag, so the tool result survives as the tail and we close it
        # here instead. On an interrupt ``final_response`` is typically
        # empty, so fall back to an explicit placeholder rather than
        # persisting an empty-content assistant turn.
        if interrupted:
            from agent.message_sanitization import close_interrupted_tool_sequence
            close_interrupted_tool_sequence(messages, final_response)

        # Some recovery/fallback paths return a real final_response without
        # adding a closing assistant message to the transcript (e.g. the
        # partial-stream and prior-turn-content recovery ``break`` sites in
        # ``conversation_loop``). If persisted as-is, the durable session can
        # end at a tool/user message even though the caller — and the gateway
        # platform — already saw a completed assistant response. The next turn
        # then replays a user-only backlog and the model re-answers every
        # "unanswered" message. Close the durable turn at the source, at the
        # single chokepoint every recovery ``break`` flows through, so the
        # invariant "delivered final_response ⇒ assistant row in transcript"
        # holds regardless of which path produced it. (#43849 / #44100)
        if final_response and not interrupted:
            try:
                _tail_role = messages[-1].get("role") if messages else None
            except Exception:
                _tail_role = None
            if _tail_role != "assistant":
                messages.append({"role": "assistant", "content": final_response})

        agent._persist_session(messages, conversation_history)
        _patch_completed_turn_working_state(
            agent,
            original_user_message=original_user_message,
            final_response=final_response,
            messages=messages,
            interrupted=interrupted,
            failed=failed,
            continuation_checkpoint=continuation_checkpoint,
        )
    except Exception as _persist_err:
        _cleanup_errors.append(f"persist_session: {_persist_err}")
        logger.error("finalize_turn: _persist_session failed: %s", _persist_err, exc_info=True)

    # ── Turn-exit diagnostic log ─────────────────────────────────────
    # Always logged at INFO so agent.log captures WHY every turn ended.
    # When the last message is a tool result (agent was mid-work), log
    # at WARNING — this is the "just stops" scenario users report.
    _last_msg_role = messages[-1].get("role") if messages else None
    _last_tool_name = None
    if _last_msg_role == "tool":
        # Walk back to find the assistant message with the tool call
        for _m in reversed(messages):
            if _m.get("role") == "assistant" and _m.get("tool_calls"):
                _tcs = _m["tool_calls"]
                if _tcs and isinstance(_tcs[0], dict):
                    _last_tool_name = _tcs[-1].get("function", {}).get("name")
                break

    _turn_tool_count = sum(
        1 for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    _resp_len = len(final_response) if final_response else 0
    _budget_used = agent.iteration_budget.used if agent.iteration_budget else 0
    _budget_max = agent.iteration_budget.max_total if agent.iteration_budget else 0

    _diag_msg = (
        "Turn ended: reason=%s model=%s api_calls=%d/%d budget=%d/%d "
        "tool_turns=%d last_msg_role=%s response_len=%d session=%s"
    )
    _diag_args = (
        _turn_exit_reason, agent.model, api_call_count, agent.max_iterations,
        _budget_used, _budget_max,
        _turn_tool_count, _last_msg_role, _resp_len,
        agent.session_id or "none",
    )

    if _last_msg_role == "tool" and not interrupted:
        # Agent was mid-work — this is the "just stops" case.
        logger.warning(
            "Turn ended with pending tool result (agent may appear stuck). "
            + _diag_msg + " last_tool=%s",
            *_diag_args, _last_tool_name,
        )
    else:
        logger.info(_diag_msg, *_diag_args)

    # File-mutation verifier footer.
    # If one or more ``write_file`` / ``patch`` calls failed during this
    # turn and were never superseded by a successful write to the same
    # path, append an advisory footer to the assistant response.  This
    # catches the specific case — reported by Ben Eng (#15524-adjacent)
    # — where a model issues a batch of parallel patches, half of them
    # fail with "Could not find old_string", and the model summarises
    # the turn claiming every file was edited.  The user then has to
    # manually run ``git status`` to catch the lie.  With this footer
    # the truth is surfaced on every turn, so over-claiming is
    # structurally impossible past the model.
    #
    # Gate: only applied when a real text response exists for this
    # turn and the user didn't interrupt.  Empty/interrupted turns
    # already have other surface text that shouldn't be augmented.
    if final_response and not interrupted:
        try:
            _failed = getattr(agent, "_turn_failed_file_mutations", None) or {}
            if _failed and agent._file_mutation_verifier_enabled():
                footer = agent._format_file_mutation_failure_footer(_failed)
                if footer:
                    final_response = final_response.rstrip() + "\n\n" + footer
        except Exception as _ver_err:
            logger.debug("file-mutation verifier footer failed: %s", _ver_err)

    # Turn-completion explainer.
    # When a turn ends abnormally after substantive work — empty content
    # after retries, a partial/truncated stream, a still-pending tool
    # result, or an iteration/budget limit — the user otherwise gets a
    # blank or fragmentary response box with no consolidated reason why
    # the agent stopped (#34452).  Surface a single user-visible
    # explanation derived from ``_turn_exit_reason``, mirroring the
    # file-mutation verifier footer pattern above.
    #
    # Gate carefully so healthy turns stay quiet:
    #   - ``text_response(...)`` exits never produce an explanation
    #     (handled inside the formatter), so a terse ``Done.`` is silent.
    #   - We only ACT when there is no genuinely usable reply this turn:
    #     an empty response, the "(empty)" terminal sentinel, or a
    #     suspiciously short partial fragment with no terminating
    #     punctuation (e.g. "The").  A real short answer keeps its text.
    if not interrupted:
        try:
            if agent._turn_completion_explainer_enabled():
                _stripped = (final_response or "").strip()
                _is_empty_terminal = _stripped == "" or _stripped == "(empty)"
                # A short fragment that is not a normal text_response exit
                # and lacks sentence-ending punctuation is treated as a
                # truncated partial (the "The" case from #34452).
                _is_partial_fragment = (
                    not _is_empty_terminal
                    and not preserved_verification_fallback
                    and not str(_turn_exit_reason).startswith("text_response")
                    and len(_stripped) <= 24
                    and _stripped[-1:] not in {".", "!", "?", "。", "！", "？", "`", ")"}
                )
                _is_partial_stream_recovery = (
                    str(_turn_exit_reason) == "partial_stream_recovery"
                )
                if (
                    _is_empty_terminal
                    or _is_partial_fragment
                    or _is_partial_stream_recovery
                ):
                    _explanation = agent._format_turn_completion_explanation(
                        _turn_exit_reason
                    )
                    if _explanation:
                        if _is_empty_terminal:
                            # Replace the bare "(empty)"/blank sentinel with
                            # the actionable explanation.
                            final_response = _explanation
                        else:
                            # Keep the partial fragment, append the reason so
                            # the user sees both what arrived and why it
                            # stopped.
                            final_response = (
                                _stripped + "\n\n" + _explanation
                            )
        except Exception as _exp_err:
            logger.debug("turn-completion explainer failed: %s", _exp_err)

    _response_transformed = False

    # Plugin hook: transform_llm_output
    # Fired once per turn after the tool-calling loop completes.
    # Plugins can transform the LLM's output text before it's returned.
    # First hook to return a string wins; None/empty return leaves text unchanged.
    if final_response and not interrupted:
        try:
            from hermes_cli.plugins import invoke_hook as _invoke_hook
            _transform_results = _invoke_hook(
                "transform_llm_output",
                response_text=final_response,
                session_id=agent.session_id or "",
                model=agent.model,
                platform=getattr(agent, "platform", None) or "",
            )
            for _hook_result in _transform_results:
                if isinstance(_hook_result, str) and _hook_result:
                    final_response = _hook_result
                    _response_transformed = True
                    break  # First non-empty string wins
        except Exception as exc:
            logger.warning("transform_llm_output hook failed: %s", exc)

    # Plugin hook: post_llm_call
    # Fired once per turn after the tool-calling loop completes.
    # Plugins can use this to persist conversation data (e.g. sync
    # to an external memory system).
    if final_response and not interrupted:
        try:
            from hermes_cli.plugins import invoke_hook as _invoke_hook
            _invoke_hook(
                "post_llm_call",
                session_id=agent.session_id,
                task_id=effective_task_id,
                turn_id=turn_id,
                user_message=original_user_message,
                assistant_response=final_response,
                conversation_history=list(messages),
                model=agent.model,
                platform=getattr(agent, "platform", None) or "",
            )
        except Exception as exc:
            logger.warning("post_llm_call hook failed: %s", exc)

    # Extract reasoning from the CURRENT turn only.  Walk backwards
    # but stop at the user message that started this turn — anything
    # earlier is from a prior turn and must not leak into the reasoning
    # box (confusing stale display; #17055).  Within the current turn
    # we still want the *most recent* non-empty reasoning: many
    # providers (Claude thinking, DeepSeek v4, Codex Responses) emit
    # reasoning on the tool-call step and leave the final-answer step
    # with reasoning=None, so picking only the last assistant would
    # silently drop legitimate same-turn reasoning.
    last_reasoning = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            break  # turn boundary — don't cross into prior turns
        if msg.get("role") == "assistant" and msg.get("reasoning"):
            last_reasoning = msg["reasoning"]
            break

    # Build result with interrupt info if applicable
    result = {
        "final_response": final_response,
        "last_reasoning": last_reasoning,
        "messages": messages,
        "api_calls": api_call_count,
        "completed": completed,
        "turn_exit_reason": _turn_exit_reason,
        "failed": failed,
        "partial": False,  # True only when stopped due to invalid tool calls
        "interrupted": interrupted,
        "response_transformed": _response_transformed,
        "response_previewed": getattr(agent, "_response_was_previewed", False),
        "model": agent.model,
        "provider": agent.provider,
        "base_url": agent.base_url,
        "input_tokens": agent.session_input_tokens,
        "output_tokens": agent.session_output_tokens,
        "cache_read_tokens": agent.session_cache_read_tokens,
        "cache_write_tokens": agent.session_cache_write_tokens,
        "reasoning_tokens": agent.session_reasoning_tokens,
        "prompt_tokens": agent.session_prompt_tokens,
        "completion_tokens": agent.session_completion_tokens,
        "total_tokens": agent.session_total_tokens,
        "last_prompt_tokens": getattr(agent.context_compressor, "last_prompt_tokens", 0) or 0,
        "estimated_cost_usd": agent.session_estimated_cost_usd,
        "cost_status": agent.session_cost_status,
        "cost_source": agent.session_cost_source,
        "session_id": agent.session_id,
    }
    if agent._tool_guardrail_halt_decision is not None:
        result["guardrail"] = agent._tool_guardrail_halt_decision.to_metadata()
    if continuation_checkpoint is not None:
        result["continuation_checkpoint"] = continuation_checkpoint
    # Surface any post-loop cleanup failures so the caller can distinguish a
    # clean turn from one whose trajectory/session/resource teardown raised
    # (the response is still returned either way — #8049).
    if _cleanup_errors:
        result["cleanup_errors"] = _cleanup_errors
    # If a /steer landed after the final assistant turn (no more tool
    # batches to drain into), hand it back to the caller so it can be
    # delivered as the next user turn instead of being silently lost.
    _leftover_steer = agent._drain_pending_steer()
    if _leftover_steer:
        result["pending_steer"] = _leftover_steer
    agent._response_was_previewed = False

    # Include interrupt message if one triggered the interrupt
    if interrupted and agent._interrupt_message:
        result["interrupt_message"] = agent._interrupt_message

    # Clear interrupt state after handling
    agent.clear_interrupt()

    # Clear stream callback so it doesn't leak into future calls
    agent._stream_callback = None

    # Check skill trigger NOW — based on how many tool iterations THIS turn used.
    _should_review_skills = False
    if (agent._skill_nudge_interval > 0
            and agent._iters_since_skill >= agent._skill_nudge_interval
            and "skill_manage" in agent.valid_tool_names):
        _should_review_skills = True
        agent._iters_since_skill = 0

    # External memory provider: sync the completed turn + queue next prefetch.
    agent._sync_external_memory_for_turn(
        original_user_message=original_user_message,
        final_response=final_response,
        interrupted=interrupted,
        messages=messages,
    )

    # Background memory/skill review — runs AFTER the response is delivered
    # so it never competes with the user's task for model attention.
    if final_response and not interrupted and (_should_review_memory or _should_review_skills):
        try:
            agent._spawn_background_review(
                messages_snapshot=list(messages),
                review_memory=_should_review_memory,
                review_skills=_should_review_skills,
            )
        except Exception:
            pass  # Background review is best-effort

    # Note: Memory provider on_session_end() + shutdown_all() are NOT
    # called here — run_conversation() is called once per user message in
    # multi-turn sessions. Shutting down after every turn would kill the
    # provider before the second message. Actual session-end cleanup is
    # handled by the CLI (atexit / /reset) and gateway (session expiry /
    # _reset_session).

    # Plugin hook: on_session_end
    # Fired at the very end of every run_conversation call.
    # Plugins can use this for cleanup, flushing buffers, etc.
    try:
        from hermes_cli.plugins import invoke_hook as _invoke_hook
        _invoke_hook(
            "on_session_end",
            session_id=agent.session_id,
            task_id=effective_task_id,
            turn_id=turn_id,
            completed=completed,
            interrupted=interrupted,
            model=agent.model,
            platform=getattr(agent, "platform", None) or "",
        )
    except Exception as exc:
        logger.warning("on_session_end hook failed: %s", exc)

    return result
