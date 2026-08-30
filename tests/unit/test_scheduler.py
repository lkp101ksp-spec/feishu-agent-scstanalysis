import asyncio
import datetime as dt

from orchestrator.planner.dag_schema import DAGNode, DAGPlan
from orchestrator.planner.scheduler import Scheduler
from shared.executor_types import ExecutionState, ExecutionTask, TaskHandle


class FakeExecutor:
    def __init__(self):
        self.submitted: list[ExecutionTask] = []
        self.handles: dict[str, TaskHandle] = {}

    def submit(self, task: ExecutionTask) -> TaskHandle:
        h = TaskHandle(
            execution_id=f"e_{len(self.submitted)}",
            task_id=task.task_id,
            node_id=task.node_id,
            state=ExecutionState.RUNNING,
            started_at=dt.datetime.utcnow(),
        )
        self.submitted.append(task)
        self.handles[h.execution_id] = h
        return h

    def get_status(self, handle):
        return handle.state

    def cancel(self, handle):
        handle.state = ExecutionState.CANCELLED

    def list_active(self):
        return [h for h in self.handles.values() if h.state == ExecutionState.RUNNING]


def make_plan() -> DAGPlan:
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="a",
                 inputs={}, depends_on=[])
    n2 = DAGNode(node_id="n2", kind="tool", tool_name="b",
                 inputs={"x": "n1.result"}, depends_on=["n1"])
    return DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, n2], entry_node_ids=["n1"])


async def test_scheduler_runs_sequentially():
    plan = make_plan()
    ex = FakeExecutor()
    sch = Scheduler(plan=plan, executor=ex)

    async def drive():
        for _ in range(50):
            await asyncio.sleep(0.005)
            # n1 first
            for h in ex.handles.values():
                if h.state == ExecutionState.RUNNING and h.finished_at is None:
                    h.state = ExecutionState.SUCCESS
                    h.outputs = {"result": "ok"}
                    h.finished_at = dt.datetime.utcnow()
            if sch._all_terminal():
                return

    asyncio.create_task(drive())
    result = await asyncio.wait_for(sch.run_until_done(), timeout=2.0)
    assert ex.handles["e_1"].state == ExecutionState.SUCCESS
    assert result.status == "success"


async def test_scheduler_continues_on_failure():
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="a",
                 inputs={}, depends_on=[], on_node_fail="continue")
    n2 = DAGNode(node_id="n2", kind="tool", tool_name="b",
                 inputs={"x": "n1.result"}, depends_on=["n1"])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, n2], entry_node_ids=["n1"])
    ex = FakeExecutor()
    sch = Scheduler(plan=plan, executor=ex)

    async def drive():
        for _ in range(50):
            await asyncio.sleep(0.005)
            for h in ex.handles.values():
                if h.state == ExecutionState.RUNNING and h.finished_at is None:
                    h.state = ExecutionState.FAILED
                    h.error_code = "X"
                    h.finished_at = dt.datetime.utcnow()
            if sch._all_terminal():
                return

    asyncio.create_task(drive())
    result = await asyncio.wait_for(sch.run_until_done(), timeout=2.0)
    # n2 因上游失败被 SKIPPED
    assert any(s == ExecutionState.SKIPPED for s in result.node_states.values())
    assert result.status == "success_with_partial_failure"


def test_scheduler_init_stores_plan():
    plan = make_plan()
    ex = FakeExecutor()
    sch = Scheduler(plan=plan, executor=ex)
    assert sch.plan.plan_id == "p"
    assert sch.max_concurrent == 4


def test_resolve_inputs_alias_fallback():
    """引用字段缺失时按别名回退（records/text/results/summary）。

    真机 2026-08-30：模型把 blast 输出 records 猜成 summary，
    下游拿到 None 导致摘要空转。
    """
    plan = make_plan()
    ex = FakeExecutor()
    sch = Scheduler(plan=plan, executor=ex)
    # 上游输出只有 records（无 result/summary）
    sch._handles["n1"] = TaskHandle(
        execution_id="e0", task_id="t", node_id="n1",
        state=ExecutionState.SUCCESS, started_at=dt.datetime.utcnow(),
        finished_at=dt.datetime.utcnow(),
        outputs={"records": [{"title": "BRCA1"}]},
    )
    resolved = sch._resolve_inputs(plan.nodes[1])
    assert resolved["x"] == [{"title": "BRCA1"}]  # n1.result → 回退 n1.records


def test_resolve_inputs_missing_without_alias_is_none():
    """无别名可回退时保持 None（不抛错）。"""
    plan = make_plan()
    ex = FakeExecutor()
    sch = Scheduler(plan=plan, executor=ex)
    sch._handles["n1"] = TaskHandle(
        execution_id="e0", task_id="t", node_id="n1",
        state=ExecutionState.SUCCESS, started_at=dt.datetime.utcnow(),
        finished_at=dt.datetime.utcnow(),
        outputs={"answer": 42},
    )
    resolved = sch._resolve_inputs(plan.nodes[1])
    assert resolved["x"] is None
