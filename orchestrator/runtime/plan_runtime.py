"""PlanRuntime：抽离 Scheduler 中的动态化 / 循环 / 冻结逻辑。

状态机：
- init: 构造完成
- running: run() 进行中
- terminal: 所有节点终态 / Plan 完成

Phase 3 简化版：仅暴露 dynamic-append 与 loop 计数接口；
run() 由 Scheduler 调用（Phase 3 完整调度集成见 orchestrator.app.process_phase3）。"""
from __future__ import annotations

import datetime
from enum import Enum
from typing import Optional

from orchestrator.planner.dag_schema import DAGNode, DAGPlan, validate_dag
from shared.errors import DynamicAppendError, LoopMaxIterError
from shared.ulid_ import new_ulid


class RuntimeState(str, Enum):
    INIT = "init"
    RUNNING = "running"
    TERMINAL = "terminal"


class PlanRuntime:
    def __init__(
        self,
        *,
        state_repo,
        audit_repo,
        max_iterations: int = 10,
        plan_id: Optional[str] = None,
    ) -> None:
        self.state_repo = state_repo
        self.audit_repo = audit_repo
        self.max_iterations = max_iterations
        self.plan_id = plan_id
        self.state = RuntimeState.INIT
        self._loop_counters: dict[str, int] = {}
        self._dynamic_nodes: list[dict] = []
        self._iteration_vars: dict[str, list] = {}

    # === 动态追加 ===
    def append_dynamic_nodes(
        self,
        *,
        plan_id: str,
        parent_node_id: str,
        new_nodes: list[DAGNode],
    ) -> None:
        """校验 + 记录到 state。失败抛 DynamicAppendError。"""
        # 1. 校验：构造虚拟 plan 走 validate_dag
        fake_plan = DAGPlan(
            plan_id=plan_id, task_id="t", session_id="s",
            nodes=[
                DAGNode(
                    node_id=parent_node_id, kind="branch",
                    condition_prompt="x",
                    true_branch=new_nodes,
                    false_branch=[],
                    depends_on=[],
                )
            ],
            entry_node_ids=[parent_node_id],
        )
        try:
            validate_dag(fake_plan)
        except Exception as e:
            if self.audit_repo is not None:
                self.audit_repo.write(
                    actor_type="system", actor_id="plan_runtime",
                    action="dynamic_append_failed", target_type="plan",
                    target_id=plan_id, detail={"parent": parent_node_id, "error": str(e)},
                )
            raise DynamicAppendError(f"validate_dag failed: {e}")

        # 2. 记录
        self._dynamic_nodes.append({
            "parent_node_id": parent_node_id,
            "appended_at": datetime.datetime.utcnow().isoformat(),
            "node_ids": [n.node_id for n in new_nodes],
        })
        # 3. 持久化
        if self.state_repo is not None:
            self.state_repo.upsert(
                plan_id=plan_id,
                state_json={
                    "loop_counters": self._loop_counters,
                    "dynamic_nodes": self._dynamic_nodes,
                    "iteration_vars": self._iteration_vars,
                },
            )
        # 4. 审计
        if self.audit_repo is not None:
            self.audit_repo.write(
                actor_type="system", actor_id="plan_runtime",
                action="append_dynamic_nodes", target_type="plan",
                target_id=plan_id, detail={"parent": parent_node_id, "count": len(new_nodes)},
            )

    # === 循环节点 ===
    def loop_iteration_done(self, loop_id: str) -> int:
        """返回当前 iteration（1-based）；超过 max_iterations 抛 LoopMaxIterError。"""
        self._loop_counters[loop_id] = self._loop_counters.get(loop_id, 0) + 1
        if self._loop_counters[loop_id] > self.max_iterations:
            if self.audit_repo is not None:
                self.audit_repo.write(
                    actor_type="system", actor_id="plan_runtime",
                    action="loop_max_iter", target_type="loop",
                    target_id=loop_id, detail={"count": self._loop_counters[loop_id]},
                )
            raise LoopMaxIterError(
                f"loop {loop_id} reached max_iterations {self.max_iterations}"
            )
        return self._loop_counters[loop_id]

    def loop_exit(self, loop_id: str) -> None:
        self._loop_counters.pop(loop_id, None)
        if self.audit_repo is not None:
            self.audit_repo.write(
                actor_type="system", actor_id="plan_runtime",
                action="loop_exit", target_type="loop", target_id=loop_id, detail={},
            )

    # === session 冻结（仅记录 + 生成新 id）===
    def freeze_session(self, *, origin_session_id: str, summary: str) -> str:
        """生成新 session_id 并审计；实际 session 创建由 SessionService 处理。"""
        new_sid = new_ulid()
        if self.audit_repo is not None:
            self.audit_repo.write(
                actor_type="system", actor_id="plan_runtime",
                action="freeze_session", target_type="session",
                target_id=origin_session_id, detail={"new_session_id": new_sid},
            )
        return new_sid

    async def run(self, plan: DAGPlan) -> dict:
        """Phase 3 简化版入口。Phase 3.1 接入完整调度逻辑。"""
        self.state = RuntimeState.RUNNING
        try:
            return {"status": "delegated_to_scheduler", "plan_id": plan.plan_id}
        finally:
            self.state = RuntimeState.TERMINAL
