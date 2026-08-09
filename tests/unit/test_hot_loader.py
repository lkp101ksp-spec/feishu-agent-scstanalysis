import os
import tempfile

import pytest

from orchestrator.tools.ast_guard import ASTGuard
from orchestrator.tools.hot_loader import HotLoader
from orchestrator.tools.tool_registry import ToolRegistry
from shared.errors import ToolBlockedError


def _make_loader():
    from unittest.mock import MagicMock
    reg = ToolRegistry()
    audit = MagicMock()
    loader = HotLoader(
        tool_registry=reg, audit_repo=audit, ast_guard=ASTGuard(),
        temp_dir=tempfile.mkdtemp(),
    )
    return loader, reg, audit


def test_hot_loader_registers_safe_tool():
    loader, reg, _ = _make_loader()
    code = "def handle(seq): return {'rc': seq[::-1]}"
    name = loader.upload(
        name="reverse_complement", code=code,
        parameters={"type": "object", "properties": {"seq": {"type": "string"}}},
        risk_level="L0_read", actor_open_id="admin_1",
    )
    assert name == "reverse_complement"
    spec = reg.get("reverse_complement")
    assert spec is not None


def test_hot_loader_blocks_eval():
    loader, _, _ = _make_loader()
    code = "def handle(query): return eval(query)"
    with pytest.raises(ToolBlockedError):
        loader.upload(
            name="bad_tool", code=code,
            parameters={"type": "object"},
            risk_level="L0_read", actor_open_id="admin_1",
        )


def test_hot_loader_blocks_exec():
    loader, _, _ = _make_loader()
    code = "def handle(): exec('print(1)')"
    with pytest.raises(ToolBlockedError):
        loader.upload(
            name="bad_tool2", code=code,
            parameters={"type": "object"},
            risk_level="L0_read", actor_open_id="admin_1",
        )


def test_hot_loader_requires_handle_function():
    loader, _, _ = _make_loader()
    code = "x = 1\n"
    with pytest.raises(ValueError):
        loader.upload(
            name="no_handle", code=code,
            parameters={"type": "object"},
            risk_level="L0_read", actor_open_id="admin_1",
        )


def test_hot_loader_size_limit():
    loader, _, _ = _make_loader()
    big_code = "x = 1\n" * 20000  # ~ 120KB
    with pytest.raises(ValueError):
        loader.upload(
            name="big", code=big_code,
            parameters={"type": "object"},
            risk_level="L0_read", actor_open_id="admin_1",
        )