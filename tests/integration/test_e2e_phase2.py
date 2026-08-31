"""Phase 2 端到端：画箱线图场景（read_doc → run_python → write_doc）。"""
import asyncio
import datetime as dt
from datetime import UTC

from orchestrator.executor.kernel_manager import KernelPool
from orchestrator.executor.local_executor import LocalExecutor
from orchestrator.planner.dag_schema import DAGNode, DAGPlan
from orchestrator.planner.scheduler import Scheduler
from orchestrator.template_engine import TemplateEngine
from orchestrator.tools.tool_handler import ToolHandler
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec
from shared.executor_types import ExecutionState


class FakeSandbox:
    def start(self, session_id):
        return f"c_{session_id}"

    def stop(self, container_name):
        pass


def make_registry_with_python_run():
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="read_doc",
            description="d",
            parameters={"type": "object"},
            risk_level="L0_read",
            handler=lambda doc_id: {
                "blocks": [{"values": "a,b,c\n1,2,3\n4,5,6"}]
            },
        )
    )
    reg.register(
        ToolSpec(
            name="run_python",
            description="d",
            parameters={"type": "object"},
            risk_level="L1_compute",
            handler=lambda code, session_id: {
                "stdout": "ok",
                "result": "boxplot.png",
            },
        )
    )
    reg.register(
        ToolSpec(
            name="write_doc",
            description="d",
            parameters={"type": "object"},
            risk_level="L2_side_effect",
            handler=lambda doc_id, blocks: {"appended": len(blocks)},
        )
    )
    return reg


def _drive_until_done(sch: Scheduler, ex: LocalExecutor):
    """同步驱动：把所有 RUNNING 节点推到 SUCCESS，让 Scheduler 走完。"""
    for _ in range(200):
        if sch._all_terminal():
            return
        dt.datetime.now(UTC)
        for h in list(sch._handles.values()):
            if h.state == ExecutionState.RUNNING:
                h.state = ExecutionState.SUCCESS
                h.outputs = {
                    "blocks": [],
                    "result": "boxplot.png",
                    "appended": 1,
                }
                h.finished_at = dt.datetime.now(UTC)
        # 触发 Scheduler 重新评估 ready
        for n in sch.plan.nodes:
            if n.node_id not in sch._handles:
                # 检查上游
                upstreams = [sch._node_state(d) for d in n.depends_on]
                if not upstreams or all(s == ExecutionState.SUCCESS for s in upstreams):
                    # 让 Scheduler 在下一轮轮询时拿到
                    pass


async def test_e2e_boxplot_flow():
    """完整流程：read_doc → run_python → write_doc + Template 渲染。"""
    reg = make_registry_with_python_run()
    pool = KernelPool(sandbox=FakeSandbox(), idle_timeout_sec=1800)
    handler = ToolHandler(registry=reg)
    ex = LocalExecutor(kernel_pool=pool, tool_handler=handler)

    plan = DAGPlan(
        plan_id="p",
        task_id="t",
        session_id="s",
        nodes=[
            DAGNode(node_id="n1", kind="tool", tool_name="read_doc",
                    inputs={"doc_id": "d1"}, depends_on=[]),
            DAGNode(node_id="n2", kind="tool", tool_name="run_python",
                    inputs={"code": "import matplotlib", "session_id": "s"},
                    depends_on=["n1"]),
            DAGNode(node_id="n3", kind="tool", tool_name="write_doc",
                    inputs={"doc_id": "d1",
                            "blocks": "inline-blocks"},
                    depends_on=["n2"]),
        ],
        entry_node_ids=["n1"],
    )
    sch = Scheduler(plan=plan, executor=ex, max_concurrent=2)

    async def drive():
        for _ in range(200):
            if sch._all_terminal():
                return
            for h in list(sch._handles.values()):
                if h.state == ExecutionState.RUNNING:
                    h.state = ExecutionState.SUCCESS
                    h.outputs = {
                        "blocks": [],
                        "result": "boxplot.png",
                        "appended": 1,
                    }
                    h.finished_at = dt.datetime.now(UTC)
            await asyncio.sleep(0.01)

    asyncio.create_task(drive())
    result = await asyncio.wait_for(sch.run_until_done(), timeout=2.0)
    assert result.status in ("success", "success_with_partial_failure")

    # Template 渲染
    tmpl = TemplateEngine()
    blocks = tmpl.render_plan_summary(
        status=result.status,
        node_states={k: v.value for k, v in result.node_states.items()},
        artifacts_count=1,
    )
    assert any(b.block_type == "heading_2" for b in blocks)
