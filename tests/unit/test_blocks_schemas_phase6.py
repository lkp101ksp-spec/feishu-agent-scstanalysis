import pytest
from pydantic import ValidationError

from orchestrator.blocks.schemas import (
    CalloutBlock,
    DividerBlock,
    EmbedBlock,
    EquationBlock,
    FileBlock,
    MathBlock,
    MermaidBlock,
    VideoBlock,
)


def test_embed_block_minimal():
    e = EmbedBlock(url="https://example.com")
    assert e.type == "embed"


def test_embed_block_rejects_non_http():
    with pytest.raises(ValidationError):
        EmbedBlock(url="ftp://x")


def test_divider_block_minimal():
    d = DividerBlock()
    assert d.type == "divider"


def test_callout_block_minimal():
    c = CalloutBlock(emoji="⚠️", text="warning")
    assert c.type == "callout"


def test_equation_block_minimal():
    e = EquationBlock(latex="E = mc^2")
    assert e.type == "equation"


def test_math_block_display_mode():
    m = MathBlock(latex="\\sum x", display_mode=True)
    assert m.type == "math"
    assert m.display_mode is True


def test_mermaid_block_minimal():
    m = MermaidBlock(code="graph TD; A-->B")
    assert m.type == "mermaid"


def test_video_block_minimal():
    v = VideoBlock(url="https://x.com/v.mp4")
    assert v.type == "video"


def test_file_block_minimal():
    f = FileBlock(file_token="abc", name="x.txt", size=1024)
    assert f.type == "file"
    assert f.size == 1024
