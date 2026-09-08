"""长会话记忆：ChatMemory 编排单测（mock 边界，6 用例）。"""
from unittest.mock import MagicMock

from shared.errors import FreezeRequired
from shared.schemas import ChatMessage

from orchestrator.chat_memory import ChatMemory


def _row(role, content):
    """模拟 MessageRow（只取 role/content 两个属性）。"""
    r = MagicMock()
    r.role, r.content = role, content
    return r


def _memory(*, rows=(), compress_out=None, freeze=False, summary="摘要"):
    """装配 mock 边界的 ChatMemory；freeze=True 时 compressor 抛 FreezeRequired。"""
    repo = MagicMock()
    repo.list_all.return_value = list(rows)
    comp = MagicMock()
    if freeze:
        comp.maybe_compress.side_effect = FreezeRequired("ratio 0.97 >= freeze 0.95")
        comp.summarize_only.return_value = summary
        comp.estimate_tokens.return_value = 194
        comp.token_budget = 200
    elif compress_out is not None:
        comp.maybe_compress.return_value = compress_out
    else:
        # 无压缩路径必须保持入参同一性（真实 maybe_compress 原样 return messages）
        comp.maybe_compress.side_effect = lambda m: m
    svc = MagicMock()
    svc.freeze_session.return_value = "new_sid"
    im = MagicMock()
    return ChatMemory(message_repo=repo, compressor=comp,
                      session_service=svc, im=im), repo, comp, svc, im


def test_prepare_normal_passthrough():
    """无压缩：历史原样透传，会话不变、不回写。"""
    mem, repo, comp, svc, im = _memory(rows=[_row("user", "hi"), _row("assistant", "你好")])
    history, sid, frozen = mem.prepare("s1", "c1")
    assert [(m.role, m.content) for m in history] == [("user", "hi"), ("assistant", "你好")]
    assert sid == "s1" and frozen is False
    repo.replace_all.assert_not_called()


def test_prepare_compress_writes_back():
    """发生压缩（返回新列表）：replace_all 回写压缩态，返回压缩后历史。"""
    rows = [_row("user", f"m{i}") for i in range(8)]
    compressed = [ChatMessage(role="system", content="[已压缩] 前文摘要"),
                  ChatMessage(role="user", content="m7")]
    mem, repo, comp, svc, im = _memory(rows=rows, compress_out=compressed)
    history, sid, frozen = mem.prepare("s1", "c1")
    assert history == compressed and frozen is False
    repo.replace_all.assert_called_once_with(
        "s1", [("system", "[已压缩] 前文摘要"), ("user", "m7")])


def test_prepare_freeze_full_orchestration():
    """冻结全链路：摘要 → freeze_session → 新会话播摘要行 → 通知 → 返回新会话。"""
    rows = [_row("user", "m1"), _row("assistant", "r1")]
    mem, repo, comp, svc, im = _memory(rows=rows, freeze=True, summary="讨论过质控")
    history, sid, frozen = mem.prepare("s1", "c1")
    assert sid == "new_sid" and frozen is True
    svc.freeze_session.assert_called_once()
    kw = svc.freeze_session.call_args.kwargs
    assert kw["session_id"] == "s1" and kw["summary"] == "讨论过质控"
    assert abs(kw["trigger_ratio"] - 0.97) < 1e-6
    repo.append.assert_called_once_with("new_sid", "system", "[已压缩] 讨论过质控")
    im.reply.assert_called_once()
    assert "新会话" in im.reply.call_args.args[1]
    assert [(m.role, m.content) for m in history] == [("system", "[已压缩] 讨论过质控")]


def test_prepare_freeze_summary_failure_degrades():
    """摘要 LLM 失败：空摘要冻结，不播摘要行，仍正常开新会话。"""
    mem, repo, comp, svc, im = _memory(rows=[_row("user", "m1")], freeze=True)
    comp.summarize_only.side_effect = RuntimeError("llm down")
    history, sid, frozen = mem.prepare("s1", "c1")
    assert sid == "new_sid" and frozen is True and history == []
    assert svc.freeze_session.call_args.kwargs["summary"] == ""
    repo.append.assert_not_called()


def test_prepare_load_failure_degrades_to_no_memory():
    """DB 读失败：降级为空历史，会话不变，不抛异常。"""
    mem, repo, comp, svc, im = _memory()
    repo.list_all.side_effect = RuntimeError("db down")
    history, sid, frozen = mem.prepare("s1", "c1")
    assert (history, sid, frozen) == ([], "s1", False)
    comp.maybe_compress.assert_not_called()


def test_append_turn_and_clear():
    """append_turn 双写 user/assistant；clear 走 freeze_session(summary 空, ratio 0)。"""
    mem, repo, comp, svc, im = _memory()
    mem.append_turn("s1", "你好", "你好！")
    assert repo.append.call_args_list[0].args == ("s1", "user", "你好")
    assert repo.append.call_args_list[1].args == ("s1", "assistant", "你好！")
    new_sid = mem.clear("s1")
    assert new_sid == "new_sid"
    kw = svc.freeze_session.call_args.kwargs
    assert kw == {"session_id": "s1", "summary": "", "trigger_ratio": 0.0}
