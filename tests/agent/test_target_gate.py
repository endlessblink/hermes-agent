"""Target gate: confirm the visual target before generating/animating.

Blocks image/video generation on a bare deictic ("animate this") until a target
is confirmed, so the agent asks which frame instead of guessing.
"""

from agent import target_gate
from agent.turn_context import _render_active_target_block


class TestActiveTargetBlock:
    def test_renders_target_with_binding_instruction(self):
        block = _render_active_target_block(
            {"kind": "file", "path": "/frames/jane.png", "label": "Jane frame", "source": "file-browser"}
        )
        assert "<active-target>" in block and "</active-target>" in block
        assert "Jane frame" in block and "/frames/jane.png" in block
        assert "this" in block.lower()

    def test_tool_result_source_adds_confirm_note(self):
        block = _render_active_target_block(
            {"path": "/gen/out.png", "label": "generated", "source": "tool-result"}
        )
        assert "confirm" in block.lower()

    def test_empty_or_malformed_returns_blank(self):
        assert _render_active_target_block(None) == ""
        assert _render_active_target_block({}) == ""
        assert _render_active_target_block("nope") == ""


class TestVisualDeicticDetector:
    def test_bare_deictic_english(self):
        assert target_gate.is_visual_deictic("animate this")
        assert target_gate.is_visual_deictic("try to animate it and see how it looks")
        assert target_gate.is_visual_deictic("regenerate this")
        assert target_gate.is_visual_deictic("animate the frame")

    def test_bare_deictic_hebrew(self):
        assert target_gate.is_visual_deictic("תנפיש את זה")
        assert target_gate.is_visual_deictic("צור את זה")

    def test_named_target_is_not_deictic(self):
        assert not target_gate.is_visual_deictic("animate the Jane frame at 0:05")
        assert not target_gate.is_visual_deictic("animate shot 13")
        assert not target_gate.is_visual_deictic("generate @image:/tmp/jane.png")
        assert not target_gate.is_visual_deictic("animate /home/x/shot13.png")

    def test_attached_image_is_not_deictic(self):
        assert not target_gate.is_visual_deictic("animate this\n@image:/tmp/f.png")
        assert not target_gate.is_visual_deictic("animate this data:image/png;base64,AAAA")

    def test_non_generation_message_not_deictic(self):
        assert not target_gate.is_visual_deictic("what did we do yesterday?")
        assert not target_gate.is_visual_deictic("summarize the scene bible")

    def test_long_instruction_not_deictic(self):
        long = "generate a cinematic 4k render of a red sports car at dusk " + " ".join(
            ["detail"] * 30)
        assert not target_gate.is_visual_deictic(long)


class TestArming:
    def test_arms_on_deictic_without_target(self):
        assert target_gate.should_arm("animate this", has_active_target=False)

    def test_does_not_arm_with_active_target(self):
        assert not target_gate.should_arm("animate this", has_active_target=True)

    def test_does_not_arm_on_named_target(self):
        assert not target_gate.should_arm("animate shot 13", has_active_target=False)


class TestEvaluate:
    def test_blocks_generation_tools(self):
        for t in ("image_generate", "video_generate", "xai_video_edit", "xai_video_extend"):
            assert target_gate.evaluate(t, {}) is not None

    def test_blocks_magnific_creation_upload(self):
        assert target_gate.evaluate("magnific__creations_request_upload", {}) is not None
        assert target_gate.evaluate("magnific__creations_finalize_upload", {}) is not None

    def test_allows_readonly_magnific(self):
        assert target_gate.evaluate("magnific__account_balance", {}) is None
        assert target_gate.evaluate("magnific__creations_get", {}) is None
        assert target_gate.evaluate("magnific__video_models_list", {}) is None

    def test_allows_non_generation_tools(self):
        for t in ("read_file", "clarify", "search_files", "terminal"):
            assert target_gate.evaluate(t, {}) is None


class TestGateBlockMessage:
    class _Agent:
        def __init__(self, armed, satisfied):
            self._target_gate_armed = armed
            self._target_gate_satisfied = satisfied

    def test_blocks_when_armed_unsatisfied(self):
        a = self._Agent(armed=True, satisfied=False)
        assert target_gate.gate_block_message(a, "image_generate", {}) is not None

    def test_allows_when_not_armed(self):
        a = self._Agent(armed=False, satisfied=False)
        assert target_gate.gate_block_message(a, "image_generate", {}) is None

    def test_allows_when_satisfied(self):
        a = self._Agent(armed=True, satisfied=True)
        assert target_gate.gate_block_message(a, "image_generate", {}) is None

    def test_fail_open_on_bad_agent(self):
        assert target_gate.gate_block_message(object(), "image_generate", {}) is None
