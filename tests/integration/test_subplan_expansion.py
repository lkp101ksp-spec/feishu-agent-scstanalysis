from unittest.mock import MagicMock

from orchestrator.planner.dag_schema import DAGNode
from orchestrator.planner.scheduler import Scheduler
from orchestrator.templates.schemas import SubPlanTemplateStep


def test_scheduler_expand_subplan():
    template_service = MagicMock()
    template_service.get.return_value = MagicMock(
        template_id="t1", type="subplan", archived_at=None,
        steps_json='[{"step_id": "s1", "tool_name": "blast_search",'
                  ' "inputs": {"query": "{{gene}}"}},'
                  ' {"step_id": "s2", "tool_name": "summary",'
                  ' "inputs": {"input": "n1.records"}}]',
    )
    template_service.render_subplan.return_value = [
        SubPlanTemplateStep(step_id="s1", tool_name="blast_search",
                             inputs={"query": "BRCA1"}),
        SubPlanTemplateStep(step_id="s2", tool_name="summary",
                             inputs={"input": "n1.records"}),
    ]
    n = DAGNode(node_id="n1", kind="tool", tool_name="tpl_use",
                inputs={},
                subplan_template_id="t1",
                subplan_params={"gene": "BRCA1"})

    expanded = Scheduler._expand_subplan_static(n, template_service)
    assert len(expanded) == 2
    assert expanded[0].tool_name == "blast_search"
    assert expanded[0].inputs["query"] == "BRCA1"


def test_scheduler_no_subplan_returns_self():
    template_service = MagicMock()
    n = DAGNode(node_id="n1", kind="tool", tool_name="x",
                inputs={"q": "y"})
    out = Scheduler._expand_subplan_static(n, template_service)
    assert len(out) == 1
    assert out[0].node_id == "n1"


def test_scheduler_subplan_not_found_raises():
    from unittest.mock import MagicMock
    template_service = MagicMock()
    template_service.get.return_value = None
    n = DAGNode(node_id="n1", kind="tool", tool_name="x",
                inputs={}, subplan_template_id="t_missing")
    import pytest
    with pytest.raises(ValueError):
        Scheduler._expand_subplan_static(n, template_service)