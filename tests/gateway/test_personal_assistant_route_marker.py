from types import SimpleNamespace

import pytest

from gateway.run import GatewayRunner, _is_dedicated_personal_assistant_source
from gateway.session import Platform, SessionSource


def test_office_work_telegram_route_is_personal_assistant() -> None:
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        profile="office-work",
    )

    assert _is_dedicated_personal_assistant_source(source) is True


def test_same_profile_on_other_platform_is_not_personal_assistant() -> None:
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="123",
        profile="office-work",
    )

    assert _is_dedicated_personal_assistant_source(source) is False


def test_unrouted_telegram_chat_is_not_personal_assistant() -> None:
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="123")

    assert _is_dedicated_personal_assistant_source(source) is False


@pytest.mark.asyncio
async def test_telegram_review_commit_uses_office_work_shared_controller() -> None:
    runner = object.__new__(GatewayRunner)
    observed = {}

    class Controller:
        def respond(self, payload):
            observed["payload"] = payload
            return {"receipt": {"id": "saved"}}

    def controller_for(profile):
        observed["profile"] = profile
        return Controller()

    runner._personal_assistant_interview_controller = controller_for
    callback = runner._make_personal_assistant_interview_commit_callback("default")

    result = await callback({
        "profile": "office-work",
        "interviewId": "i1",
        "expectedRevision": 2,
        "taskId": "t1",
        "questionId": "urgency",
        "requestId": "telegram:1",
        "response": {"action": "confirm"},
    })

    assert observed["profile"] == "office-work"
    assert observed["payload"]["interviewId"] == "i1"
    assert "profile" not in observed["payload"]
    assert result["receipt"]["id"] == "saved"


@pytest.mark.asyncio
async def test_telegram_review_commit_rejects_non_personal_assistant_profile() -> None:
    runner = object.__new__(GatewayRunner)
    callback = runner._make_personal_assistant_interview_commit_callback("default")

    with pytest.raises(ValueError, match="office-work"):
        await callback({"profile": "default"})


def test_telegram_adapter_receives_personal_assistant_commit_callback() -> None:
    runner = object.__new__(GatewayRunner)
    adapter = SimpleNamespace(set_hermes_ui_commit_callback=lambda callback: setattr(adapter, "callback", callback))

    runner._configure_personal_assistant_interview_commit(adapter, "office-work")

    assert callable(adapter.callback)
