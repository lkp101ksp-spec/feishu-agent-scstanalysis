import pytest

from orchestrator.planner.dag_schema import DAGNode, DAGPlan, validate_dag
from shared.errors import DAGValidationError


def test_branch_node_minimal():
    n = DAGNode(
        node_id="b1", kind="branch",
        condition_prompt="if error then retry",
        true_branch=[],
        false_branch=[],
        depends_on=[],
    )
    assert n.kind == "branch"


def test_while_node_minimal():
    n = DAGNode(
        node_id="w1", kind="while",
        while_condition_prompt="keep looping",
        body=[], max_iterations=10, depends_on=[],
    )
    assert n.max_iterations == 10


def test_for_node_minimal():
    n = DAGNode(
        node_id="f1", kind="for",
        iterate_over="n1.files",
        iteration_var="file",
        body=[], max_iterations=100, depends_on=[],
    )
    assert n.iterate_over == "n1.files"


def test_branch_with_subtree_validates():
    sub = DAGNode(node_id="b1a", kind="tool", tool_name="read_doc",
                  inputs={"doc_id": "d1"}, depends_on=["b1"])
    n = DAGNode(node_id="b1", kind="branch",
                condition_prompt="x",
                true_branch=[sub],
                false_branch=[],
                depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n], entry_node_ids=["b1"])
    validate_dag(plan)


def test_branch_with_cyclic_subtree_fails():
    sub1 = DAGNode(node_id="sub1", kind="tool", tool_name="a",
                   inputs={}, depends_on=["sub2"])
    sub2 = DAGNode(node_id="sub2", kind="tool", tool_name="b",
                   inputs={}, depends_on=["sub1"])
    n = DAGNode(node_id="b1", kind="branch", condition_prompt="x",
                true_branch=[sub1, sub2], false_branch=[], depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n], entry_node_ids=["b1"])
    with pytest.raises(DAGValidationError, match="cycle"):
        validate_dag(plan)


def test_branch_empty_subtrees_fails():
    n = DAGNode(node_id="b1", kind="branch", condition_prompt="x",
                true_branch=None, false_branch=None, depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n], entry_node_ids=["b1"])
    with pytest.raises(DAGValidationError, match="no sub-branches"):
        validate_dag(plan)


def test_while_nested_in_while_fails():
    inner = DAGNode(node_id="inner_w", kind="while",
                    while_condition_prompt="x", body=[], depends_on=[])
    outer = DAGNode(node_id="outer_w", kind="while",
                    while_condition_prompt="x", body=[inner], depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[outer], entry_node_ids=["outer_w"])
    with pytest.raises(DAGValidationError, match="nested while/for"):
        validate_dag(plan)