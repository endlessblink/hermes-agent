from types import SimpleNamespace


def test_personal_assistant_stops_after_first_silent_timeout():
    from agent.conversation_loop import _stop_retrying_silent_timeout

    agent = SimpleNamespace(_single_attempt_silent_timeout=True)
    error = TimeoutError(
        "Non-streaming API call timed out after 60s with no response "
        "(threshold: 60s)"
    )

    assert _stop_retrying_silent_timeout(agent, error) is True


def test_normal_transient_errors_keep_existing_retry_policy():
    from agent.conversation_loop import _stop_retrying_silent_timeout

    agent = SimpleNamespace(_single_attempt_silent_timeout=True)

    assert _stop_retrying_silent_timeout(agent, TimeoutError("read timed out")) is False
    assert _stop_retrying_silent_timeout(agent, ConnectionError("connection reset")) is False


def test_policy_is_scoped_to_latency_sensitive_agents():
    from agent.conversation_loop import _stop_retrying_silent_timeout

    agent = SimpleNamespace(_single_attempt_silent_timeout=False)
    error = TimeoutError("API call timed out after 60s with no response")

    assert _stop_retrying_silent_timeout(agent, error) is False
