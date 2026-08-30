"""Planner：Intent → DAGPlan。

调用两次 LLM：
- LLM-call-A（轻量）：message → intent 字符串
- LLM-call-B（强推理）：prompt + tools schema → DAGPlan JSON
返回前调用 validate_dag()；失败抛 DAGValidationError。

Phase 3 升级：DAGNode 支持 branch/while/for 嵌套子树，递归构造。
Phase 12 修正：模型常把 JSON 包进 markdown 代码围栏或夹带说明文字，
_build_plan 前先剥围栏并截取最外层 {...}（真机 2026-08-29 发现）。
"""
from __future__ import annotations

import json
import re

from orchestrator.planner.dag_schema import DAGNode, DAGPlan, validate_dag
from shared.errors import DAGValidationError
from shared.ulid_ import new_ulid

_CODE_FENCE_RE = re.compile(r"^```[\w-]*\s*|\s*```$")


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
        session_context: str = "",
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
            session_context=session_context,
        )

        base_prompt = prompt
        last_err = None
        for attempt in range(self.max_retries + 1):
            dag_resp = self.llm_router.call(
                role=self.role_dag, prompt=prompt
            )
            try:
                plan = self._build_plan(
                    dag_resp, task_id=task_id, session_id=session_id
                )
                validate_dag(plan)
                return plan
            except (DAGValidationError, json.JSONDecodeError, KeyError) as e:
                last_err = e
                # 重试附错误反馈，引导模型修正格式（Phase 12 真机改进）
                prompt = (
                    f"{base_prompt}\n\n【上一次输出无效：{e}】"
                    "请重新输出，只输出一个合法 JSON 对象。"
                )
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
        session_context: str = "",
    ) -> str:
        """构建 DAG 生成 prompt；工具 schema 全量内联（Phase 12 板块③）。

        LLMRouter 的 tools 形参不进请求体，schema 必须内联进 prompt 才能到
        模型侧——否则模型只看到工具名，inputs 字段只能靠猜。
        session_context（Phase 12 真机 2026-08-30）：注入绑定文档等运行时
        上下文——模型无从得知 doc_id 等会话状态，不注入则只能编占位符。
        """
        tool_names = ", ".join(available_tools)
        context_block = (
            f"会话上下文（引用其中 id 时必须原样使用，不得编造）：\n"
            f"{session_context}\n\n" if session_context else ""
        )
        return (
            f"用户消息：{message}\n"
            f"intent：{intent}\n"
            f"{context_block}"
            f"可用工具：{tool_names}\n\n"
            f"工具契约（inputs 键必须严格取自对应工具的 parameters.properties，"
            f"不得发明字段）：\n{json.dumps(tools_schema, ensure_ascii=False)}\n\n"
            "输出格式（严格遵循，只输出一个 JSON 对象，不要 markdown 围栏）：\n"
            '{"nodes": [{"node_id": "n1", "kind": "tool", '
            '"tool_name": "<工具名>", "inputs": {…}, "depends_on": []}, …],\n'
            ' "entry_node_ids": ["n1"]}\n'
            "每个节点必须含 node_id、kind（只能取 tool/branch/while/for）、"
            "tool_name（kind=tool 时必填）。\n"
            "branch.condition_prompt 是自然语言条件；true_branch/false_branch 是嵌套节点数组。"
            "while.while_condition_prompt 是循环条件；body 是嵌套节点数组；max_iterations 默认10。"
            "for.iterate_over 是上游 outputs 字段（<node_id>.<field>）；body 嵌套；max_iterations 默认100。"
            "节点 inputs 用 '<upstream_node_id>.<field>' 引用上游输出。"
            "entry_node_ids 必须是 depends_on=[] 的节点。"
        )

    def _build_plan(self, resp, *, task_id: str, session_id: str) -> DAGPlan:
        payload = _extract_json_object(resp)
        nodes = [_build_node(n) for n in payload["nodes"]]
        # entry 缺失时自动推导：depends_on 为空的节点即入口（容错，真机 2026-08-30）
        entry = payload.get("entry_node_ids") or [
            n.node_id for n in nodes if not n.depends_on
        ]
        if not entry:
            raise KeyError("entry_node_ids")
        return DAGPlan(
            plan_id=new_ulid(),
            task_id=task_id,
            session_id=session_id,
            nodes=nodes,
            entry_node_ids=entry,
        )


def _extract_json_object(resp) -> dict:
    """从 LLM 响应提取 JSON 对象：容忍 dict 直传、markdown 围栏、夹带说明文字。

    失败抛 json.JSONDecodeError（由 plan() 的重试循环捕获）。
    """
    if isinstance(resp, dict):
        return resp
    text = str(resp).strip()
    # 剥 ```json ... ``` 围栏
    if text.startswith("```"):
        text = _CODE_FENCE_RE.sub("", text).strip()
    # 夹带说明文字时截取最外层 {...}
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


def _build_node(payload: dict) -> DAGNode:
    """递归构造嵌套 DAGNode（Phase 3）。

    容错（Phase 12 真机 2026-08-30）：
    - kind 缺省/写成 type 视为 tool（纯 tool 节点模型常省略 kind）
    - inputs 值统一 str() 强转——DAGNode.inputs 契约为 dict[str,str]，
      模型常把 max_words 等数值输出成 int
    """
    kind = payload.get("kind") or payload.get("type") or "tool"
    kwargs = dict(
        node_id=payload["node_id"],
        kind=kind,
        tool_name=payload.get("tool_name"),
        inputs={k: str(v) for k, v in payload.get("inputs", {}).items()},
        depends_on=payload.get("depends_on", []),
        config=payload.get("config", {}),
        condition=payload.get("condition"),
        join_strategy=payload.get("join_strategy"),
        on_node_fail=payload.get("on_node_fail", "continue"),
    )
    if kind == "branch":
        kwargs["condition_prompt"] = payload.get("condition_prompt")
        kwargs["true_branch"] = [_build_node(n) for n in payload.get("true_branch", [])]
        kwargs["false_branch"] = [_build_node(n) for n in payload.get("false_branch", [])]
    elif kind == "while":
        kwargs["while_condition_prompt"] = payload.get("while_condition_prompt")
        kwargs["body"] = [_build_node(n) for n in payload.get("body", [])]
        kwargs["max_iterations"] = payload.get("max_iterations", 10)
    elif kind == "for":
        kwargs["iterate_over"] = payload.get("iterate_over")
        kwargs["iteration_var"] = payload.get("iteration_var", "item")
        kwargs["body"] = [_build_node(n) for n in payload.get("body", [])]
        kwargs["max_iterations"] = payload.get("max_iterations", 100)
    return DAGNode(**kwargs)
