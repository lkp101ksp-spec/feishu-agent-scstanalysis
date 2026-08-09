from orchestrator.template_engine import BlockSpec, TemplateEngine


def test_render_small_text():
    engine = TemplateEngine()
    blocks = engine.render_text("hello world")
    assert blocks == [BlockSpec(block_type="text", content={"text": "hello world"})]


def test_render_large_text_uses_callout():
    engine = TemplateEngine()
    big = "x" * 3000
    blocks = engine.render_text(big)
    assert blocks[0].block_type == "callout"
    assert big in blocks[0].content["text"]


def test_render_table_small():
    engine = TemplateEngine()
    rows = [[1, 2], [3, 4]]
    blocks = engine.render_table(headers=["a", "b"], rows=rows)
    assert blocks[0].block_type == "table"


def test_render_table_large_uses_summary_and_file():
    engine = TemplateEngine()
    rows = [[i, i + 1] for i in range(20)]
    blocks = engine.render_table(headers=["a", "b"], rows=rows)
    types = [b.block_type for b in blocks]
    assert "callout" in types
    assert "file" in types


def test_render_image():
    engine = TemplateEngine()
    blocks = engine.render_image(file_token="boxcn_xxx", alt="图1")
    assert blocks[0].block_type == "image"
    assert blocks[0].content["file_token"] == "boxcn_xxx"


def test_render_code_inferred_python():
    engine = TemplateEngine()
    blocks = engine.render_code("print(1)", language="inferred")
    assert blocks[0].block_type == "code_block"
    assert blocks[0].content["language"] == "python"


def test_render_error():
    engine = TemplateEngine()
    blocks = engine.render_error("出错了")
    assert blocks[0].block_type == "callout"
    assert "出错了" in blocks[0].content["text"]