"""Phase 12 板块⑤：ResearchRunner 测试（内存 SQLite + 假引擎组件）。

假 Executor 直接返回 SUCCESS 句柄（不起线程跑真实工具），
验证受理即回、后台执行、结果回复与文档写回语义。
"""
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.planner.dag_schema import DAGNode, DAGPlan
from orchestrator.research_runner import ResearchRunner
from persistence.models import Base
from shared.executor_types import ExecutionState, ExecutionTask, TaskHandle
from shared.schemas import IncomingMessage
from shared.ulid_ import new_ulid


class _FakeExecutor:
    """submit 即 SUCCESS 的假执行器；记录收到的工具调用。"""

    def __init__(self, outputs: dict | None = None) -> None:
        self.calls: list[str] = []
        self.outputs = outputs or {"summary": "- 要点"}

    def submit(self, task: ExecutionTask) -> TaskHandle:
        self.calls.append(task.tool_name)
        return TaskHandle(
            execution_id=new_ulid(), task_id=task.task_id, node_id=task.node_id,
            state=ExecutionState.SUCCESS, started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(), outputs=self.outputs,
        )

    def cancel(self, handle) -> None: ...

    def get_status(self, handle) -> ExecutionState:
        return handle.state

    def list_active(self) -> list:
        return []


def _plan() -> DAGPlan:
    return DAGPlan(
        plan_id="plan_1", task_id="t1", session_id="s1",
        nodes=[
            DAGNode(node_id="n1", kind="tool", tool_name="summarize_text",
                    inputs={"text": "x"}),
        ],
        entry_node_ids=["n1"],
    )


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _incoming(text="") -> IncomingMessage:
    return IncomingMessage(
        message_id="om_r", chat_id="oc_r", sender_open_id="ou_r", text=text,
    )


