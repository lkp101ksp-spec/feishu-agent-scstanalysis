"""TemplateEngine：纯函数 payload → BlockSpec 列表。

无副作用，便于测试。Phase 5 扩展富文本块（list[AnyBlock]）+ 用户自定义模板。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BlockSpec:
    block_type: str
    content: dict

    def to_dict(self) -> dict:
        return {"block_type": self.block_type, **self.content}


class TemplateEngine:
    TEXT_THRESHOLD = 2000
    TABLE_ROW_THRESHOLD = 10

    def render_text(self, text: str) -> list[BlockSpec]:
        if len(text.encode()) < self.TEXT_THRESHOLD:
            return [BlockSpec(block_type="text", content={"text": text})]
        return [
            BlockSpec(
                block_type="callout",
                content={"emoji": "📄", "text": text, "color": "blue"},
            )
        ]

    def render_table(self, *, headers: list, rows: list) -> list[BlockSpec]:
        if len(rows) < self.TABLE_ROW_THRESHOLD:
            return [
                BlockSpec(
                    block_type="table",
                    content={"headers": headers, "rows": rows},
                )
            ]
        summary = f"表格 {len(rows)} 行；完整数据请见文件。"
        return [
            BlockSpec(
                block_type="callout",
                content={"emoji": "📊", "text": summary, "color": "blue"},
            ),
            BlockSpec(
                block_type="file",
                content={
                    "placeholder": "summary_parquet",
                    "note": "实际文件由 Drive Adapter 在 Phase 2.1 注入",
                },
            ),
        ]

    def render_image(self, *, file_token: str, alt: str = "") -> list[BlockSpec]:
        return [
            BlockSpec(
                block_type="image",
                content={"file_token": file_token, "alt": alt},
            )
        ]

    def render_code(self, code: str, *, language: str = "inferred") -> list[BlockSpec]:
        lang = "python" if language == "inferred" else language
        return [
            BlockSpec(
                block_type="code_block",
                content={"language": lang, "text": code},
            )
        ]

    def render_error(self, message: str) -> list[BlockSpec]:
        return [
            BlockSpec(
                block_type="callout",
                content={"emoji": "⚠️", "text": message, "color": "red"},
            )
        ]

    def render_plan_summary(
        self, *, status: str, node_states: dict, artifacts_count: int
    ) -> list[BlockSpec]:
        blocks = [
            BlockSpec(
                block_type="heading_2",
                content={"text": f"Plan 执行结果（{status}）"},
            )
        ]
        for node_id, state in node_states.items():
            blocks.append(
                BlockSpec(
                    block_type="text",
                    content={"text": f"- {node_id}: {state}"},
                )
            )
        blocks.append(BlockSpec(block_type="divider", content={}))
        blocks.append(
            BlockSpec(
                block_type="text",
                content={"text": f"共产生 {artifacts_count} 个 artifacts"},
            )
        )
        return blocks

    # === Phase 5: 富文本块（AnyBlock） ===

    def render_plan_summary_blocks(
        self, *, status: str, node_states: dict, artifacts_count: int
    ) -> list:
        """Phase 5: 返回 Pydantic Block 列表（用于飞书 doc 渲染）。"""
        from orchestrator.blocks.schemas import HeadingBlock, TableBlock, TextBlock
        return [
            HeadingBlock(level=2, text=f"Plan 执行结果（{status}）"),
            TextBlock(text=f"Nodes: {len(node_states)}; Artifacts: {artifacts_count}"),
            TableBlock(
                headers=["Node", "State"],
                rows=[[nid, state] for nid, state in node_states.items()],
            ),
        ]

    def render_blocks_to_text(self, blocks) -> str:
        """Phase 5: list[Block] → 纯文本（用于 IM reply）。"""
        lines = []
        for b in blocks:
            t = getattr(b, "type", None)
            if t == "heading":
                lines.append(f"{'#' * b.level} {b.text}")
            elif t == "text":
                lines.append(b.text)
            elif t == "code":
                lines.append(f"```{b.language}\n{b.text}\n```")
            elif t == "quote":
                lines.append(f"> {b.text}")
            elif t == "table":
                lines.append(", ".join(b.headers))
                lines.extend([", ".join(r) for r in b.rows])
            elif t == "list":
                for i, item in enumerate(b.items, 1):
                    if b.ordered:
                        lines.append(f"{i}. {item}")
                    else:
                        lines.append(f"- {item}")
            elif t == "image":
                lines.append(f"![{b.alt}]({b.url})")
            # === Phase 6: 8 类增量 ===
            elif t == "embed":
                lines.append(f"[embed: {b.title}]({b.url})")
            elif t == "divider":
                lines.append("---")
            elif t == "callout":
                lines.append(f"{b.emoji} {b.text}")
            elif t == "equation":
                lines.append(f"$${b.latex}$$")
            elif t == "math":
                if b.display_mode:
                    lines.append(f"$$\n{b.latex}\n$$")
                else:
                    lines.append(f"${b.latex}$")
            elif t == "mermaid":
                lines.append(f"```mermaid\n{b.code}\n```")
            elif t == "video":
                lines.append(f"[video: {b.url}]")
            elif t == "file":
                lines.append(f"[file: {b.name} ({b.size} bytes)]")
        return "\n".join(lines)
