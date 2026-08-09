"""Planner：Intent → DAGPlan。

调用两次 LLM：
- LLM-call-A（轻量）：message → intent 字符串
- LLM-call-B（强推理）：prompt + tools schema → DAGPlan JSON
返回前调用 validate_dag()；失败抛 DAGValidationError。
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
            "请生成 DAGPlan JSON。节点 inputs 用 '<upstream_node_id>.<field>' 引用上游输出。"
            "entry_node_ids 必须是 depends_on=[] 的节点。"
        )

    def _build_plan(self, resp, *, task_id: str, session_id: str) -> DAGPlan:
        if isinstance(resp, dict):
            payload = resp
        else:
            payload = json.loads(resp)
        nodes = [DAGNode(**n) for n in payload["nodes"]]
        return DAGPlan(
            plan_id=new_ulid(),
            task_id=task_id,
            session_id=session_id,
            nodes=nodes,
            entry_node_ids=payload["entry_node_ids"],
        )