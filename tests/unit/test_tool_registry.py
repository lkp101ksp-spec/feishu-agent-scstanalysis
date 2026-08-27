
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec
from shared.errors import ToolNotFoundError


def test_tool_spec_minimal():
    spec = ToolSpec(
        name="read_doc",
        description="读取飞书 doc",
        parameters={
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
        },
        risk_level="L0_read",
        handler=lambda doc_id: None,
    )
    assert spec.timeout_sec == 60  # default
    assert spec.tool_version == "1.0.0"


def test_registry_register_and_get():
    reg = ToolRegistry()
    spec = ToolSpec(
        name="a",
        description="d",
        parameters={"type": "object"},
        risk_level="L0_read",
        handler=lambda: None,
    )
    reg.register(spec)
    assert reg.get("a").name == "a"


def test_registry_get_unknown_raises():
    reg = ToolRegistry()
    try:
        reg.get("nope")
        assert False, "should raise ToolNotFoundError"
    except ToolNotFoundError:
        pass


def test_registry_to_openai_functions_filters_L2_by_default():
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="r",
            description="r",
            parameters={"type": "object"},
            risk_level="L0_read",
            handler=lambda: None,
        )
    )
    reg.register(
        ToolSpec(
            name="w",
            description="w",
            parameters={"type": "object"},
            risk_level="L2_side_effect",
            handler=lambda: None,
        )
    )
    funcs = reg.to_openai_functions(include_L2=False)
    names = [f["function"]["name"] for f in funcs]
    assert names == ["r"]
    funcs_all = reg.to_openai_functions(include_L2=True)
    assert sorted([f["function"]["name"] for f in funcs_all]) == ["r", "w"]
