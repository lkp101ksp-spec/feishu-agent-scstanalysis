import pytest
from pydantic import ValidationError

from orchestrator.blocks.schemas import (
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    QuoteBlock,
    TableBlock,
    TextBlock,
)


def test_heading_block_validates_level():
    h = HeadingBlock(level=2, text="Hi")
    assert h.level == 2
    assert h.text == "Hi"


def test_heading_block_rejects_level_out_of_range():
    with pytest.raises(ValidationError):
        HeadingBlock(level=4, text="x")


def test_text_block_minimal():
    t = TextBlock(text="hello")
    assert t.type == "text"


def test_code_block_minimal():
    c = CodeBlock(language="python", text="x=1")
    assert c.type == "code"


def test_quote_block_minimal():
    q = QuoteBlock(text="...")
    assert q.type == "quote"


def test_table_block_validates_row_columns():
    t = TableBlock(
        headers=["A", "B"],
        rows=[["1", "2"], ["3", "4"]],
    )
    assert t.type == "table"


def test_table_block_rejects_row_column_mismatch():
    with pytest.raises(ValidationError):
        TableBlock(headers=["A", "B"], rows=[["1", "2", "3"]])


def test_list_block_minimal():
    l = ListBlock(items=["x", "y"])
    assert l.type == "list"


def test_image_block_minimal():
    i = ImageBlock(url="https://example.com/x.png", alt="x")
    assert i.type == "image"
