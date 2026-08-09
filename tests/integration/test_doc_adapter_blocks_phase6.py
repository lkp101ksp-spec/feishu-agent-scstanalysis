import httpx
import pytest
import respx

from feishu_adapter.doc_adapter import DocAdapter
from orchestrator.blocks.schemas import (CalloutBlock, DividerBlock,
                                          EmbedBlock, EquationBlock,
                                          FileBlock, MathBlock,
                                          MermaidBlock, VideoBlock)


class NoWaitLimiter:
    def wait(self):
        pass


@pytest.fixture
def adapter():
    a = DocAdapter(base_url="https://example.feishu.cn", api_token="t",
                    rate_limiter=NoWaitLimiter())
    return a


@respx.mock
def test_render_divider(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1", [DividerBlock()])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_embed(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1",
        [EmbedBlock(url="https://example.com", title="x")])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_callout(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1",
        [CalloutBlock(emoji="⚠️", text="warn", color="red")])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_equation(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1",
        [EquationBlock(latex="E = mc^2")])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_mermaid_fallback(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1",
        [MermaidBlock(code="graph TD; A-->B")])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_video(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1",
        [VideoBlock(url="https://x.com/v.mp4")])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_file(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1",
        [FileBlock(file_token="abc", name="x.txt", size=1024)])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_all_phase6_types(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    blocks = [
        EmbedBlock(url="https://example.com"),
        DividerBlock(),
        CalloutBlock(emoji="📌", text="hi"),
        EquationBlock(latex="x"),
        MathBlock(latex="y"),
        MermaidBlock(code="graph TD"),
        VideoBlock(url="https://v.mp4"),
        FileBlock(file_token="t", name="f"),
    ]
    adapter.render_blocks("doc_1", blocks)
    assert respx.calls.call_count == 8