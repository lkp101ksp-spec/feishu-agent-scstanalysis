from orchestrator.blocks.schemas import AnyBlock, HeadingBlock, TableBlock, TextBlock
from orchestrator.template_engine import TemplateEngine


def test_render_plan_summary_blocks_returns_blocks():
    eng = TemplateEngine()
    blocks = eng.render_plan_summary_blocks(
        status="success",
        node_states={"n1": "success", "n2": "failed"},
        artifacts_count=2,
    )
    assert len(blocks) > 0
    assert all(isinstance(b, AnyBlock) for b in blocks)


def test_render_plan_summary_blocks_includes_status_table():
    eng = TemplateEngine()
    blocks = eng.render_plan_summary_blocks(
        status="success",
        node_states={"n1": "success"},
        artifacts_count=0,
    )
    types = [b.type for b in blocks]
    assert "table" in types
    assert "heading" in types


def test_render_blocks_to_text():
    eng = TemplateEngine()
    blocks = [
        HeadingBlock(level=2, text="Hi"),
        TextBlock(text="hello"),
    ]
    text = eng.render_blocks_to_text(blocks)
    assert "## Hi" in text
    assert "hello" in text