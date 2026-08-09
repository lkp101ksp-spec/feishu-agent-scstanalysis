"""Planner：Intent → DAGPlan。

调用两次 LLM：
- LLM-call-A（轻量）：message → intent 字符串
- LLM-call-B（强推理）：prompt + tools schema → DAGPlan JSON
返回前调用 validate_dag()；失败抛 DAGValidationError。

Phase 3 升级：DAGNode 支持 branch/while/for 嵌套子树，递归构造。
"""
from __future__ import annotations

import json

from orchestrator.planner.dag_schema import DAGNode, DAGPlan, validate_dag
from shared.errors import DAGValidationError
from shared.ulid_ import new_ulid


class Planner:
    def __init__(
        self,
        llm_router,
        *,
        role_intent: str = "intent_parser",
        role_dag: str = "dag_builder",
        max_retries: int = 1,
    ) -> None:
        self.llm_router = llm_router
        self.role_intent = role_intent
        self.role_dag = role_dag
        self.max_retries = max_retries

    def plan(
        self,
        *,
        message: str,
        session_id: str,
        task_id: str,
        available_tools: list,
        tools_schema: list,
    ) -> DAGPlan:
        intent_resp = self.llm_router.call(
            role=self.role_intent, prompt=f"intent:\n{message}"
        )
        intent = self._parse_intent(intent_resp)

        prompt = self._build_dag_prompt(
            message=message,
            intent=intent,
            available_tools=available_tools,
            tools_schema=tools_schema,
        )

        last_err = None
        for attempt in range(self.max_retries + 1):
            dag_resp = self.llm_router.call(
                role=self.role_dag, prompt=prompt, tools=tools_schema
            )
            try:
                plan = self._build_plan(
                    dag_resp, task_id=task_id, session_id=session_id
                )
                validate_dag(plan)
                return plan
            except (DAGValidationError, json.JSONDecodeError, KeyError) as e:
                last_err = e
                continue
        raise DAGValidationError(f"planner failed after retries: {last_err}")

    def _parse_intent(self, resp):
        if isinstance(resp, dict):
            return resp.get("intent", "unknown")
        try:
            return json.loads(resp).get("intent", "unknown")
        except (json.JSONDecodeError, TypeError):
            return "unknown"

    def _build_dag_prompt(
        self,
        *,
        message: str,
        intent: str,
        available_tools: list,
        tools_schema: list,
    ) -> str:
        tool_names = ", ".join(available_tools)
        return (
            f"用户消息：{message}\n"
            f"intent：{intent}\n"
            f"可用工具：{tool_names}\n\n"
            "你可以生成 tool/branch/while/for 节点。"
            "branch.condition_prompt 是自然语言条件；true_branch/false_branch 是嵌套 DAGNode 数组。"
            "while.while_condition_prompt 是循环条件；body 是嵌套 DAGNode 数组；max_iterations 默认10。"
            "for.iterate_over 是上游 outputs 字段（<node_id>.<field>）；body 嵌套；max_iterations 默认100。"
            "节点 inputs 用 '<upstream_node_id>.<field>' 引用上游输出。"
            "entry_node_ids 必须是 depends_on=[] 的节点。"
        )

    def _build_plan(self, resp, *, task_id: str, session_id: str) -> DAGPlan:
        if isinstance(resp, dict):
            payload = resp
        else:
            payload = json.loads(resp)
        nodes = [_build_node(n) for n in payload["nodes"]]
        return DAGPlan(
            plan_id=new_ulid(),
            task_id=task_id,
            session_id=session_id,
            nodes=nodes,
            entry_node_ids=payload["entry_node_ids"],
        )


def _build_node(payload: dict) -> DAGNode:
    """递归构造嵌套 DAGNode（Phase 3）。"""
    kwargs = dict(
        node_id=payload["node_id"],
        kind=payload["kind"],
        tool_name=payload.get("tool_name"),
        inputs=payload.get("inputs", {}),
        depends_on=payload.get("depends_on", []),
        config=payload.get("config", {}),
        condition=payload.get("condition"),
        join_strategy=payload.get("join_strategy"),
        on_node_fail=payload.get("on_node_fail", "continue"),
    )
    if payload["kind"] == "branch":
        kwargs["condition_prompt"] = payload.get("condition_prompt")
        kwargs["true_branch"] = [_build_node(n) for n in payload.get("true_branch", [])]
        kwargs["false_branch"] = [_build_node(n) for n in payload.get("false_branch", [])]
    elif payload["kind"] == "while":
        kwargs["while_condition_prompt"] = payload.get("while_condition_prompt")
        kwargs["body"] = [_build_node(n) for n in payload.get("body", [])]
        kwargs["max_iterations"] = payload.get("max_iterations", 10)
    elif payload["kind"] == "for":
        kwargs["iterate_over"] = payload.get("iterate_over")
        kwargs["iteration_var"] = payload.get("iteration_var", "item")
        kwargs["body"] = [_build_node(n) for n in payload.get("body", [])]
        kwargs["max_iterations"] = payload.get("max_iterations", 100)
    return DAGNode(**kwargs)