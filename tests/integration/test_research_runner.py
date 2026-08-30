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


def _orch(db, *, with_engine=True, bound_doc=None):
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
