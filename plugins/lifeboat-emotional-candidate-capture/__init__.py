"""Life-Boat-only candidate capture plugin."""

from .candidate_capture import pre_llm_capture


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", pre_llm_capture)
