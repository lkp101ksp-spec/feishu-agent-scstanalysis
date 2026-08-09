from orchestrator.planner.dag_schema import DAGNode, DAGPlan
from orchestrator.planner.scheduler import Scheduler
from orchestrator.runtime.plan_runtime import PlanRuntime


def _make_executor():
    from shared.executor_types import ExecutionState, ExecutionTask, TaskHandle
    import datetime as dt

    class FakeExecutor:
        def submit(self, task):
            return TaskHandle(
                execution_id="e1", task_id=task.task_id, node_id=task.node_id,
                state=ExecutionState.RUNNING, started_at=dt.datetime.utcnow(),
            )

        def get_status(self, h):
            return h.state

        def cancel(self, h):
            h.state = ExecutionState.CANCELLED

        def list_active(self):
            return []

    return FakeExecutor()


def test_scheduler_with_runtime_delegates():
    rt = PlanRuntime(state_repo=None, audit_repo=None)
    ex = _make_executor()
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[DAGNode(node_id="n1", kind="tool", tool_name="a",
                                  inputs={}, depends_on=[])],
                   entry_node_ids=["n1"])
    sch = Scheduler(plan=plan, executor=ex, runtime=rt)
    assert sch.runtime is rt


def test_scheduler_without_runtime_uses_phase2_path():
    ex = _make_executor()
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[DAGNode(node_id="n1", kind="tool", tool_name="a",
                                  inputs={}, depends_on=[])],
                   entry_node_ids=["n1"])
    sch = Scheduler(plan=plan, executor=ex, runtime=None)
    assert sch.runtime is None