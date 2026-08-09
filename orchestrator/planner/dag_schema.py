"""DAG 节点 / Plan Pydantic 模型 + 静态校验。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from shared.errors import DAGValidationError


class DAGNode(BaseModel):
    node_id: str
    kind: Literal["tool", "llm", "branch", "while", "for", "join"]
    tool_name: Optional[str] = None
    inputs: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    condition: Optional[str] = None
    true_branch: Optional[list["DAGNode"]] = None
    false_branch: Optional[list["DAGNode"]] = None
    join_strategy: Optional[Literal["all", "any", "first"]] = None
    on_node_fail: Literal["stop_plan", "continue"] = "continue"
    # === Phase 3 ===
    condition_prompt: Optional[str] = None
    while_condition_prompt: Optional[str] = None
    body: Optional[list["DAGNode"]] = None
    max_iterations: int = 10
    iterate_over: Optional[str] = None
    iteration_var: str = "item"
    # === Phase 5 ===
    subplan_template_id: Optional[str] = None
    subplan_params: dict[str, str] = Field(default_factory=dict)

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
            for sub in (node.true_branch or []) + (node.false_branch or []):
                siblings = (node.true_branch or []) + (node.false_branch or [])
                _validate_subtree(sub, parent_id=node.node_id, all_nodes=plan.nodes,
                                   siblings=siblings)
        elif node.kind in {"while", "for"}:
            if not node.body:
                raise DAGValidationError(
                    f"{node.kind} node {node.node_id} has empty body"
                )
            for sub in node.body:
                if sub.kind in {"while", "for"}:
                    raise DAGValidationError(
                        f"nested while/for not allowed in Phase 3: "
                        f"{sub.kind} inside {node.kind} {node.node_id}"
                    )
                _validate_subtree(sub, parent_id=node.node_id, all_nodes=plan.nodes,
                                   siblings=list(node.body))


def _validate_subtree(sub: "DAGNode", *, parent_id: str, all_nodes: list[DAGNode],
                       siblings: list["DAGNode"] | None = None) -> None:
    """校验嵌套子树：依赖、循环。

    siblings：同子树内其他节点（用于解析子树内相互引用）。
    全局 DFS 整个 siblings 集合以检测跨节点环。
    """
    pool = list(siblings) if siblings else [sub]
    pool_by_id = {n.node_id: n for n in pool}
    ids = {n.node_id for n in all_nodes} | {parent_id} | set(pool_by_id.keys())
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n.node_id: WHITE for n in pool}

    def dfs(u: "DAGNode", stack: list[str]) -> None:
        color[u.node_id] = GRAY
        stack.append(u.node_id)
        for dep in u.depends_on:
            if dep not in ids:
                raise DAGValidationError(
                    f"subtree node {u.node_id} depends_on missing {dep!r}"
                )
            if dep == parent_id:
                continue  # 隐式依赖 parent，跳过
            if color.get(dep) == GRAY:
                raise DAGValidationError(
                    f"cycle in subtree: {' -> '.join(stack + [dep])}"
                )
            if dep in pool_by_id and color[dep] == WHITE:
                dfs(pool_by_id[dep], stack)
        stack.pop()
        color[u.node_id] = BLACK

    for node in pool:
        if color[node.node_id] == WHITE:
            dfs(node, [])