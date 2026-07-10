"""The recovery preamble must carry the work lane as structure, not as prose.

Regression for profile ``office-work``, session ``20260709_142246_1c06cf``
(2026-07-09 14:24). Compression recovery built a preamble that contained
``codex exec --cd /home/endlessblink/.hermes/hermes-agent`` twice -- buried in a
1000-char clip of Hebrew chat log -- reported ``Workspace: /home/endlessblink``,
and then replayed the user's vague prompt into it. The model reconstructed the
workspace with ``glob.glob('/home/endlessblink/*')``, which cannot see dotdirs,
got back exactly one repository, and went to work in the wrong one.
"""

import threading

import pytest

from tui_gateway.server import _build_dropoff_seed


REPO = "/home/endlessblink/.hermes/hermes-agent"
PROMPT_FILE = "/tmp/codex-flowstate-primitives-prompt.md"
REASON = "Context compression did not finish within 12s. Continuing in a fresh session."


@pytest.fixture
def parent_session():
    """The parent session as recovery saw it: home-dir cwd, Codex job in history."""
    return {
        "session_key": "20260708_180705_123bf5",
        "cwd": "/home/endlessblink",  # the fallback, not a workspace
        "history_lock": threading.Lock(),
        "history": [
            {
                "role": "assistant",
                "content": (
                    "מעולה. הרצתי שוב את Codex מכאן.\n\nתהליך רץ: `proc_c96b639969e3`\n\n"
                    f"```bash\ncodex exec --cd {REPO} --sandbox workspace-write - < {PROMPT_FILE}\n```"
                ),
            },
            {
                "role": "user",
                "content": (
                    "[IMPORTANT: Background process proc_c96b639969e3 exited (exit code 1).\n"
                    f"Command: codex exec --cd {REPO} --sandbox workspace-write - < {PROMPT_FILE}"
                ),
            },
        ],
    }


def _seed_text(session, **kwargs):
    seed = _build_dropoff_seed(session, error_message=REASON, **kwargs)
    assert len(seed) == 1 and seed[0]["role"] == "system"
    return seed[0]["content"]


class TestLaneCarryOver:
    def test_preamble_names_the_real_repo_as_structure(self, parent_session):
        text = _seed_text(parent_session, pending_prompt="קודקס סיים, אתה יכול לבדוק?")
        assert "<active-lane" in text
        lane_block = text.split("<active-lane")[1].split("</active-lane>")[0]
        assert REPO in lane_block
        assert PROMPT_FILE in lane_block

    def test_lane_appears_before_the_transcript_prose(self, parent_session):
        text = _seed_text(parent_session)
        assert text.index("<active-lane") < text.index("Recent transcript context:")

    def test_home_cwd_alone_yields_an_explicit_no_lane_warning(self):
        """No agent launch in history: the model must be told to ask, not probe."""
        bare = {
            "session_key": "s",
            "cwd": "/home/endlessblink",
            "history_lock": threading.Lock(),
            "history": [{"role": "user", "content": "שלום"}],
        }
        text = _seed_text(bare)
        assert 'confidence="none"' in text
        assert "do not infer" in text.lower()


class TestContinuityRecall:
    def test_self_referential_dropoff_hits_are_dropped(self, parent_session):
        """Recovery once recalled its own error message, three times."""
        hits = [
            {
                "id": f"dropoff-17835158341{i}",
                "snippet": "[Context] [compression] [did] [not] [finish] [within] [12s].",
            }
            for i in range(3)
        ]
        text = _seed_text(parent_session, recall_hits=hits)
        assert "Retrieved continuity context:" not in text

    def test_genuine_continuity_hits_survive(self, parent_session):
        hits = [{"id": "n-1", "snippet": "FlowState owns tasks; Hermes renders the planning surface."}]
        text = _seed_text(parent_session, recall_hits=hits)
        assert "Retrieved continuity context:" in text
        assert "FlowState owns tasks" in text
