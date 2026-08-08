"""Doc 文档操作：Phase 1 仅支持块树读取与纯文本追加。"""
from typing import Any

from feishu_adapter.client import LarkCLI


class DocAdapter:
    """对飞书 Doc 文档的薄封装。"""

    def __init__(self, cli: LarkCLI | None = None):
        self.cli = cli or LarkCLI()

    def get_block_tree(self, doc_id: str) -> list[dict[str, Any]]:
        """读取文档块树，返回有序块列表。"""
        result = self.cli.run(["docx", "block", "list", "--doc-id", doc_id])
        return result.get("blocks", [])

    def append_plain_text(self, doc_id: str, text: str) -> str:
        """在文档末尾追加一段纯文本块。返回新 block_id。"""
        result = self.cli.run([
            "docx", "block", "create",
            "--doc-id", doc_id,
            "--block-type", "text",
            "--content", text,
        ])
        return result.get("block_id", "")