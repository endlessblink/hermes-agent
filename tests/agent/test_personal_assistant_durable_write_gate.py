from agent.personal_assistant_durable_write_gate import (
    durable_personal_assistant_write_gate_message,
)


PROTECTED_NOTE = (
    "/vault/MAIN VULT/_System/Hermes Knowledge Graph/"
    "Office Work Personal Assistant.md"
)


def test_blocks_direct_replace_of_personal_assistant_knowledge_note() -> None:
    message = durable_personal_assistant_write_gate_message(
        "patch",
        {
            "mode": "replace",
            "path": PROTECTED_NOTE,
            "old_string": "source_version: 9",
            "new_string": "source_version: 10",
        },
    )

    assert message is not None
    assert "personal_assistant_propose_capture" in message
    assert "approval" in message.lower()


def test_blocks_protected_note_hidden_inside_multifile_patch() -> None:
    message = durable_personal_assistant_write_gate_message(
        "patch",
        {
            "mode": "patch",
            "patch": (
                "*** Begin Patch\n"
                f"*** Update File: {PROTECTED_NOTE}\n"
                "@@\n-old\n+new\n"
                "*** End Patch"
            ),
        },
    )

    assert message is not None


def test_blocks_direct_write_but_allows_reads_and_unrelated_files() -> None:
    assert (
        durable_personal_assistant_write_gate_message(
            "write_file", {"path": PROTECTED_NOTE, "content": "replacement"}
        )
        is not None
    )
    assert (
        durable_personal_assistant_write_gate_message(
            "read_file", {"path": PROTECTED_NOTE}
        )
        is None
    )
    assert (
        durable_personal_assistant_write_gate_message(
            "patch", {"mode": "replace", "path": "/workspace/notes.md"}
        )
        is None
    )


def test_blocks_move_or_delete_targeting_the_protected_note() -> None:
    assert (
        durable_personal_assistant_write_gate_message(
            "move_file", {"src": PROTECTED_NOTE, "dst": "/tmp/moved.md"}
        )
        is not None
    )
    assert (
        durable_personal_assistant_write_gate_message(
            "delete_file", {"path": PROTECTED_NOTE}
        )
        is not None
    )


def test_lifeboat_blocks_any_direct_obsidian_write_until_user_approves() -> None:
    message = durable_personal_assistant_write_gate_message(
        "patch",
        {
            "mode": "replace",
            "path": "/vault/MAIN VULT/_System/Hermes Knowledge Graph/Projects/Daily Evidence Journal/2026-08-10.md",
        },
        lifeboat_mode=True,
    )
    assert message is not None
    assert "exact short summary" in message
    assert "explicit approval" in message
