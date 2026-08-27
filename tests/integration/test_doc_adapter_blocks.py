import time

import httpx
import pytest
import respx

from feishu_adapter.doc_adapter import DocAdapter
from orchestrator.blocks.schemas import (
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    QuoteBlock,
    TableBlock,
    TextBlock,
)


class NoWaitLimiter:
    def wait(self):
        pass


@pytest.fixture
def adapter():
    a = DocAdapter(base_url="https://example.feishu.cn", api_token="t",
                    rate_limiter=NoWaitLimiter())
    return a


@respx.mock
def test_render_heading(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1", [HeadingBlock(level=2, text="Hi")])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_all_six_types(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    blocks = [
        HeadingBlock(level=1, text="h"),
        TextBlock(text="t"),
        CodeBlock(language="py", text="x"),
        QuoteBlock(text="q"),
        TableBlock(headers=["A"], rows=[["1"]]),
        ListBlock(items=["x"]),
        ImageBlock(url="https://x.png"),
    ]
    adapter.render_blocks("doc_1", blocks)
    assert respx.calls.call_count == 7


@respx.mock
def test_render_blocks_respects_rate_limit():
    """3 req/s 限流：连续渲染多个 block，最少等待 ~1.33s（5 个）。"""
    a = DocAdapter(base_url="https://example.feishu.cn", api_token="t")
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    blocks = [HeadingBlock(level=2, text=f"h{i}") for i in range(5)]
    t0 = time.monotonic()
    a.render_blocks("doc_1", blocks)
    elapsed = time.monotonic() - t0
    # 5 calls: 4 intervals × 0.333s ≈ 1.33s
    assert elapsed >= 1.0
