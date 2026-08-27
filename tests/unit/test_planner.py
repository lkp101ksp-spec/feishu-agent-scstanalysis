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
