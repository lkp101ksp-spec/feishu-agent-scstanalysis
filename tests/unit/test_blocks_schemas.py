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


def test_image_block_file_token_only():
    """path 图片块（本地文件，Phase 20）：无 url 也合法。"""
    i = ImageBlock(path=r"D:\bio_ws\ds\umap.png", alt="umap")
    assert i.type == "image" and i.url == ""


def test_image_block_requires_url_or_token():
    """url/path 均空：校验失败。"""
    with pytest.raises(ValidationError):
        ImageBlock(alt="empty")


def test_image_block_rejects_non_http_url():
    """url 非 http(s)：校验失败。"""
    with pytest.raises(ValidationError):
        ImageBlock(url="ftp://x/y.png")
