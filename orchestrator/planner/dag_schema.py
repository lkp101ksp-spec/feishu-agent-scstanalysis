"""DAG 节点 / Plan Pydantic 模型 + 静态校验。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from shared.errors import DAGValidationError


class DAGNode(BaseModel):
    node_id: str
    kind: Literal["tool", "llm", "branch", "join"]
    tool_name: Optional[str] = None
    inputs: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    condition: Optional[str] = None
    true_branch: Optional[list["DAGNode"]] = None
    false_branch: Optional[list["DAGNode"]] = None
    join_strategy: Optional[Literal["all", "any", "first"]] = None
    on_node_fail: Literal["stop_plan", "continue"] = "continue"

    @field_validator("tool_name")
    @classmethod
    def tool_required_for_tool_kind(cls, v, info):
        if info.data.get("kind") == "tool" and not v:
            raise ValueError("tool_name required when kind=tool")
        return v


class DAGPlan(BaseModel):
    plan_id: str
    task_id: str
    session_id: str
    nodes: list[DAGNode]
    entry_node_ids: list[str]


def validate_dag(plan: DAGPlan) -> None:
    """静态校验：循环依赖 / 悬空引用 / entry 必须无 deps。"""
    node_ids = {n.node_id for n in plan.nodes}
    for entry in plan.entry_node_ids:
        if entry not in node_ids:
            raise DAGValidationError(f"entry {entry!r} not in nodes")
        node = next(n for n in plan.nodes if n.node_id == entry)
        if node.depends_on:
            raise DAGValidationError(
                f"entry node {entry} must have empty depends_on, got {node.depends_on}"
            )

    for node in plan.nodes:
        for dep in node.depends_on:
            if dep not in node_ids:
                raise DAGValidationError(
                    f"node {node.node_id} depends_on missing node {dep!r}"
                )

    # DFS 循环检测
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n.node_id: WHITE for n in plan.nodes}
    adj = {n.node_id: n.depends_on for n in plan.nodes}

    def dfs(u: str, stack: list[str]) -> None:
        color[u] = GRAY
        stack.append(u)
        for v in adj[u]:
            if color[v] == GRAY:
                cycle = stack[stack.index(v):] + [v]
                raise DAGValidationError(f"cycle detected: {' -> '.join(cycle)}")
            if color[v] == WHITE:
                dfs(v, stack)
        stack.pop()
        color[u] = BLACK

    for nid in list(color.keys()):
        if color[nid] == WHITE:
            dfs(nid, [])

    # branch 子树递归校验
    for node in plan.nodes:
        if node.kind == "branch":
            if node.true_branch is None and node.false_branch is None:
                raise DAGValidationError(
                    f"branch node {node.node_id} has no sub-branches"
                )