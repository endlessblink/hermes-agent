"""Lane resolution for vague continuations after compression recovery.

Regression fixture: profile ``office-work``, session ``20260709_142246_1c06cf``,
messages 5936-5957 (2026-07-09). Compression recovery created a fresh session
with ``cwd='/home/endlessblink'`` and NULL ``git_repo_root``. The user's vague
prompt ("קודקס סיים, אתה יכול לבדוק?") was replayed into it. With no structured
lane, the model reconstructed one from ``glob.glob('/home/endlessblink/*')`` --
which cannot see dotdirs -- got exactly one repo back, and inspected
``html-video`` instead of ``.hermes/hermes-agent``.

The repo path was present in the recovery preamble the whole time, as prose.
"""

import pytest

from agent.lane_resolver import echoes_recovery_reason, render_lane_block, resolve_lane


HOME = "/home/endlessblink"
REPO = "/home/endlessblink/.hermes/hermes-agent"
PROMPT_FILE = "/tmp/codex-flowstate-primitives-prompt.md"


def _history():
    """The parent session's tail, as ``_build_dropoff_seed`` sees it."""
    return [
        {"role": "user", "content": "אני משתמש בקודקס חיצונית. אתה יכול להתחבר אליו?"},
        {
            "role": "assistant",
            "content": (
                "מעולה. הרצתי שוב את Codex מכאן.\n\nתהליך רץ:\n`proc_c96b639969e3`\n\n"
                "הפקודה:\n\n```bash\ncodex exec --cd /home/endlessblink/.hermes/hermes-agent "
                f"--sandbox workspace-write - < {PROMPT_FILE}\n```"
            ),
        },
        {
            "role": "user",
            "content": (
                "[IMPORTANT: Background process proc_c96b639969e3 exited (exit code 1).\n"
                "Command: codex exec --cd /home/endlessblink/.hermes/hermes-agent "
                f"--sandbox workspace-write - < {PROMPT_FILE}\n"
                "Output:\n 401 Unauthorized, url: wss://api.openai.com/v1/responses"
            ),
        },
    ]


class TestTranscriptEvidence:
    def test_recovers_repo_from_exited_codex_job(self):
        """The job had already exited, so the live process registry was empty.

        The launch command survives in the transcript. That is the durable
        record, and it is the one the failing turn ignored.
        """
        lane = resolve_lane(_history(), session_cwd=HOME)
        assert lane is not None
        assert lane.repo_path == REPO
        assert lane.prompt_file == PROMPT_FILE
        assert lane.source == "transcript"

    def test_home_dir_cwd_is_never_a_lane(self):
        """`cwd='/home/endlessblink'` is the recovery fallback, not a workspace."""
        lane = resolve_lane([], session_cwd=HOME)
        assert lane is None

    def test_transcript_beats_session_cwd(self):
        """Even a real repo cwd loses to an explicit agent launch command."""
        lane = resolve_lane(_history(), session_cwd="/home/endlessblink/html-video")
        assert lane.repo_path == REPO

    def test_most_recent_launch_wins(self):
        history = _history() + [
            {"role": "assistant", "content": "```bash\ncodex exec --cd /home/endlessblink/other-repo -\n```"}
        ]
        assert resolve_lane(history, session_cwd=HOME).repo_path == "/home/endlessblink/other-repo"

    @pytest.mark.parametrize(
        "command,expected",
        [
            ("codex exec --cd /srv/proj -", "/srv/proj"),
            ("codex exec --cd '/srv/my proj' -", "/srv/my proj"),
            ("claude --add-dir /srv/proj", "/srv/proj"),
            ("opencode run -C /srv/proj", "/srv/proj"),
            ("codex exec --cwd /srv/proj", "/srv/proj"),
        ],
    )
    def test_agent_launch_forms(self, command, expected):
        lane = resolve_lane([{"role": "assistant", "content": command}], session_cwd=HOME)
        assert lane is not None and lane.repo_path == expected

    def test_bare_cd_flag_without_agent_binary_is_ignored(self):
        """`--cd` alone is not evidence; some other tool may use the flag."""
        history = [{"role": "assistant", "content": "rsync --cd /srv/backup"}]
        assert resolve_lane(history, session_cwd=HOME) is None


class TestProcessRegistry:
    def test_live_job_outranks_transcript(self):
        procs = [{"session_key": "proc_x", "cwd": "/srv/live", "command": "codex exec -"}]
        lane = resolve_lane(_history(), session_cwd=HOME, process_sessions=procs)
        assert lane.repo_path == "/srv/live"
        assert lane.source == "process-registry"

    def test_non_agent_process_is_not_a_lane(self):
        procs = [{"session_key": "p", "cwd": "/srv/live", "command": "npm run dev"}]
        lane = resolve_lane([], session_cwd=HOME, process_sessions=procs)
        assert lane is None


