from orchestrator.tools.builtin.l3_bio import register_l3_bio
from orchestrator.tools.tool_registry import ToolRegistry


def test_register_l3_bio_adds_blast_search():
    reg = ToolRegistry()
    register_l3_bio(reg)
    spec = reg.get("blast_search")
    assert spec.name == "blast_search"
    assert spec.risk_level == "L0_read"
    assert "query" in spec.parameters["properties"]


def test_blast_search_handler_is_callable():
    reg = ToolRegistry()
    register_l3_bio(reg)
    spec = reg.get("blast_search")
    assert callable(spec.handler)
