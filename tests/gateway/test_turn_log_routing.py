"""The turn log must say which conversation a turn came from.

Life-Boat's Telegram turns were filed into the shared default-profile log with
nothing marking the topic, while everything reading Life-Boat's history looked
in a folder that only the local profile ever wrote to. So every check that
claimed to verify behaviour from the transcript was reading the wrong file.

The hook that writes the log never received the chat or the topic, so it could
not have filed them correctly. This pins that it does now.
"""

from __future__ import annotations

import inspect

import pytest

from gateway.lifeboat_turn_log import (
    LIFEBOAT_LOG_NAME,
    log_folder_for,
    log_folder_read_check,
)


class _Source:
    def __init__(self, platform="telegram", chat_id="", thread_id="", profile=""):
        self.platform = platform
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.profile = profile


def test_the_support_topic_gets_its_own_folder() -> None:
    source = _Source(chat_id="-1004230590253", thread_id="2")

    assert log_folder_for(source, profile="default") == LIFEBOAT_LOG_NAME


def test_another_topic_in_the_same_chat_stays_with_its_profile() -> None:
    source = _Source(chat_id="-1004230590253", thread_id="3")

    assert log_folder_for(source, profile="default") == "default"


def test_another_chat_entirely_stays_with_its_profile() -> None:
    source = _Source(chat_id="602196268", thread_id="")

    assert log_folder_for(source, profile="office-work") == "office-work"


def test_a_non_telegram_turn_stays_with_its_profile() -> None:
    source = _Source(platform="cron", chat_id="-1004230590253", thread_id="2")

    assert log_folder_for(source, profile="default") == "default"


def test_no_source_falls_back_to_the_profile() -> None:
    assert log_folder_for(None, profile="default") == "default"


def test_the_hook_is_given_the_chat_and_the_topic() -> None:
    """Without these the writer cannot tell the topics apart."""
    from agent import turn_finalizer

    body = inspect.getsource(turn_finalizer)
    # The call block, up to its closing paren.
    hook = body.split('"post_llm_call"', 1)[1].split("\n            )", 1)[0]

    assert "chat_id=" in hook
    assert "thread_id=" in hook


# --- the reading direction is one-way ---------------------------------------
#
# Noam, 2026-08-23: "life advisor is a hermes local profile, not the telegram
# bot one. we can make it use the bot's context but not the other way around."
# Written down, that lasts until someone changes it by accident. In code, it
# refuses.

from gateway.lifeboat_turn_log import LOCAL_PROFILE_LOG_NAME, readable_log_folders


def test_the_bot_reads_only_its_own_transcript() -> None:
    folders = readable_log_folders(for_bot=True)

    assert LIFEBOAT_LOG_NAME in folders
    assert LOCAL_PROFILE_LOG_NAME not in folders


def test_the_local_profile_may_read_the_bot() -> None:
    folders = readable_log_folders(for_bot=False)

    assert LIFEBOAT_LOG_NAME in folders
    assert LOCAL_PROFILE_LOG_NAME in folders


def test_the_bot_is_refused_the_local_profiles_folder() -> None:
    with pytest.raises(PermissionError):
        log_folder_read_check(LOCAL_PROFILE_LOG_NAME, for_bot=True)


def test_the_bot_may_read_its_own() -> None:
    assert log_folder_read_check(LIFEBOAT_LOG_NAME, for_bot=True) is True
