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
    bind_svc = BindDocService(session_svc, audit_repo, ttl_sec=1800,
                               session_repo=session_repo)

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


def test_bind_doc_renew_command(orch):
    """生产 process() 路由 /bind-doc-renew：绑定后可手动续期。"""
    from datetime import datetime, timezone

    from persistence.models import SessionRow

    orch.process(IncomingMessage(
        message_id="om_b", chat_id="oc_1", sender_open_id="ou_1",
        text="/bind-doc doccnABC123", is_bind_doc_cmd=True,
        bind_doc_id="doccnABC123",
    ))
    orch._test_session.commit()
    sid_row = orch._test_session.query(SessionRow).one()
    old_exp = sid_row.bind_expires_at

    result = orch.process(IncomingMessage(
        message_id="om_r", chat_id="oc_1", sender_open_id="ou_1",
        text="/bind-doc-renew",
    ))
    orch._test_session.commit()

    assert result["status"] == "renew_bind"
    orch._test_session.expire_all()
    new_exp = orch._test_session.get(SessionRow, result["session_id"]).bind_expires_at
    assert new_exp >= old_exp
    # SQLite 存回的 naive UTC → 补 tz 再比 now
    assert new_exp.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    # 回复了续期成功消息，且没有走 LLM
    orch.llm.chat.assert_not_called()
    assert "已续期" in orch.im.reply.call_args.args[-1]


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


def test_bind_doc_with_anchor_persists_to_session(orch):
    """回归：/bind-doc 带 @锚点 必须把 anchor 落库到 session.bind_anchor。"""
    from persistence.models import SessionRow

    incoming = IncomingMessage(
        message_id="om_b",
        chat_id="oc_1",
        sender_open_id="ou_1",
        text="/bind-doc doccnABC123 @1 测试",
        is_bind_doc_cmd=True,
        bind_doc_id="doccnABC123",
        bind_anchor="1 测试",
    )
    result = orch.process(incoming)
    orch._test_session.commit()

    assert result["status"] == "bind_doc_success"
    row = orch._test_session.get(SessionRow, result["session_id"])
    assert row.bind_anchor == "1 测试"


def test_process_write_to_msg_locates_anchor(orch):
    """#写到 语法：消息级锚点定位写入，LLM 只见剥离前缀后的正文。"""
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

    # 2. mock 文档块树：锚点章节在 index 0
    orch.doc_write_service.doc_adapter.list_root_children.return_value = [
        {"block_id": "b0", "block_type": 2,
         "text": {"elements": [{"text_run": {"content": "1 测试"}}]}},
        {"block_id": "b1", "block_type": 2,
         "text": {"elements": [{"text_run": {"content": "其他"}}]}},
    ]

    # 3. 发 #写到 消息（normalizer 已剥离前缀 → 干净正文 + write_anchor）
    msg_incoming = IncomingMessage(
        message_id="om_m", chat_id="oc_1", sender_open_id="ou_1",
        text="帮我记录结论", write_anchor="1 测试",
    )
    result = orch.process(msg_incoming)
    orch._test_session.commit()

    assert result["status"] == "success"
    assert result["doc_written"] is True
    # LLM 收到的 user 消息是干净正文（不含 #写到 前缀）
    user_msg = orch.llm.chat.call_args.args[0][-1]
    assert user_msg.content == "帮我记录结论"
    # 写入插到锚点块之后（index=1）
    orch.doc_write_service.doc_adapter.append_plain_text.assert_called_once_with(
        doc_id="doccnABC123", text="你好，我是 AI 助手。", index=1
    )


def test_process_write_to_without_body_replies_usage(orch):
    """#写到 语法不完整（有锚点没正文）：回用法提示，不进 LLM。"""
    incoming = IncomingMessage(
        message_id="om_u", chat_id="oc_1", sender_open_id="ou_1",
        text="", write_anchor="1 测试",
    )
    result = orch.process(incoming)
    assert result["status"] == "skipped"
    orch.llm.chat.assert_not_called()
    orch.im.reply.assert_called_once()
    assert "#写到" in orch.im.reply.call_args.args[1]


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


# === Phase 5-9 市场指令接入生产 process()（_try_market_commands 路由器） ===


def test_process_routes_template_list(orch):
    """生产 process() 路由 /template-list：不调 LLM，回复模板清单。"""
    ts = MagicMock()
    ts.list_by_owner.return_value = [
        type("T", (), {"template_id": "tpl_a"})(),
        type("T", (), {"template_id": "tpl_b"})(),
    ]
    orch.template_service = ts

    result = orch.process(IncomingMessage(
        message_id="om_tl", chat_id="oc_1", sender_open_id="ou_1",
        text="/template-list",
    ))

    assert result["status"] == "template_listed"
    assert result["templates"] == ["tpl_a", "tpl_b"]
    orch.llm.chat.assert_not_called()
    orch.im.reply.assert_called_once()
    assert "tpl_a" in orch.im.reply.call_args.args[1]


def test_process_routes_template_find(orch):
    """生产 process() 路由 /template-find：走统一检索并渲染结果。"""
    us = MagicMock()
    us.search.return_value = [{"template_id": "tpl_x"}]
    us.render.return_value = "(1) tpl_x"
    orch.unified_search_service = us

    result = orch.process(IncomingMessage(
        message_id="om_tf", chat_id="oc_1", sender_open_id="ou_1",
        text="/template-find 单细胞 #范文",
    ))

    assert result["status"] == "find_rendered"
    us.search.assert_called_once_with(query="单细胞", tag="范文", limit=10)
    orch.llm.chat.assert_not_called()
    assert orch.im.reply.call_args.args[1] == "(1) tpl_x"


def test_process_market_command_not_configured_replies_hint(orch):
    """评论服务未注入（无 FEISHU_API_* 环境变量）：回复未配置提示而非抛错。"""
    result = orch.process(IncomingMessage(
        message_id="om_cs", chat_id="oc_1", sender_open_id="ou_1",
        text="/comments-sync doccnXYZ",
    ))

    assert result["status"] == "sync_failed"
    orch.llm.chat.assert_not_called()
    assert "未配置" in orch.im.reply.call_args.args[1]


def test_process_ordinary_message_still_goes_to_llm(orch):
    """普通消息不受市场路由影响：照常走 LLM。"""
    result = orch.process(IncomingMessage(
        message_id="om_norm", chat_id="oc_1", sender_open_id="ou_1",
        text="帮我总结一下",
    ))

    assert result["status"] == "success"
    orch.llm.chat.assert_called_once()


def test_process_template_tag_routes(orch):
    """生产 process() 路由 /template-tag：打标签并回显规范化结果。"""
    tag_svc = MagicMock()
    tag_svc.attach.return_value = "范文"
    orch.tag_service = tag_svc

    result = orch.process(IncomingMessage(
        message_id="om_tag", chat_id="oc_1", sender_open_id="ou_1",
        text="/template-tag tpl_a 范文模板",
    ))

    assert result["status"] == "tagged"
    tag_svc.attach.assert_called_once_with(
        template_id="tpl_a", tag="范文模板", caller_open_id="ou_1")
    orch.llm.chat.assert_not_called()
