"""D 分析报告自动汇编（Phase D）。

流程收尾自动收集 sc_* 节点产物 → LLM 逐节解读 → Markdown 底稿 +
飞书云文档交付。research_runner 收尾钩子调 maybe_build_report，
故障隔离由调用方 try/except 保证。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.report.report_builder import (
    Report,
    interpret_sections,
    to_blocks,
    write_markdown,
)
from orchestrator.report.section_digest import Section, collect_sections

logger = logging.getLogger(__name__)

__all__ = ["maybe_build_report", "Section", "collect_sections"]


def _dataset_id(sections: list[Section], ws_root: str) -> str:
    """从首个产物宿主路径推数据集 id（ws_root/<ds>/... 第一段）。"""
    root = Path(ws_root).resolve()
    for s in sections:
        for p in s.images + s.csvs:
            try:
                parts = Path(p).resolve().relative_to(root).parts
            except ValueError:
                continue
            if parts:
                return parts[0]
    return "unknown"


def maybe_build_report(*, plan: Any, scheduler: Any, ws_root: str,
                       llm: Any | None, doc_adapter: Any | None,
                       im: Any, chat_id: str, sender_open_id: str,
                       folder_token: str = "",
                       task_text: str = "") -> dict[str, Any]:
    """sc 流程收尾自动汇编：md 底稿必落盘，云文档链路逐级降级。

    返回 status：skipped（无成功 sc 节点/未配 ws_root）/ sent（云文档
    已发链接）/ degraded（云文档失败，md+要点已发）/ md_only（无 SDK
    通道，仅底稿）。本函数内部不抛交付链异常（degraded 兜住）；收集与
    md 阶段异常交由调用方隔离。
    """
    if not ws_root:
        return {"status": "skipped"}
    sections = collect_sections(plan, scheduler, ws_root)
    if not sections:
        return {"status": "skipped"}
    ds = _dataset_id(sections, ws_root)
    label = task_text[:20] + ("…" if len(task_text) > 20 else "")
    title = f"单细胞分析报告 · {label} · {datetime.now():%m-%d %H:%M}"
    built, conclusion = interpret_sections(sections, llm)
    report = Report(title=title, dataset_id=ds, sections=built,
                    conclusion=conclusion)
    md_path = write_markdown(report, ws_root, ds)
    logger.info("report markdown written: %s", md_path)
    if doc_adapter is None or getattr(doc_adapter, "sdk_client", None) is None:
        im.reply(chat_id, f"[报告] 已生成分析底稿：{md_path}")
        return {"status": "md_only", "md_path": str(md_path)}
    try:
        doc_id = doc_adapter.create_document(title, folder_token or None)
        doc_adapter.render_blocks(doc_id, to_blocks(report))
        if sender_open_id:
            doc_adapter.grant_doc_view(doc_id, sender_open_id)
        url = f"https://feishu.cn/docx/{doc_id}"
        im.reply(chat_id, f"[报告] 分析报告已生成：{url}")
        return {"status": "sent", "doc_id": doc_id,
                "md_path": str(md_path)}
    except Exception as e:
        logger.warning("report doc delivery failed: %s", e)
        digest = conclusion or "；".join(b.section.title for b in built)
        im.reply(chat_id,
                 f"[报告] 云文档生成失败（{e}），底稿已落盘：{md_path}\n"
                 f"要点：{digest[:500]}")
        return {"status": "degraded", "md_path": str(md_path)}
