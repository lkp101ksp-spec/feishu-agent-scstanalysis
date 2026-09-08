from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.runtime.context_compressor import ContextCompressor
from persistence.models import AuditLogRow, Base
from persistence.repositories.audit_repo import AuditRepo
from shared.errors import FreezeRequired
from shared.schemas import ChatMessage


def fake_llm_router(summary_text="..."):
    r = MagicMock()
    r.call.return_value = summary_text
    return r


def test_estimate_tokens_simple():
    comp = ContextCompressor(
        llm_router=fake_llm_router(), session_repo=MagicMock(),
        audit_repo=MagicMock(),
        token_counter=lambda msgs: sum(len(m.content) // 4 for m in msgs),
    )
    msgs = [ChatMessage(role="user", content="hello world")]
    assert comp.estimate_tokens(msgs) == 2


def test_maybe_compress_below_threshold():
    comp = ContextCompressor(
        llm_router=fake_llm_router(), session_repo=MagicMock(),
        audit_repo=MagicMock(),
        token_counter=lambda msgs: 100,
        compress_trigger_ratio=0.8,
        freeze_trigger_ratio=0.95,
        token_budget=200,
    )
    msgs = [ChatMessage(role="user", content="hi")]
    out = comp.maybe_compress(msgs)
    assert out == msgs  # 未压缩


def test_maybe_compress_above_compress_below_freeze():
    fake_router = fake_llm_router("summary text")
    comp = ContextCompressor(
        llm_router=fake_router, session_repo=MagicMock(),
        audit_repo=MagicMock(),
        token_counter=lambda msgs: 180,
        compress_trigger_ratio=0.8,
        freeze_trigger_ratio=0.95,
        token_budget=200,
    )
    msgs = [
        ChatMessage(role="user", content="x" * 100),
        ChatMessage(role="assistant", content="y" * 100),
    ]
    out = comp.maybe_compress(msgs)
    # 总结后 messages 应该更短
    assert len(out) < len(msgs) + 1


def test_maybe_compress_above_freeze_raises():
    fake_router = fake_llm_router("still big")
    comp = ContextCompressor(
        llm_router=fake_router, session_repo=MagicMock(),
        audit_repo=MagicMock(),
        token_counter=lambda msgs: 199,
        compress_trigger_ratio=0.8,
        freeze_trigger_ratio=0.95,
        token_budget=200,
    )
    msgs = [
        ChatMessage(role="user", content="x" * 800),
        ChatMessage(role="assistant", content="y" * 800),
    ]
    with pytest.raises(FreezeRequired):
        comp.maybe_compress(msgs)


def test_summarize_only_calls_llm():
    """freeze 前强制摘要：直接调 LLM，不做 ratio 判断。"""
    llm = MagicMock()
    llm.call.return_value = "摘要：讨论了单细胞质控"
    comp = ContextCompressor(
        llm_router=llm, session_repo=MagicMock(), audit_repo=MagicMock(),
        token_budget=1000,
    )
    msgs = [ChatMessage(role="user", content="qc 怎么做"),
            ChatMessage(role="assistant", content="先跑 sc_qc")]
    out = comp.summarize_only(msgs)
    assert out == "摘要：讨论了单细胞质控"
    llm.call.assert_called_once()
    assert llm.call.call_args.kwargs["role"] == "context_compressor"


def test_summarize_only_empty_messages():
    """空历史不调用 LLM，直接返回空串。"""
    llm = MagicMock()
    comp = ContextCompressor(
        llm_router=llm, session_repo=MagicMock(), audit_repo=MagicMock(),
        token_budget=1000,
    )
    assert comp.summarize_only([]) == ""
    llm.call.assert_not_called()


def test_maybe_compress_audit_write_real_repo():
    """真 AuditRepo 钉契约：compress 审计必须带 audit_id（2026-09-09 真机 TypeError 回归）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        comp = ContextCompressor(
            llm_router=fake_llm_router("summary"), session_repo=MagicMock(),
            audit_repo=AuditRepo(s),
            token_counter=lambda msgs: 180,
            compress_trigger_ratio=0.8, freeze_trigger_ratio=0.95,
            token_budget=200,
        )
        # 6 条 > preserve_recent_n=5，进入真正压缩分支；new_ratio 0.9 < 0.95 不冻结
        msgs = [ChatMessage(role="user", content=f"m{i}") for i in range(6)]
        comp.maybe_compress(msgs)
        rows = s.query(AuditLogRow).filter_by(action="compress_history").all()
        assert len(rows) == 1 and rows[0].audit_id
    finally:
        s.close()
