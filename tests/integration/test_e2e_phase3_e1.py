"""E1: branch 节点 condition_eval=true 时动态追加重试 read_doc（简化版）。"""
from orchestrator.planner.dag_schema import DAGNode
from orchestrator.runtime.plan_runtime import PlanRuntime
from shared.schemas import ChatMessage


def test_e1_branch_dynamic_append_simple():
    """PlanRuntime.append_dynamic_nodes 能接受合法子树并写入 state。"""
    rt = PlanRuntime(state_repo=None, audit_repo=None)
    sub = DAGNode(node_id="b1a", kind="tool", tool_name="read_doc",
                  inputs={"doc_id": "d2"}, depends_on=["b1"])
    rt.append_dynamic_nodes(plan_id="p", parent_node_id="b1", new_nodes=[sub])
    assert len(rt._dynamic_nodes) == 1
    assert rt._dynamic_nodes[0]["parent_node_id"] == "b1"
    assert rt._dynamic_nodes[0]["node_ids"] == ["b1a"]


def test_e1_branch_subtree_siblings_validates():
    """branch 内多个 sibling 子树节点互相依赖能被校验。"""
    from orchestrator.planner.dag_schema import validate_dag, DAGPlan
    from orchestrator.planner.dag_schema import DAGPlan
    sub1 = DAGNode(node_id="b1a", kind="tool", tool_name="read_doc",
                   inputs={"doc_id": "d"}, depends_on=[])
    sub2 = DAGNode(node_id="b1b", kind="tool", tool_name="read_doc",
                   inputs={"doc_id": "d"}, depends_on=["b1a"])
    b = DAGNode(node_id="b1", kind="branch", condition_prompt="x",
                true_branch=[sub1, sub2], false_branch=[], depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[b], entry_node_ids=["b1"])
    validate_dag(plan)  # 不抛