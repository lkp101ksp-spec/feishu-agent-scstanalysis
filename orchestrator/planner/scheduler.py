"""DAG Scheduler：协程轮询驱动 ready 节点 → ExecutorClient。

- ready = 所有 depends_on 已 SUCCESS
- 失败处理：on_node_fail=continue → 下游 SKIPPED；stop_plan → 终止
- 并发上限：max_concurrent_nodes（默认 4）
- 默认 on_node_fail=continue（见 ADR）

Phase 2 实现：
- run_until_done() 用 asyncio.sleep(0.01) 轮询 + 触发新节点
- 节点状态变化由 ExecutorClient.get_status() 反馈
- 全部终态后 collect_result() 汇总 PlanResult
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from orchestrator.planner.dag_schema import DAGNode, DAGPlan
from shared.executor_types import ExecutionState, ExecutionTask, TaskHandle


@dataclass
class PlanResult:
    plan_id: str
    status: str  # success | success_with_partial_failure | failed
    node_states: dict[str, ExecutionState]
    started_at: datetime
    finished_at: datetime


class Scheduler:
    def __init__(self, plan: DAGPlan, executor, max_concurrent: int = 4, runtime=None) -> None:
        self.plan = plan
        self.executor = executor
        self.max_concurrent = max_concurrent
        self.runtime = runtime  # Phase 3：可选注入；None 时走 Phase 2 路径
        self._handles: dict[str, TaskHandle] = {}
        self._node_map: dict[str, DAGNode] = {n.node_id: n for n in plan.nodes}
        self._started_at = datetime.utcnow()

    @staticmethod
    def _expand_subplan_static(node: DAGNode, template_service) -> list[DAGNode]:
        """Phase 5: 展开 sub-Plan 模板为内联 DAGNode 列表。"""
        if not node.subplan_template_id:
            return [node]
        tpl = template_service.get(node.subplan_template_id)
        if tpl is None or tpl.archived_at is not None:
            raise ValueError(f"template {node.subplan_template_id} not found")
        if tpl.type != "subplan":
            raise ValueError(
                f"template {node.subplan_template_id} is not subplan"
            )
        steps = template_service.render_subplan(
            template_id=node.subplan_template_id,
            params=node.subplan_params,
        )
        out = []
        for i, step in enumerate(steps):
            out.append(DAGNode(
                node_id=f"{node.node_id}_step_{i}",
                kind="tool",
                tool_name=step.tool_name,
                inputs=step.inputs,
                depends_on=list(node.depends_on) if i == 0 else [],
            ))
        return out

    def _node_state(self, node_id: str) -> ExecutionState:
        if node_id not in self._handles:
            return ExecutionState.PENDING
        return self._handles[node_id].state

    def _ready_nodes(self) -> list[DAGNode]:
        out: list[DAGNode] = []
        for node in self.plan.nodes:
            if node.node_id in self._handles:
                continue
            upstreams = [self._node_state(d) for d in node.depends_on]
            if not upstreams:
                out.append(node)
                continue
            # 上游任一非 SUCCESS → 下游 SKIPPED
            bad = any(
                s in (
                    ExecutionState.FAILED,
                    ExecutionState.DENIED,
                    ExecutionState.SKIPPED,
                    ExecutionState.CANCELLED,
                )
                for s in upstreams
            )
            if bad:
                self._handles[node.node_id] = TaskHandle(
                    execution_id=f"sk_{node.node_id}",
                    task_id=self.plan.task_id,
                    node_id=node.node_id,
                    state=ExecutionState.SKIPPED,
                    started_at=datetime.utcnow(),
                    finished_at=datetime.utcnow(),
                )
                continue
            if all(s == ExecutionState.SUCCESS for s in upstreams):
                out.append(node)
        return out

    def _all_terminal(self) -> bool:
        states = [self._node_state(n.node_id) for n in self.plan.nodes]
        return all(
            s in (
                ExecutionState.SUCCESS,
                ExecutionState.FAILED,
                ExecutionState.SKIPPED,
                ExecutionState.CANCELLED,
                ExecutionState.DENIED,
            )
            for s in states
        )

    def _resolve_inputs(self, node: DAGNode) -> dict:
        """从上游 outputs 解析 <node>.field 形式的引用（值非 str 时原样透传）。"""
        resolved: dict = {}
        for k, v in node.inputs.items():
            if isinstance(v, str) and "." in v:
                upstream_id, field_name = v.split(".", 1)
                up_handle = self._handles.get(upstream_id)
                if up_handle and up_handle.outputs:
                    resolved[k] = up_handle.outputs.get(field_name)
                else:
                    resolved[k] = None
            else:
                resolved[k] = v
        return resolved

    def _refresh_running_handles(self) -> None:
        for h in list(self._handles.values()):
            if h.state == ExecutionState.RUNNING:
                current = self.executor.get_status(h)
                if current != ExecutionState.RUNNING:
                    h.state = current

    async def run_until_done(self) -> PlanResult:
        # Phase 3：runtime 注入时委托给 Runtime（dynamic-append / loop / freeze）
        if self.runtime is not None:
            from orchestrator.runtime.plan_runtime import RuntimeState
            self.runtime.state = RuntimeState.RUNNING
            try:
                # Phase 3 简化版：Runtime 仅承载状态；驱动循环仍由 Scheduler 负责
                # 完整调度集成由 Phase 3.1 / Phase 4 完成
                return await self._run_phase2_loop()
            finally:
                self.runtime.state = RuntimeState.TERMINAL
        return await self._run_phase2_loop()

    async def _run_phase2_loop(self) -> PlanResult:
        # Phase 2 原逻辑（保留）
        while not self._all_terminal():
            ready = self._ready_nodes()
            for node in ready[: self.max_concurrent]:
                task = ExecutionTask(
                    task_id=self.plan.task_id,
                    node_id=node.node_id,
                    tool_name=node.tool_name or "",
                    inputs=self._resolve_inputs(node),
                )
                handle = self.executor.submit(task)
                self._handles[node.node_id] = handle
            await asyncio.sleep(0.01)
            self._refresh_running_handles()
        return self._collect_result()

    def _collect_result(self) -> PlanResult:
        node_states = {n.node_id: self._node_state(n.node_id) for n in self.plan.nodes}
        failed = any(s == ExecutionState.FAILED for s in node_states.values())
        skipped = any(s == ExecutionState.SKIPPED for s in node_states.values())
        if failed and not skipped:
            status = "failed"
        elif failed or skipped:
            status = "success_with_partial_failure"
        else:
            status = "success"
        return PlanResult(
            plan_id=self.plan.plan_id,
            status=status,
            node_states=node_states,
            started_at=self._started_at,
            finished_at=datetime.utcnow(),
        )
