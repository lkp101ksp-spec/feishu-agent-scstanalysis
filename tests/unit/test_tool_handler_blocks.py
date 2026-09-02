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


def test_tool_handler_blocks_parse_failure_degrades():
    """blocks 解析失败降级告警，不炸工具（真机 2026-08-30 read_doc 撞名事故）。"""
    reg = MagicMock()
    reg.get.return_value = ToolSpec(
        name="x", description="d", parameters={"type": "object"},
        risk_level="L0_read",
        # 原始 docx 块树：无 type 键，富文本解析必炸
        handler=lambda: {
            "data": "y",
            "blocks": [{"block_type": 2, "text": {"elements": []}}],
        },
    )
    handler = ToolHandler(registry=reg)
    result = handler.execute("x", {}, actor_open_id="ou_1", session_id="s1")
    assert result.error_code is None  # 工具本体不失败
    assert result.outputs == {"data": "y"}
    assert result.blocks is None


def test_tool_handler_coerces_repr_string_params(caplog):
    """Phase 24：repr 串参数在 execute() 被 schema 驱动纠正 + warning 日志。"""
    import logging

    seen = {}

    def handler(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    reg = MagicMock()
    reg.get.return_value = ToolSpec(
        name="x", description="d",
        parameters={"type": "object",
                    "properties": {"genes": {"type": "array"}}},
        risk_level="L0_read",
        handler=handler,
    )
    th = ToolHandler(registry=reg)
    with caplog.at_level(logging.WARNING):
        result = th.execute("x", {"genes": "['A', 'B']"},
                            actor_open_id="ou_1", session_id="s1")
    assert result.error_code is None
    assert seen["genes"] == ["A", "B"]
    assert "coerced from repr-string" in caplog.text
