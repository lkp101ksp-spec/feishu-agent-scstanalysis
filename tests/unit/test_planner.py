import pytest

from orchestrator.planner.planner import Planner
from shared.errors import DAGValidationError


class FakeLLMRouter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, *, role, prompt, tools=None):
        self.calls.append((role, prompt, tools))
        return self.responses.pop(0)


def test_planner_plan_minimal():
    fake = FakeLLMRouter(
        [
            '{"intent": "summarize"}',
            """{"nodes": [
                {"node_id": "n1", "kind": "tool", "tool_name": "read_doc",
                 "inputs": {"doc_id": "placeholder"}, "depends_on": []},
                {"node_id": "n2", "kind": "tool", "tool_name": "summarize_text",
                 "inputs": {"text": "n1.blocks"}, "depends_on": ["n1"]}
              ],
              "entry_node_ids": ["n1"]
            }""",
        ]
    )
    p = Planner(llm_router=fake)
    plan = p.plan(
        message="读 doc 并总结",
        session_id="s1",
        task_id="t1",
        available_tools=["read_doc", "summarize_text"],
        tools_schema=[],
    )
    assert len(plan.nodes) == 2
    assert plan.entry_node_ids == ["n1"]


def test_planner_prompt_inlines_tools_schema():
    """Phase 12 板块③：schema 内联进 DAG prompt（tools 形参不进请求体）。"""
    fake = FakeLLMRouter(
        [
            '{"intent": "summarize"}',
            """{"nodes": [
                {"node_id": "n1", "kind": "tool", "tool_name": "read_doc",
                 "inputs": {"doc_id": "d"}, "depends_on": []}
              ],
              "entry_node_ids": ["n1"]
            }""",
        ]
    )
    schema = [{
        "type": "function",
        "function": {
            "name": "read_doc",
            "description": "读取飞书 doc 块树",
            "parameters": {"type": "object",
                           "properties": {"doc_id": {"type": "string"}}},
        },
    }]
    Planner(llm_router=fake).plan(
        message="读文档", session_id="s", task_id="t",
        available_tools=["read_doc"], tools_schema=schema,
    )
    dag_call = next(c for c in fake.calls if c[0] == "dag_builder")
    prompt = dag_call[1]
    # schema 关键内容进 prompt
    assert "read_doc" in prompt
    assert "doc_id" in prompt
    assert "parameters" in prompt
    # 只输出 JSON 的约束
    assert "只输出一个 JSON" in prompt


def test_extract_json_object_tolerates_fences():
    """Phase 12 真机修正：容忍 markdown 围栏与夹带说明文字。"""
    from orchestrator.planner.planner import _extract_json_object

    ok = '{"nodes": [], "entry_node_ids": []}'
    assert _extract_json_object({"a": 1}) == {"a": 1}
    assert _extract_json_object(ok) == {"nodes": [], "entry_node_ids": []}
    assert _extract_json_object(f"```json\n{ok}\n```") == {
        "nodes": [], "entry_node_ids": []}
    assert _extract_json_object(f"好的，这是结果：\n{ok}\n以上。") == {
        "nodes": [], "entry_node_ids": []}


def test_planner_tolerates_missing_kind_and_entry():
    """Phase 12 真机（2026-08-30）：节点缺 kind 默认 tool；entry 缺失自动推导。"""
    fake = FakeLLMRouter([
        '{"intent": "x"}',
        """{"nodes": [
            {"node_id": "n1", "tool_name": "read_doc",
             "inputs": {"doc_id": "d"}, "depends_on": []},
            {"node_id": "n2", "type": "tool", "tool_name": "summarize_text",
             "inputs": {"text": "n1.blocks"}, "depends_on": ["n1"]}
          ]}""",
    ])
    plan = Planner(llm_router=fake).plan(
        message="m", session_id="s", task_id="t",
        available_tools=["read_doc", "summarize_text"], tools_schema=[],
    )
    assert all(n.kind == "tool" for n in plan.nodes)
    assert plan.entry_node_ids == ["n1"]


def test_planner_retry_feeds_error_back():
    """重试 prompt 附带上次解析错误反馈。"""
    bad = "这不是 JSON"
    good = """{"nodes": [
        {"node_id": "n1", "kind": "tool", "tool_name": "a",
         "inputs": {}, "depends_on": []}
      ],
      "entry_node_ids": ["n1"]
    }"""
    fake = FakeLLMRouter(['{"intent": "x"}', bad, good])
    Planner(llm_router=fake, max_retries=1).plan(
        message="m", session_id="s", task_id="t",
        available_tools=["a"], tools_schema=[],
    )
    # 第 2 次 dag 调用的 prompt 含错误反馈
    dag_prompts = [c[1] for c in fake.calls if c[0] == "dag_builder"]
    assert len(dag_prompts) == 2
    assert "上一次输出无效" in dag_prompts[1]


def test_planner_plan_validates_dag():
    fake = FakeLLMRouter(
        [
            '{"intent": "x"}',
            """{"nodes": [
                {"node_id": "n_entry", "kind": "tool", "tool_name": "a",
                 "inputs": {}, "depends_on": []},
                {"node_id": "n1", "kind": "tool", "tool_name": "a",
                 "inputs": {}, "depends_on": ["n2"]},
                {"node_id": "n2", "kind": "tool", "tool_name": "b",
                 "inputs": {}, "depends_on": ["n1"]}
              ],
              "entry_node_ids": ["n_entry"]
            }""",
        ]
    )
    # max_retries=0 → 只尝试 1 次（不重试）
    p = Planner(llm_router=fake, max_retries=0)
    with pytest.raises(DAGValidationError):
        p.plan(
            message="m",
            session_id="s1",
            task_id="t1",
            available_tools=["a", "b"],
            tools_schema=[],
        )


def test_planner_injects_plan_id_and_task_id():
    fake = FakeLLMRouter(
        [
            '{"intent": "x"}',
            """{"nodes": [{"node_id":"n1","kind":"tool","tool_name":"read_doc",
                 "inputs":{"doc_id":"d"},"depends_on":[]}],
              "entry_node_ids":["n1"]}""",
        ]
    )
    p = Planner(llm_router=fake)
    plan = p.plan(
        message="m",
        session_id="s1",
        task_id="t_xyz",
        available_tools=["read_doc"],
        tools_schema=[],
    )
    assert plan.task_id == "t_xyz"
    assert plan.session_id == "s1"
    assert len(plan.plan_id) > 0
