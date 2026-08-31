"""文档写入服务层。

业务流程：
1. 校验 session 当前是否对该 doc_id 有有效 bind-doc 授权
2. DocWriteRepo.create_pending → DocWriteRepo.transition(approved) → transition(writing)
3. 调用 DocAdapter.append_plain_text 真正写文档
4. DocWriteRepo.mark_success（带 anchor_block_id）

异常路径：
- 无有效 bind：抛 DocWriteError，不创建 doc_writes
- 适配层异常：mark_failed 后抛 DocWriteError
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from persistence.models import DocWriteRow
from persistence.repositories.doc_write_repo import DocWriteRepo
from persistence.repositories.session_repo import SessionRepo
from shared.errors import DocWriteError
from shared.ulid_ import new_ulid

# DocAdapter 仅作类型标注：真实导入会拖进 lark SDK（protobuf 冷导入
# 实测 ~10s，真机卡片测试踩 10s 轮询超时线上），TYPE_CHECKING 斩断依赖链
if TYPE_CHECKING:
    from feishu_adapter.doc_adapter import DocAdapter

logger = logging.getLogger(__name__)


class DocWriteService:
    """在 bind-doc 授权窗口内对飞书文档追加纯文本。"""

    def __init__(
        self,
        session_repo: SessionRepo,
        doc_repo: DocWriteRepo,
        doc_adapter: DocAdapter,
    ):
        self.session_repo = session_repo
        self.doc_repo = doc_repo
        self.doc_adapter = doc_adapter

    def write_plain_text(
        self, session_id: str, task_id: str, requested_by: str, text: str,
        anchor_text: Optional[str] = None,
    ) -> dict:
        """在授权窗口内追加纯文本。返回 {doc_write_id, doc_id, anchor_block_id, status}。

        anchor_text：本条消息的临时锚点（#写到 语法），优先于会话级 bind_anchor。

        异常：DocWriteError（无 bind / bind 过期 / 写入失败）
        """
        session_row, doc_id = self._valid_bound_doc(session_id)
        doc_write_id = new_ulid()

        # 1. pending → approved → writing
        self.doc_repo.create_pending(
            doc_write_id=doc_write_id,
            task_id=task_id,
            doc_id=doc_id,
            requested_by=requested_by,
            approval_mode="bind_scope",
            payload_text=text,
            anchor_text=anchor_text or getattr(session_row, "bind_anchor", None),
        )
        self.doc_repo.transition(doc_write_id, "approved")
        self.doc_repo.transition(doc_write_id, "writing")

        # 2. 真正写入（消息级/会话级锚点定位插入，否则追加文档末尾）
        insert_index = self._resolve_insert_index(session_row, session_id, doc_id,
                                                  anchor_text)
        try:
            anchor = self.doc_adapter.append_plain_text(
                doc_id=doc_id, text=text, index=insert_index)
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            self.doc_repo.mark_failed(doc_write_id, reason=reason)
            logger.warning("doc_write_failed doc_write_id=%s reason=%s", doc_write_id, reason)
            raise DocWriteError(f"doc write failed: {e}") from e

        # 3. success
        self.doc_repo.mark_success(doc_write_id, anchor_block_id=anchor)

        return {
            "doc_write_id": doc_write_id,
            "doc_id": doc_id,
            "anchor_block_id": anchor,
            "status": "success",
        }

    # === Phase 14：card_confirm 卡片确认写回 ===

    def create_confirm_pending(
        self, *, session_id: str, task_id: str, requested_by: str,
        preview_text: str, approval_mode: str = "card_confirm",
    ) -> dict:
        """card_confirm / node_l2 第一步：校验 bind + 建 pending 记录。

        approval_mode="card_confirm"（Phase 14 收尾整体写回审批）或
        "node_l2"（Phase 17 write_doc 节点级审批）——两者共用 doc_writes
        表、owner 校验与启动清扫，仅发起位置不同。
        返回 {doc_write_id, doc_id}；无 bind / 过期抛 DocWriteError（调用方
        以 IM 提示收场，不写文档）。preview_text 落 payload_json 供审计。
        """
        _, doc_id = self._valid_bound_doc(session_id)
        doc_write_id = new_ulid()
        self.doc_repo.create_pending(
            doc_write_id=doc_write_id,
            task_id=task_id,
            doc_id=doc_id,
            requested_by=requested_by,
            approval_mode=approval_mode,
            payload_text=preview_text,
        )
        return {"doc_write_id": doc_write_id, "doc_id": doc_id}

    def complete_confirmed(
        self, *, doc_write_id: str, decision: str, blocks: list,
    ) -> dict:
        """card_confirm 第二步：按决策收尾状态机并（若 approve）渲染写入。

        decision="approve" → approved → writing → render_blocks → success/failed；
        "deny"/"timeout" → cancelled（不写文档）。返回 {status, ...}，
        写入异常不抛（mark_failed 后返回 failed，调用方 IM 提示）。
        """
        if decision != "approve":
            self.doc_repo.transition(doc_write_id, "cancelled")
            return {"doc_write_id": doc_write_id, "status": "cancelled"}

        self.doc_repo.transition(doc_write_id, "approved")
        self.doc_repo.transition(doc_write_id, "writing")
        row = self.doc_repo.get(doc_write_id)
        if row is None:
            return {"doc_write_id": doc_write_id, "status": "failed",
                    "reason": "doc_write row missing"}
        try:
            anchor = self.doc_adapter.render_blocks(row.doc_id, blocks)
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            self.doc_repo.mark_failed(doc_write_id, reason=reason)
            logger.warning(
                "doc_write_failed doc_write_id=%s reason=%s",
                doc_write_id, reason)
            return {"doc_write_id": doc_write_id, "status": "failed",
                    "reason": reason}
        self.doc_repo.mark_success(doc_write_id, anchor_block_id=anchor)
        return {"doc_write_id": doc_write_id, "doc_id": row.doc_id,
                "anchor_block_id": anchor, "status": "success"}

    def cancel_stale_pending(self) -> int:
        """启动清扫：遗留卡片审批 pending（重启后无人等待）置 cancelled。

        覆盖 card_confirm（Phase 14 收尾写回）与 node_l2（Phase 17 节点级）
        两种 mode。返回清扫条数。正常流转的 pending 生命周期 ≤ 审批超时，
        启动时仍在 pending 的必是孤儿记录。
        """
        rows = (
            self.doc_repo.session.query(DocWriteRow)
            .filter(DocWriteRow.approval_mode.in_(
                        ["card_confirm", "node_l2"]),
                    DocWriteRow.status == "pending")
            .all()
        )
        for row in rows:
            row.status = "cancelled"
            row.fail_reason = "stale pending on startup"
        if rows:
            self.doc_repo.session.flush()
        return len(rows)

    def _valid_bound_doc(self, session_id: str):
        """校验 session 的 bind-doc 授权窗口，返回 (session_row, doc_id)。"""
        session_row = self.session_repo.get(session_id)
        if session_row is None or session_row.bound_doc_id is None:
            raise DocWriteError("no valid bind-doc on this session")
        if session_row.bind_expires_at is None:
            raise DocWriteError("bind-doc expired, please /bind-doc again")
        expires_at = session_row.bind_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise DocWriteError("bind-doc expired, please /bind-doc again")
        return session_row, session_row.bound_doc_id

    def _resolve_insert_index(self, session_row, session_id: str,
                              doc_id: str, msg_anchor: Optional[str] = None) -> int:
        """锚点定位：返回根块下的插入下标；-1 = 追加到文档末尾。

        锚点来源优先级：消息级 #写到锚点 > 会话级 bind_anchor。规则：
        1. 同锚点有历史成功写入：插到最近一次写入块之后（保证顺序向下）；
        2. 否则插到第一个文字包含锚点的块之后；
        3. 锚点找不到：回退末尾追加并记 warning。
        """
        anchor_text = msg_anchor or getattr(session_row, "bind_anchor", None)
        if not anchor_text:
            return -1
        try:
            children = self.doc_adapter.list_root_children(doc_id)
        except Exception as e:
            logger.warning("anchor locate failed, fallback to end: %s", e)
            return -1

        last_anchor = self.doc_repo.latest_success_anchor_for_session(
            session_id, anchor_text=anchor_text)
        if last_anchor:
            for i, block in enumerate(children):
                if block.get("block_id") == last_anchor:
                    return i + 1
            logger.info("last write block %s gone, relocate by anchor text",
                        last_anchor)

        for i, block in enumerate(children):
            if anchor_text in _block_text(block):
                return i + 1
        logger.warning("anchor text %r not found in doc %s, fallback to end",
                       anchor_text, doc_id)
        return -1


def _block_text(block: dict) -> str:
    """提取块的纯文本（text/heading1-9 等带 elements 的块类型）。"""
    for value in block.values():
        if isinstance(value, dict) and "elements" in value:
            return "".join(
                e.get("text_run", {}).get("content", "")
                for e in value.get("elements", []))
    return ""
