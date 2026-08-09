import pytest

from orchestrator.planner.dag_schema import DAGNode, DAGPlan, validate_dag
from shared.errors import DAGValidationError


def test_dagplan_minimal_roundtrip():
    plan = DAGPlan(
        plan_id="p1",
        task_id="t1",
        session_id="s1",
        nodes=[
            DAGNode(node_id="n1", kind="tool", tool_name="run_python",
                     inputs={"code": "1+1"}, depends_on=[]),
            DAGNode(node_id="n2", kind="tool", tool_name="summarize_text",
                     inputs={"text": "n1.result"}, depends_on=["n1"]),
        ],
        entry_node_ids=["n1"],
    )
    payload = plan.model_dump()
    rebuilt = DAGPlan.model_validate(payload)
    assert rebuilt.plan_id == "p1"


def test_dag_validation_cyclic_fails():
    # 用一个 entry 节点 + 一个被环引用的节点来制造真正环
    n_entry = DAGNode(node_id="n_entry", kind="tool", tool_name="a",
                      inputs={}, depends_on=[])
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="a",
                 inputs={}, depends_on=["n2"])
    n2 = DAGNode(node_id="n2", kind="tool", tool_name="b",
                 inputs={}, depends_on=["n1"])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n_entry, n1, n2], entry_node_ids=["n_entry"])
    with pytest.raises(DAGValidationError, match="cycle"):
        validate_dag(plan)


def test_dag_validation_dangling_reference():
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="a",
                 inputs={}, depends_on=["nx"])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1], entry_node_ids=["n1"])
    with pytest.raises(DAGValidationError, match="nx"):
        validate_dag(plan)


def test_dag_validation_entry_must_have_no_deps():
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="a",
                 inputs={}, depends_on=["n2"])
    n2 = DAGNode(node_id="n2", kind="tool", tool_name="b",
                 inputs={}, depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, n2], entry_node_ids=["n1"])
    with pytest.raises(DAGValidationError, match="entry"):
        validate_dag(plan)