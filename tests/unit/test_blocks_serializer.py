import pytest

from orchestrator.blocks.schemas import (
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    QuoteBlock,
    TableBlock,
    TextBlock,
)
from orchestrator.blocks.serializer import (
    blocks_to_json,
    json_to_blocks,
    parse_blocks,
)


def test_blocks_to_json_roundtrip():
    blocks = [
        HeadingBlock(level=2, text="Hi"),
        TextBlock(text="hello"),
        CodeBlock(language="python", text="x=1"),
        QuoteBlock(text="..."),
        TableBlock(headers=["A"], rows=[["1"]]),
        ListBlock(items=["x"]),
        ImageBlock(url="https://example.com/x.png"),
    ]
    s = blocks_to_json(blocks)
    out = json_to_blocks(s)
    assert len(out) == 7
    assert out[0].text == "Hi"
    assert out[6].url == "https://example.com/x.png"


def test_blocks_to_json_empty_list():
    s = blocks_to_json([])
    assert s == "[]"


def test_json_to_blocks_invalid_type_raises():
    import json
    bad = json.dumps([{"type": "unknown", "x": 1}])
    with pytest.raises(ValueError):
        json_to_blocks(bad)


# === parse_blocks（宽松解析，write_doc 节点路径） ===

def test_parse_blocks_repr_string():
    """单引号 repr 串（planner str() 强转产物）→ Block 列表。"""
    out = parse_blocks("[{'type': 'text', 'text': 'hi'}]")
    assert out == [TextBlock(text="hi")]


def test_parse_blocks_json_string():
    out = parse_blocks('[{"type": "text", "text": "hi"}]')
    assert out == [TextBlock(text="hi")]


def test_parse_blocks_list_of_dict():
    out = parse_blocks([{"type": "heading", "level": 1, "text": "T"}])
    assert out == [HeadingBlock(level=1, text="T")]


def test_parse_blocks_single_dict_wrapped():
    out = parse_blocks({"type": "text", "text": "solo"})
    assert out == [TextBlock(text="solo")]


def test_parse_blocks_empty_string_returns_empty():
    assert parse_blocks("") == []
    assert parse_blocks("   ") == []


def test_parse_blocks_garbage_raises():
    with pytest.raises(Exception):
        parse_blocks("not-a-blocks-literal")
