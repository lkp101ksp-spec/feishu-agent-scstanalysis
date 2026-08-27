"""Orchestrator.process() 端到端测试：mock LLM + mock lark-cli + 内存 SQLite DB。"""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
from shared.errors import LLMCallError
from shared.schemas import IncomingMessage


@pytest.fixture
def orch():
    """构建一个带内存 SQLite + mock LLM + mock 适配层的 Orchestrator。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()

    session_repo = SessionRepo(s)
    task_repo = TaskRepo(s)
    doc_repo = DocWriteRepo(s)
    audit_repo = AuditRepo(s)

    session_svc = SessionService(session_repo)
    task_svc = TaskService(task_repo, audit_repo)
    bind_svc = BindDocService(session_svc, audit_repo, ttl_sec=1800)

    doc_adapter = MagicMock()
    doc_adapter.append_plain_text.return_value = "blk_x"
    doc_write_svc = DocWriteService(session_repo, doc_repo, doc_adapter)

    llm_router = MagicMock()
    llm_router.chat.return_value = "你好，我是 AI 助手。"

    im_adapter = MagicMock()
    im_adapter.reply.return_value = "om_reply"

    orch = Orchestrator(
        llm_router=llm_router,
        session_service=session_svc,
        task_service=task_svc,
        bind_doc_service=bind_svc,
        doc_write_service=doc_write_svc,
        im_adapter=im_adapter,
    )
    # 注入测试 session，让 process() 可访问同一 session
    orch._test_session = s
    yield orch
    s.close()


def test_process_message_without_bind_only_replies_im(orch):
    incoming = IncomingMessage(
        message_id="om_1", chat_id="oc_1", sender_open_id="ou_1", text="hello"
    )
    result = orch.process(incoming)
    orch._test_session.commit()

    assert result["status"] == "success"
    assert result["doc_written"] is False
    assert result["reply_text"] == "你好，我是 AI 助手。"
    # doc_written=False 已隐含验证未调用 doc_write_service
    orch.im.reply.assert_called_once()
    reply_text = orch.im.reply.call_args.args[1]
    assert "AI 助手" in reply_text


def test_process_bind_doc_command_does_not_call_llm(orch):
    incoming = IncomingMessage(
        message_id="om_b",
        chat_id="oc_1",
        sender_open_id="ou_1",
        text="/bind-doc doccnABC123",
        is_bind_doc_cmd=True,
        bind_doc_id="doccnABC123",
    )
    result = orch.process(incoming)
    orch._test_session.commit()

    assert result["status"] == "bind_doc_success"
    assert result["bound_doc_id"] == "doccnABC123"
    # bind-doc 不调 LLM
    orch.llm.chat.assert_not_called()
    # bind-doc 应回 IM
    orch.im.reply.assert_called_once()
    reply_text = orch.im.reply.call_args.args[1]
    assert "doccnABC123" in reply_text


def test_process_message_with_active_bind_writes_doc(orch):
    # 1. 先 bind-doc
    bind_incoming = IncomingMessage(
        message_id="om_b",
        chat_id="oc_1",
        sender_open_id="ou_1",
        text="/bind-doc doccnABC123",
        is_bind_doc_cmd=True,
        bind_doc_id="doccnABC123",
    )
    orch.process(bind_incoming)
    orch._test_session.commit()

    # 2. 发普通消息
    msg_incoming = IncomingMessage(
        message_id="om_m", chat_id="oc_1", sender_open_id="ou_1", text="hi"
    )
    result = orch.process(msg_incoming)
    orch._test_session.commit()

    assert result["status"] == "success"
    assert result["doc_written"] is True
    assert result["doc_id"] == "doccnABC123"
    # doc_adapter.append_plain_text 应被调用一次（无锚点 → index=-1 追加末尾）
    orch.doc_write_service.doc_adapter.append_plain_text.assert_called_once_with(
        doc_id="doccnABC123", text="你好，我是 AI 助手。", index=-1
    )


def test_process_llm_failure_marks_task_failed_and_replies_error(orch):
    orch.llm.chat.side_effect = LLMCallError("provider down")
    incoming = IncomingMessage(
        message_id="om_x", chat_id="oc_1", sender_open_id="ou_1", text="hi"
    )
    result = orch.process(incoming)
    orch._test_session.commit()

    assert result["status"] == "failed"
    assert "provider down" in result["error"]
    orch.im.reply.assert_called_once()
    reply_text = orch.im.reply.call_args.args[1]
    assert "错误" in reply_text or "provider" in reply_text.lower()


def test_process_doc_write_failure_marks_partial_failure(orch):
    # bind
    orch.process(IncomingMessage(
        message_id="om_b", chat_id="oc_1", sender_open_id="ou_1",
        text="/bind-doc doccnABC123", is_bind_doc_cmd=True, bind_doc_id="doccnABC123",
    ))
    orch._test_session.commit()

    # doc_adapter 抛异常
    orch.doc_write_service.doc_adapter.append_plain_text.side_effect = RuntimeError("lark-cli boom")

    msg_incoming = IncomingMessage(
        message_id="om_m", chat_id="oc_1", sender_open_id="ou_1", text="hi"
    )
    result = orch.process(msg_incoming)
    orch._test_session.commit()

    # IM 已成功，文档失败 → success_with_partial_failure
    assert result["status"] == "success_with_partial_failure"
    assert result["doc_written"] is False
    assert "lark-cli boom" in result["warning"]


def test_process_creates_task_and_audit_records(orch):
    incoming = IncomingMessage(
        message_id="om_audit", chat_id="oc_1", sender_open_id="ou_1", text="hi"
    )
    orch.process(incoming)
    orch._test_session.commit()

    # 至少应有一条 create_task + 一条 complete_task 审计
    audit_logs = orch.task_service.audit_repo.list_recent(limit=10)
    actions = [log.action for log in audit_logs]
    assert "create_task" in actions
    assert "complete_task" in actions
