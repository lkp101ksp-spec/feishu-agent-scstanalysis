from orchestrator.templates.renderer import render_subplan, substitute
from orchestrator.templates.schemas import SubPlanTemplateStep


def test_substitute_string():
    out = substitute("hello {{name}}", {"name": "world"})
    assert out == "hello world"


def test_substitute_missing_var_keeps_placeholder():
    out = substitute("hello {{name}}", {})
    assert out == "hello {{name}}"


def test_substitute_nested_dict_list():
    obj = {"a": "{{x}}", "b": ["{{y}}", "literal"], "c": {"d": "{{z}}"}}
    out = substitute(obj, {"x": "1", "y": "2", "z": "3"})
    assert out == {"a": "1", "b": ["2", "literal"], "c": {"d": "3"}}


def test_render_subplan_substitutes_step_inputs():
    steps = [SubPlanTemplateStep(
        step_id="s1", tool_name="blast_search",
        inputs={"query": "{{gene}}", "database": "nr"},
    )]
    out = render_subplan(steps, params={"gene": "BRCA1"})
    assert out[0].inputs["query"] == "BRCA1"
    assert out[0].inputs["database"] == "nr"
