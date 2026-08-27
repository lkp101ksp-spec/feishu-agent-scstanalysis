"""E3-E5: for 节点 / ContextCompressor / AST P1P2。"""
from unittest.mock import MagicMock

import pytest

from orchestrator.planner.dag_schema import DAGNode, DAGPlan, validate_dag
from orchestrator.runtime.context_compressor import ContextCompressor
from orchestrator.tools.ast_guard import ASTGuard
from shared.errors import FreezeRequired
from shared.schemas import ChatMessage


def test_e3_for_node_validates():
    body = [
        DAGNode(node_id="f1a", kind="tool", tool_name="read_doc",
                inputs={"doc_id": "file"}, depends_on=["f1"]),
    ]
    f = DAGNode(node_id="f1", kind="for", iterate_over="n1.files",
                body=body, depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[f], entry_node_ids=["f1"])
    validate_dag(plan)


def test_e4_context_compressor_80pct():
    fake_llm = MagicMock()
    fake_llm.call.return_value = "summary"
    comp = ContextCompressor(
        llm_router=fake_llm, session_repo=MagicMock(), audit_repo=MagicMock(),
        token_counter=lambda msgs: 180,
        compress_trigger_ratio=0.8,
        freeze_trigger_ratio=0.95,
        token_budget=200,
        preserve_recent_n=1,
    )
    msgs = [ChatMessage(role="user", content="x" * 800)]
    out = comp.maybe_compress(msgs)
    assert len(out) <= len(msgs)


def test_e4_context_compressor_95pct_freeze():
    fake_llm = MagicMock()
    fake_llm.call.return_value = "summary still big"
    comp = ContextCompressor(
        llm_router=fake_llm, session_repo=MagicMock(), audit_repo=MagicMock(),
        token_counter=lambda msgs: 199,
        compress_trigger_ratio=0.8,
        freeze_trigger_ratio=0.95,
        token_budget=200,
        preserve_recent_n=0,
    )
    msgs = [
        ChatMessage(role="user", content="x" * 800),
        ChatMessage(role="assistant", content="y" * 800),
    ]
    with pytest.raises(FreezeRequired):
        comp.maybe_compress(msgs)


def test_e5_ast_p1_p2_notices():
    guard = ASTGuard()
    r1 = guard.check("import requests\nrequests.get('http://x')")
    assert r1.blocked is False
    assert any(n[0] == "P1" for n in r1.notices)
    r2 = guard.check("open('/etc/passwd')")
    assert r2.blocked is False
    assert any(n[0] == "P2" for n in r2.notices)
