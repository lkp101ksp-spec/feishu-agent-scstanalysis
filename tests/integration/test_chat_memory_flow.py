"""长会话记忆：process() 接线集成测试（真 sqlite + mock LLM/适配层）。"""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.app import Orchestrator
from orchestrator.bind_doc_service import BindDocService
from orchestrator.doc_write_service import DocWriteService
from orchestrator.session_service import SessionService
from orchestrator.task_service import TaskService
from persistence.models import Base
from persistence.repositories.audit_repo import AuditRepo
from persistence.repositories.doc_write_repo import DocWriteRepo
from persistence.repositories.session_repo import SessionRepo
from persistence.repositories.task_repo import TaskRepo
from shared.schemas import ChatMessage, IncomingMessage


@pytest.fixture
def orch():
    """带内存 SQLite + mock LLM/IM 的 Orchestrator（test_message_flow 同款构造）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()

    session_repo = SessionRepo(s)
    session_svc = SessionService(session_repo)
    task_svc = TaskService(TaskRepo(s), AuditRepo(s))
    bind_svc = BindDocService(session_svc, AuditRepo(s), ttl_sec=1800,
                              session_repo=session_repo)
    doc_adapter = MagicMock()
    doc_adapter.append_plain_text.return_value = "blk_x"
    doc_write_svc = DocWriteService(session_repo, DocWriteRepo(s), doc_adapter)

    llm_router = MagicMock()
    llm_router.chat.return_value = "你好，我是 AI 助手。"
    im_adapter = MagicMock()
    im_adapter.reply.return_value = "om_reply"

    o = Orchestrator(
        llm_router=llm_router, session_service=session_svc,
        task_service=task_svc, bind_doc_service=bind_svc,
        doc_write_service=doc_write_svc, im_adapter=im_adapter,
    )
    yield o, llm_router, im_adapter
    s.close()


def _incoming(text, mid="om_1"):
    return IncomingMessage(message_id=mid, chat_id="oc_1",
                           sender_open_id="ou_1", text=text, chat_type="p2p")


def test_process_injects_history_and_appends_turn(orch):
    """装配 chat_memory 后：LLM 收到 system+历史+当前；回复后双写落库。"""
    o, llm_router, im_adapter = orch
    memory = MagicMock()
    memory.prepare.return_value = (
        [ChatMessage(role="user", content="前一轮"),
         ChatMessage(role="assistant", content="前一答")],
        "sid_x", False,
    )
    o.chat_memory = memory
    result = o.process(_incoming("刚才说了什么"))
    assert result["status"] == "success"
    sent = llm_router.chat.call_args.args[0]
    assert [m.role for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1].content == "前一轮" and sent[-1].content == "刚才说了什么"
    memory.append_turn.assert_called_once_with(
        result["session_id"], "刚才说了什么", "你好，我是 AI 助手。")


def test_process_freeze_continues_with_new_session(orch):
    """freeze 后：返回/bound_doc 查询用新 session_id，文档链路不受影响。"""
    o, llm_router, im_adapter = orch
    memory = MagicMock()
    memory.prepare.return_value = (
        [ChatMessage(role="system", content="[已压缩] 摘要")], "sid_new", True,
    )
    o.chat_memory = memory
    result = o.process(_incoming("继续"))
    assert result["status"] == "success"
    assert result["session_id"] == "sid_new"
    sent = llm_router.chat.call_args.args[0]
    assert sent[1].content == "[已压缩] 摘要"
    memory.append_turn.assert_called_once_with(
        "sid_new", "继续", "你好，我是 AI 助手。")


def test_process_without_memory_unchanged(orch):
    """未装配 chat_memory（旧构造）：无记忆两消息行为不变（回归护栏）。"""
    o, llm_router, im_adapter = orch
    result = o.process(_incoming("hello"))
    assert result["status"] == "success"
    sent = llm_router.chat.call_args.args[0]
    assert len(sent) == 2 and sent[-1].content == "hello"


def test_clear_command(orch):
    """/clear：走 chat_memory.clear，回复提示，不进 LLM。"""
    o, llm_router, im_adapter = orch
    memory = MagicMock()
    memory.clear.return_value = "sid_new"
    o.chat_memory = memory
    result = o.process(_incoming("/clear"))
    assert result["status"] == "cleared"
    memory.clear.assert_called_once()
    llm_router.chat.assert_not_called()
    assert "新会话" in im_adapter.reply.call_args.args[1]
