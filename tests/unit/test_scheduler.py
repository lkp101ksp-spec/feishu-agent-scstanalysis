import asyncio
import datetime as dt

import pytest

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