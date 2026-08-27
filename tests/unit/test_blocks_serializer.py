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
from orchestrator.blocks.serializer import blocks_to_json, json_to_blocks


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
