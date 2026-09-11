"""D 报告汇编：章节骨架 + LLM 逐节解读 + Markdown/文档块拼装。"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.report.section_digest import Section, section_digest_text
from shared.schemas import ChatMessage

logger = logging.getLogger(__name__)

MAX_LLM_CALLS = 12
CONCLUSION_TITLE = "总体结论"
SECTION_SYSTEM = (
    "你是资深单细胞生信分析师。根据给定分析环节的输出摘要撰写中文结果解读："
    "150-300字，点明关键趋势与数字；所有数字必须来自输入数据，禁止编造；"
    "只输出一段连贯文字，不要标题、列表或 Markdown 标记；"
    "不要提及产物图片/数据表数量等流程元信息。")
CONCLUSION_SYSTEM = (
    "你是资深单细胞生信分析师。以下是本次单细胞分析各环节的结果解读，"
    "请综合为不超过300字的总体结论段落，突出最重要的生物学发现；"
    "不要分节、不要列表，只输出一段连贯文字。")


@dataclass
class BuiltSection:
    """章节成品：产物 + 解读段落。"""
    section: Section
    interpretation: str


@dataclass
class Report:
    """报告成品：标题 + 数据集 + 各节 + 总体结论。"""
    title: str
    dataset_id: str
    sections: list[BuiltSection] = field(default_factory=list)
    conclusion: str = ""


def _fallback_text(section: Section) -> str:
    """LLM 不可用时的模板降级句。"""
    return f"本节展示 {section.tool_name} 分析结果，详见下图与附件数据。"


def interpret_sections(sections: list[Section],
                       llm: Any | None) -> tuple[list[BuiltSection], str]:
    """逐节 LLM 解读 + 总体结论；单节失败降级不中断，总评失败省略。

    LLM 调用上限 MAX_LLM_CALLS（为总评预留 1 次额度）。
    """
    built: list[BuiltSection] = []
    calls = 0
    for section in sections:
        if llm is None or calls >= MAX_LLM_CALLS - 1:
            built.append(BuiltSection(section=section,
                                      interpretation=_fallback_text(section)))
            continue
        digest = section_digest_text(section)
        user = f"分析环节：{section.title}（工具 {section.tool_name}）\n{digest}"
        try:
            text = llm.chat([
                ChatMessage(role="system", content=SECTION_SYSTEM),
                ChatMessage(role="user", content=user),
            ]).strip()
            calls += 1
        except Exception as e:
            logger.warning("report section llm failed (%s): %s",
                           section.tool_name, e)
            text = ""
        built.append(BuiltSection(
            section=section,
            interpretation=text or _fallback_text(section)))
    conclusion = ""
    if llm is not None and built:
        joined = "\n\n".join(f"【{b.section.title}】{b.interpretation}"
                             for b in built)
        try:
            conclusion = llm.chat([
                ChatMessage(role="system", content=CONCLUSION_SYSTEM),
                ChatMessage(role="user", content=joined),
            ]).strip()
        except Exception as e:
            logger.warning("report conclusion llm failed: %s", e)
    return built, conclusion


def render_markdown(report: Report, report_dir: Path) -> str:
    """拼装 Markdown 底稿；图片用相对 report_dir 的 POSIX 风格路径引用。"""
    lines = [f"# {report.title}", ""]
    for b in report.sections:
        lines.append(f"## {b.section.title}")
        lines.append("")
        lines.append(b.interpretation)
        lines.append("")
        for img in b.section.images:
            rel = os.path.relpath(img, report_dir).replace("\\", "/")
            lines.append(f"![{Path(img).stem}]({rel})")
        lines.append("")
    if report.conclusion:
        lines.append(f"## {CONCLUSION_TITLE}")
        lines.append("")
        lines.append(report.conclusion)
        lines.append("")
    return "\n".join(lines)


def write_markdown(report: Report, ws_root: str, dataset_id: str) -> Path:
    """底稿落 bio_workspace/{ds}/report/report_{ts}.md，返回路径。"""
    report_dir = Path(ws_root) / dataset_id / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"report_{datetime.now():%Y%m%d_%H%M%S}.md"
    path.write_text(render_markdown(report, report_dir), encoding="utf-8")
    return path


def to_blocks(report: Report) -> list[Any]:
    """报告 → 飞书文档块（ImageBlock path 模式，DocAdapter 三步插图）。"""
    from orchestrator.blocks.schemas import (
        HeadingBlock,
        ImageBlock,
        TextBlock,
    )
    blocks: list[Any] = [HeadingBlock(level=1, text=report.title)]
    for b in report.sections:
        blocks.append(HeadingBlock(level=2, text=b.section.title))
        blocks.append(TextBlock(text=b.interpretation))
        for img in b.section.images:
            blocks.append(ImageBlock(path=img, alt=Path(img).stem))
    if report.conclusion:
        blocks.append(HeadingBlock(level=2, text=CONCLUSION_TITLE))
        blocks.append(TextBlock(text=report.conclusion))
    return blocks
