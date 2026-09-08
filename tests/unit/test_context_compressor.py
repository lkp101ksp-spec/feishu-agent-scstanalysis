from unittest.mock import MagicMock

import pytest

from orchestrator.runtime.context_compressor import ContextCompressor
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
