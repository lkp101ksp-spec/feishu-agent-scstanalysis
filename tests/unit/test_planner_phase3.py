from orchestrator.planner.dag_schema import DAGNode
from orchestrator.planner.planner import Planner


class FakeLLMRouter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, *, role, prompt, tools=None):
        self.calls.append((role, prompt, tools))
        return self.responses.pop(0)


def test_planner_parses_branch_node():
    fake = FakeLLMRouter([
        '{"intent": "if err then retry"}',
        """{"nodes": [
            {"node_id": "b1", "kind": "branch", "condition_prompt": "err?",
             "true_branch": [
               {"node_id": "b1a", "kind": "tool", "tool_name": "read_doc",
                "inputs": {"doc_id": "d"}, "depends_on": ["b1"]}
             ],
             "false_branch": [], "depends_on": []}
          ],
          "entry_node_ids": ["b1"]
        }"""
    ])
    p = Planner(llm_router=fake)
    plan = p.plan(message="如果出错则重试", session_id="s", task_id="t",
                 available_tools=["read_doc"], tools_schema=[])
    assert plan.nodes[0].kind == "branch"
    assert len(plan.nodes[0].true_branch) == 1
    assert plan.nodes[0].true_branch[0].node_id == "b1a"


def test_planner_parses_while_node():
    fake = FakeLLMRouter([
        '{"intent": "loop"}',
        """{"nodes": [
            {"node_id": "w1", "kind": "while",
             "while_condition_prompt": "keep going",
             "body": [
               {"node_id": "w1a", "kind": "tool", "tool_name": "x",
                "inputs": {}, "depends_on": ["w1"]}
             ],
             "max_iterations": 5, "depends_on": []}
          ],
          "entry_node_ids": ["w1"]
        }"""
    ])
    p = Planner(llm_router=fake)
    plan = p.plan(message="循环", session_id="s", task_id="t",
                 available_tools=["x"], tools_schema=[])
    assert plan.nodes[0].kind == "while"
    assert plan.nodes[0].max_iterations == 5


def test_planner_parses_for_node():
    fake = FakeLLMRouter([
        '{"intent": "for"}',
        """{"nodes": [
            {"node_id": "f1", "kind": "for",
             "iterate_over": "n1.files",
             "iteration_var": "file",
             "body": [
               {"node_id": "f1a", "kind": "tool", "tool_name": "read_doc",
                "inputs": {"doc_id": "file"}, "depends_on": ["f1"]}
             ],
             "depends_on": []}
          ],
          "entry_node_ids": ["f1"]
        }"""
    ])
    p = Planner(llm_router=fake)
    plan = p.plan(message="对每个文件", session_id="s", task_id="t",
                 available_tools=["read_doc"], tools_schema=[])
    assert plan.nodes[0].kind == "for"
    assert plan.nodes[0].iterate_over == "n1.files"