def _wait_reply_count(im, count: int, timeout: float = 10.0) -> bool:
    """轮询等待后台线程完成指定次数的 im.reply。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if im.reply.call_count >= count:
            return True
        time.sleep(0.02)
    return False


def _orch(db, *, with_engine=True, bound_doc=None, writeback="bind_scope",
          approval_timeout=30):
    """构造带假引擎的 Orchestrator 替身（SimpleNamespace）。"""
    from orchestrator.session_service import SessionService
    from orchestrator.template_engine import TemplateEngine
    from orchestrator.tools.builtin.l1_compute import register_l1_compute
    from orchestrator.tools.tool_registry import ToolRegistry

    im = MagicMock()
    reg = ToolRegistry()
    register_l1_compute(reg, llm_router=MagicMock(), kernel_manager=None)
    planner = MagicMock()
    planner.plan.return_value = _plan()
    executor = _FakeExecutor()
    doc_adapter = MagicMock()
    orch = SimpleNamespace(
        im=im, registry=reg, planner=planner, executor=executor,
        template=TemplateEngine(), doc_adapter=doc_adapter,
        settings=SimpleNamespace(
            max_concurrent_nodes=2, research_task_timeout_sec=30,
            research_writeback_approval=writeback,
            research_approval_timeout_sec=approval_timeout,
        ),
    )
    # 预置绑定文档（直接写 DB，模拟 /bind-doc 已生效）
    if bound_doc:
        s = db()
        from persistence.repositories.session_repo import SessionRepo
        repo = SessionRepo(s)
        svc = SessionService(repo)
        sid = svc.get_or_create(owner_open_id="ou_r", source_chat_id="oc_r")
        svc.bind_doc(session_id=sid, doc_id=bound_doc, ttl_sec=1800)
        s.commit()
        s.close()
    if not with_engine:
        del orch.planner  # 模拟引擎未初始化
    return orch


def test_usage_reply_when_no_args(db):
    orch = _orch(db)
    runner = ResearchRunner(orchestrator=orch, session_factory=db)
    out = runner.handle(_incoming("/research"))
    assert out == {"status": "research_usage"}
    assert "用法" in orch.im.reply.call_args.args[1]


def test_engine_unavailable(db):
    orch = _orch(db, with_engine=False)
    runner = ResearchRunner(orchestrator=orch, session_factory=db)
    out = runner.handle(_incoming("/research 分析一下"))
    assert out == {"status": "research_engine_unavailable"}
    assert "未初始化" in orch.im.reply.call_args.args[1]


def test_accepted_and_background_result(db):
    """受理即回（第 1 条）+ 后台完成回结果（第 2 条）。"""
    orch = _orch(db, bound_doc="doccnR1")
    runner = ResearchRunner(orchestrator=orch, session_factory=db)
    out = runner.handle(_incoming("/research 总结文档要点"))

    assert out["status"] == "research_accepted"
    # 第 1 条：受理语
    assert "已受理" in orch.im.reply.call_args_list[0].args[1]
    # 等后台线程完成第 2 条回复
    assert _wait_reply_count(orch.im, 2)
    final = orch.im.reply.call_args_list[1].args[1]
    assert "执行完成" in final
    assert "n1: success" in final
    assert "结果已写入绑定文档 doccnR1" in final
    # 假执行器收到 Planner 计划的工具调用
    assert orch.executor.calls == ["summarize_text"]
    # 文档写回（真实 API 是 render_blocks）
    orch.doc_adapter.render_blocks.assert_called_once()


def test_plan_failed_replies_error(db):
    from shared.errors import DAGValidationError

    orch = _orch(db)
    orch.planner.plan.side_effect = DAGValidationError("bad dag")
    runner = ResearchRunner(orchestrator=orch, session_factory=db)
    out = runner.handle(_incoming("/research 无效任务"))
    assert out["status"] == "research_accepted"
    assert _wait_reply_count(orch.im, 2)
    final = orch.im.reply.call_args_list[1].args[1]
    assert "规划失败" in final


def test_l2_tools_excluded_from_planner(db):
    """L2 副作用工具不给 Planner（名字也不给）——模型规划 write_doc 只会被
    approval 拒掉（真机 2026-08-30 n3 TOOL_DENIED）。"""
    from orchestrator.tools.builtin.l2_side_effect import register_l2_side_effect

    orch = _orch(db)
    register_l2_side_effect(
        orch.registry, doc_adapter=object(), base_adapter=object(),
        im_adapter=object(), drive_adapter=object(),
    )
    runner = ResearchRunner(orchestrator=orch, session_factory=db)
    runner.handle(_incoming("/research 总结要点"))
    assert _wait_reply_count(orch.im, 2)
    kwargs = orch.planner.plan.call_args.kwargs
    assert "write_doc" not in kwargs["available_tools"]
    assert "send_card" not in kwargs["available_tools"]
    schema_names = {f["function"]["name"] for f in kwargs["tools_schema"]}
    assert "write_doc" not in schema_names


def test_task_row_created_with_research_intent(db):
    """后台执行后 tasks 表落 intent=research 记录（按 message_id 查）。"""
    from persistence.repositories.task_repo import TaskRepo

    orch = _orch(db)
    runner = ResearchRunner(orchestrator=orch, session_factory=db)
    runner.handle(_incoming("/research 总结要点"))
    assert _wait_reply_count(orch.im, 2)
    s = db()
    task = TaskRepo(s).get_by_message_id("om_r")
    assert task is not None
    assert task.intent == "research"
    s.close()


# === Phase 14：card_confirm 卡片确认写回 ===

def _wait_card_sent(im, timeout: float = 10.0) -> dict:
    """轮询等待审批卡发出，返回首个按钮的 value（含 doc_write_id/decision）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if im.send_card.call_count >= 1:
            card = im.send_card.call_args.args[1]
            return card["elements"][-1]["actions"][0]["value"]
        time.sleep(0.02)
    raise AssertionError("approval card not sent in time")


def _latest_doc_write(db):
    from persistence.models import DocWriteRow
    s = db()
    row = s.query(DocWriteRow).order_by(
        DocWriteRow.created_at.desc()).first()
    s.close()
    return row


