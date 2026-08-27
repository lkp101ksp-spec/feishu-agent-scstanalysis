from unittest.mock import MagicMock

from orchestrator.tools.tool_handler import ToolHandler
from orchestrator.tools.tool_registry import ToolSpec


def test_tool_handler_extracts_blocks_field():
    reg = MagicMock()
    reg.get.return_value = ToolSpec(
        name="x", description="d", parameters={"type": "object"},
        risk_level="L0_read",
        handler=lambda: {
            "data": "y",
            "blocks": [{"type": "text", "text": "hi"}],
        },
    )
    handler = ToolHandler(registry=reg)
    result = handler.execute("x", {}, actor_open_id="ou_1", session_id="s1")
    assert result.outputs == {"data": "y"}
    assert result.blocks is not None
    assert len(result.blocks) == 1
    assert result.blocks[0].text == "hi"


def test_tool_handler_blocks_none_when_not_provided():
    reg = MagicMock()
    reg.get.return_value = ToolSpec(
        name="x", description="d", parameters={"type": "object"},
        risk_level="L0_read",
        handler=lambda: {"data": "y"},
    )
    handler = ToolHandler(registry=reg)
    result = handler.execute("x", {}, actor_open_id="ou_1", session_id="s1")
    assert result.outputs == {"data": "y"}
    assert result.blocks is None