class TestSessionCwd:
    def test_real_repo_cwd_is_a_weak_lane(self):
        lane = resolve_lane([], session_cwd="/srv/proj", is_repo=lambda p: p == "/srv/proj")
        assert lane is not None
        assert lane.source == "session-cwd"
        assert lane.confidence == "low"

    def test_non_repo_cwd_is_not_a_lane(self):
        assert resolve_lane([], session_cwd="/srv/notarepo", is_repo=lambda p: False) is None


class TestConfidence:
    def test_single_agent_launch_is_high(self):
        assert resolve_lane(_history(), session_cwd=HOME).confidence == "high"

    def test_two_competing_repos_is_ambiguous(self):
        """Two different agent launches, same tier -> the model must ask, not guess."""
        history = [
            {"role": "assistant", "content": "codex exec --cd /srv/alpha -"},
            {"role": "assistant", "content": "claude --add-dir /srv/beta"},
        ]
        lane = resolve_lane(history, session_cwd=HOME)
        assert lane.confidence == "ambiguous"
        every = {lane.repo_path} | {alt.repo_path for alt in lane.alternatives}
        assert every == {"/srv/alpha", "/srv/beta"}


class TestRenderedBlock:
    def test_block_names_repo_and_prompt_file(self):
        block = render_lane_block(resolve_lane(_history(), session_cwd=HOME))
        assert "<active-lane" in block and "</active-lane>" in block
        assert REPO in block
        assert PROMPT_FILE in block
        assert 'confidence="high"' in block

    def test_no_lane_emits_an_explicit_warning_not_silence(self):
        """The load-bearing case.

        With no lane, the model must be told *not* to reconstruct one from a
        filesystem probe. Silence is what let `glob` win.
        """
        block = render_lane_block(None)
        assert "<active-lane" in block
        assert 'confidence="none"' in block
        assert "do not infer" in block.lower()
        assert "ask" in block.lower()

    def test_ambiguous_block_lists_every_candidate(self):
        history = [
            {"role": "assistant", "content": "codex exec --cd /srv/alpha -"},
            {"role": "assistant", "content": "claude --add-dir /srv/beta"},
        ]
        block = render_lane_block(resolve_lane(history, session_cwd=HOME))
        assert 'confidence="ambiguous"' in block
        assert "/srv/alpha" in block and "/srv/beta" in block

    def test_block_carries_no_secrets(self):
        """The preamble already leaks 401 text; the lane block must not add to it."""
        block = render_lane_block(resolve_lane(_history(), session_cwd=HOME))
        assert "401" not in block
        assert "Unauthorized" not in block

    def test_external_job_keeps_the_subcommand(self):
        """`codex --cd ...` is not a runnable command; `codex exec --cd ...` is."""
        lane = resolve_lane(_history(), session_cwd=HOME)
        assert lane.external_job.startswith("codex exec --cd ")

    @pytest.mark.parametrize(
        "command",
        [
            "codex exec --cd /srv/proj --token sk-secret123 payload.txt",
            "codex exec --cd /srv/proj --api-key=sk-secret123",
        ],
    )
    def test_external_job_drops_operands_and_secret_values(self, command):
        """A lane record is not worth leaking a token into a system message."""
        job = resolve_lane([{"role": "assistant", "content": command}], session_cwd=HOME).external_job
        assert "/srv/proj" in job
        assert "sk-secret123" not in job
        assert "payload.txt" not in job


class TestContinuityEcho:
    REASON = "Context compression did not finish within 12s. Continuing in a fresh session."

    def test_dropoff_record_echoing_the_reason_is_suppressed(self):
        """The three `[Context] [compression] [did] [not]` hits from msg 5936."""
        snippet = (
            "[Context] [compression] [did] [not] [finish] [within] [12s]. "
            "[Continuing] [in] [a] [fresh] [session]."
        )
        assert echoes_recovery_reason(snippet, self.REASON) is True

    def test_real_work_context_survives(self):
        snippet = "The FlowState planning surface needs a flowstate-planning-session artifact type."
        assert echoes_recovery_reason(snippet, self.REASON) is False

    def test_no_reason_suppresses_nothing(self):
        assert echoes_recovery_reason("anything at all", "") is False

    def test_empty_snippet_is_suppressed(self):
        assert echoes_recovery_reason("", self.REASON) is True