def test_card_confirm_approve_writes_doc(db):
    """同意：卡片确认后 render_blocks 写入，doc_writes 落 card_confirm success。"""
    from orchestrator.approval_broker import ApprovalBroker

    orch = _orch(db, bound_doc="doccnR1", writeback="card_confirm")
    orch.doc_adapter.render_blocks.return_value = "blk_ok"  # 落库需真实字符串
    broker = ApprovalBroker()
    orch.approval_broker = broker
    runner = ResearchRunner(orchestrator=orch, session_factory=db)
    runner.handle(_incoming("/research 总结要点"))

    value = _wait_card_sent(orch.im)
    assert value["action"] == "research_writeback"
    assert value["decision"] == "approve"
    assert broker.decide(value["doc_write_id"], "approve", "ou_r") is True

    assert _wait_reply_count(orch.im, 2)
    final = orch.im.reply.call_args_list[1].args[1]
    assert "已写入文档（经卡片确认）" in final
    orch.doc_adapter.render_blocks.assert_called_once()
    row = _latest_doc_write(db)
    assert row.approval_mode == "card_confirm"
    assert row.status == "success"


def test_card_confirm_deny_skips_write(db):
    """跳过：不写文档，doc_writes 落 cancelled。"""
    from orchestrator.approval_broker import ApprovalBroker

    orch = _orch(db, bound_doc="doccnR1", writeback="card_confirm")
    broker = ApprovalBroker()
    orch.approval_broker = broker
    runner = ResearchRunner(orchestrator=orch, session_factory=db)
    runner.handle(_incoming("/research 总结要点"))

    value = _wait_card_sent(orch.im)
    # 点「跳过」按钮（actions[1]，decision=deny）
    deny_value = orch.im.send_card.call_args.args[1]["elements"][-1]["actions"][1]["value"]
    assert deny_value["decision"] == "deny"
    assert broker.decide(value["doc_write_id"], "deny", "ou_r") is True

    assert _wait_reply_count(orch.im, 2)
    final = orch.im.reply.call_args_list[1].args[1]
    assert "已按您的选择跳过写回" in final
    orch.doc_adapter.render_blocks.assert_not_called()
    assert _latest_doc_write(db).status == "cancelled"


def test_card_confirm_timeout_skips_write(db):
    """超时无人点：安全侧失败按拒绝收尾（timeout=0 立即超时）。"""
    orch = _orch(db, bound_doc="doccnR1", writeback="card_confirm",
                 approval_timeout=0)
    from orchestrator.approval_broker import ApprovalBroker
    orch.approval_broker = ApprovalBroker()
    runner = ResearchRunner(orchestrator=orch, session_factory=db)
    runner.handle(_incoming("/research 总结要点"))

    assert _wait_reply_count(orch.im, 2)
    final = orch.im.reply.call_args_list[1].args[1]
    assert "审批超时" in final
    orch.doc_adapter.render_blocks.assert_not_called()
    assert _latest_doc_write(db).status == "cancelled"


def test_card_confirm_without_broker_falls_back_to_direct_write(db):
    """broker 未装配：降级 bind_scope 直写（不阻塞等待）。"""
    orch = _orch(db, bound_doc="doccnR1", writeback="card_confirm")
    # 不设 orch.approval_broker
    runner = ResearchRunner(orchestrator=orch, session_factory=db)
    runner.handle(_incoming("/research 总结要点"))

    assert _wait_reply_count(orch.im, 2)
    final = orch.im.reply.call_args_list[1].args[1]
    assert "结果已写入绑定文档 doccnR1" in final
    orch.doc_adapter.render_blocks.assert_called_once()


def test_bind_scope_mode_unchanged_regression(db):
    """bind_scope 模式：无卡片，直写（Phase 13 行为回归基线）。"""
    from orchestrator.approval_broker import ApprovalBroker

    orch = _orch(db, bound_doc="doccnR1", writeback="bind_scope")
    orch.approval_broker = ApprovalBroker()  # 有 broker 也不走卡片
    runner = ResearchRunner(orchestrator=orch, session_factory=db)
    runner.handle(_incoming("/research 总结要点"))

    assert _wait_reply_count(orch.im, 2)
    orch.im.send_card.assert_not_called()
    orch.doc_adapter.render_blocks.assert_called_once()
