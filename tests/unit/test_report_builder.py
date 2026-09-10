"""Phase D：report_builder LLM 解读/md 拼装与 maybe_build_report 交付编排单测。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from orchestrator.report import maybe_build_report
from orchestrator.report.report_builder import (
    CONCLUSION_TITLE,
    Report,
    interpret_sections,
    render_markdown,
    to_blocks,
    write_markdown,
)
from orchestrator.report.section_digest import Section
from shared.executor_types import ExecutionState


class _FakeLLM:
    """按调用序返回预制文本；fail_on（1 起）的调用序抛错。"""

    def __init__(self, fail_on: set[int] | None = None) -> None:
        self.fail_on = fail_on or set()
        self.calls: list[list] = []

    def chat(self, messages) -> str:
        self.calls.append(messages)
        if len(self.calls) in self.fail_on:
            raise RuntimeError("llm down")
        return f"解读文本{len(self.calls)}"


def _sections(img_dir: Path | None = None) -> list[Section]:
    """img_dir 给定则图片路径落其下（Windows 跨盘 relpath 会抛 ValueError，
    render/write 用例需图片与 tmp_path 同盘）；缺省保持计划原始 /wsroot 样例。"""
    if img_dir is None:
        umap, heatmap = "/wsroot/ds/umap.png", "/wsroot/ds/cnv/heatmap.png"
    else:
        umap = str(img_dir / "umap.png")
        heatmap = str(img_dir / "cnv" / "heatmap.png")
    return [
        Section(title="数据质控与预处理", tool_name="sc_process",
                images=[umap], numbers={"n_cells": 8000}),
        Section(title="CNV 恶性判定与亚克隆", tool_name="sc_cnv",
                images=[heatmap],
                csvs=[], numbers={"n_malignant": 150}),
        Section(title="细胞通讯分析", tool_name="sc_cellchat"),
    ]


def test_interpret_all_llm_success():
    llm = _FakeLLM()
    built, conclusion = interpret_sections(_sections(), llm)
    assert [b.interpretation for b in built] == ["解读文本1", "解读文本2", "解读文本3"]
    assert conclusion == "解读文本4"
    assert len(llm.calls) == 4  # 3 节 + 总评


def test_interpret_partial_failure_degrades():
    llm = _FakeLLM(fail_on={2})
    built, conclusion = interpret_sections(_sections(), llm)
    assert built[0].interpretation == "解读文本1"
    assert "sc_cnv" in built[1].interpretation  # 降级模板句含工具名
    assert built[2].interpretation == "解读文本3"
    assert conclusion  # 总评仍生成


def test_interpret_no_llm_all_fallback():
    built, conclusion = interpret_sections(_sections(), None)
    assert all("分析结果" in b.interpretation for b in built)
    assert conclusion == ""


def test_interpret_conclusion_failure_omitted():
    llm = _FakeLLM(fail_on={4})
    built, conclusion = interpret_sections(_sections(), llm)
    assert conclusion == ""
    assert len(built) == 3


def _report(img_dir: Path | None = None) -> Report:
    built, conclusion = interpret_sections(_sections(img_dir), _FakeLLM())
    return Report(title="单细胞分析报告 · 测试", dataset_id="ds",
                  sections=built, conclusion=conclusion)


def test_render_markdown_relative_image_paths(tmp_path):
    report = _report(tmp_path / "ds")
    report_dir = tmp_path / "ds" / "report"
    md = render_markdown(report, report_dir)
    assert md.startswith("# 单细胞分析报告 · 测试")
    assert "## 数据质控与预处理" in md
    assert f"## {CONCLUSION_TITLE}" in md
    assert "](../umap.png)" in md
    assert "](../cnv/heatmap.png)" in md
    assert "\\" not in md.splitlines()[0]


def test_write_markdown_lands_under_ds_report(tmp_path):
    path = write_markdown(_report(tmp_path / "ds"), str(tmp_path), "ds")
    assert path.parent == tmp_path / "ds" / "report"
    assert path.name.startswith("report_") and path.suffix == ".md"
    assert path.read_text(encoding="utf-8").startswith("# ")


def test_to_blocks_sequence():
    from orchestrator.blocks.schemas import HeadingBlock, ImageBlock, TextBlock
    blocks = to_blocks(_report())
    assert isinstance(blocks[0], HeadingBlock) and blocks[0].level == 1
    assert isinstance(blocks[1], HeadingBlock) and blocks[1].level == 2
    assert isinstance(blocks[2], TextBlock)
    assert isinstance(blocks[3], ImageBlock) and blocks[3].path


def _runner_env(tmp_path):
    """maybe_build_report 的假 plan/scheduler/adapters 环境。"""
    node = SimpleNamespace(node_id="n1", kind="tool", tool_name="sc_process")
    plan = SimpleNamespace(nodes=[node])
    handle = SimpleNamespace(
        state=ExecutionState.SUCCESS,
        outputs={"umap_png": "/ws/ds/umap.png", "n_cells": 8000,
                 "de_csv": "/ws/ds/de/de.csv"})
    sch = SimpleNamespace(_handles={"n1": handle})
    im = MagicMock()
    return plan, sch, im


def test_maybe_build_report_sent(tmp_path):
    plan, sch, im = _runner_env(tmp_path)
    doc = MagicMock()
    doc.create_document.return_value = "doc_new_1"
    status = maybe_build_report(
        plan=plan, scheduler=sch, ws_root=str(tmp_path),
        llm=_FakeLLM(), doc_adapter=doc, im=im, chat_id="oc_1",
        sender_open_id="ou_1", task_text="分析这个肿瘤样本")
    assert status["status"] == "sent"
    doc.create_document.assert_called_once()
    doc.render_blocks.assert_called_once()
    doc.grant_doc_view.assert_called_once_with("doc_new_1", "ou_1")
    assert "https://feishu.cn/docx/doc_new_1" in im.reply.call_args.args[1]
    md = Path(status["md_path"])
    assert md.exists() and md.parent == tmp_path / "ds" / "report"


def test_maybe_build_report_skipped_without_sc(tmp_path):
    node = SimpleNamespace(node_id="n1", kind="tool", tool_name="summarize_text")
    plan = SimpleNamespace(nodes=[node])
    handle = SimpleNamespace(
        state=ExecutionState.SUCCESS, outputs={"summary": "x"})
    sch = SimpleNamespace(_handles={"n1": handle})
    im, llm, doc = MagicMock(), _FakeLLM(), MagicMock()
    status = maybe_build_report(
        plan=plan, scheduler=sch, ws_root=str(tmp_path),
        llm=llm, doc_adapter=doc, im=im, chat_id="oc_1", sender_open_id="ou_1")
    assert status["status"] == "skipped"
    im.reply.assert_not_called()
    doc.create_document.assert_not_called()
    assert llm.calls == []


def test_maybe_build_report_degraded_on_doc_failure(tmp_path):
    plan, sch, im = _runner_env(tmp_path)
    doc = MagicMock()
    doc.create_document.side_effect = RuntimeError("api down")
    status = maybe_build_report(
        plan=plan, scheduler=sch, ws_root=str(tmp_path),
        llm=_FakeLLM(), doc_adapter=doc, im=im, chat_id="oc_1",
        sender_open_id="ou_1", task_text="分析")
    assert status["status"] == "degraded"
    text = im.reply.call_args.args[1]
    assert "底稿已落盘" in text
    assert Path(status["md_path"]).exists()


def test_maybe_build_report_md_only_without_sdk(tmp_path):
    plan, sch, im = _runner_env(tmp_path)
    doc = MagicMock()
    doc.sdk_client = None
    status = maybe_build_report(
        plan=plan, scheduler=sch, ws_root=str(tmp_path),
        llm=_FakeLLM(), doc_adapter=doc, im=im, chat_id="oc_1",
        sender_open_id="ou_1")
    assert status["status"] == "md_only"
    doc.create_document.assert_not_called()
    assert "底稿" in im.reply.call_args.args[1]
