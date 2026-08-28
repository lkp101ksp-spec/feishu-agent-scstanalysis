"""Phase 8 T5: 评论动作服务（前缀解析 + owner apply + 打标，ADR-0020/0023）。

Phase 11 追加：apply 成功后回写评论回执（ADR-0034，失败降级 warning）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# 指令前缀 → 动作类型（ADR-0020）
_ACTION_PREFIXES = ("replan", "revise", "add-step")


@dataclass
class ParsedAction:
    """一条评论解析出的动作。"""

    kind: str        # replan / revise / add-step
    template_id: str
    payload: str     # note / desc / tool_name


def parse_action(text: str) -> Optional[ParsedAction]:
    """解析评论正文前缀指令；不匹配返回 None（普通评论）。

    语法：/replan <tid> <note> | /revise <tid> <desc> | /add-step <tid> <tool>
    """
    stripped = (text or "").strip()
    for kind in _ACTION_PREFIXES:
        prefix = f"/{kind} "
        if stripped.startswith(prefix):
            rest = stripped[len(prefix):].strip()
            parts = rest.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                return None  # 参数不足 → 视为普通评论
            return ParsedAction(kind=kind, template_id=parts[0],
                                payload=parts[1].strip())
    return None


class CommentActionService:
    """owner 显式 apply pending 评论中的指令动作。"""

    def __init__(self, comment_repo, template_repo, version_service,
                 comment_client=None) -> None:
        self.comment_repo = comment_repo
        self.template_repo = template_repo
        self.version_service = version_service
        # 可选注入：回执写回客户端（None 时禁用回执，ADR-0034）
        self.comment_client = comment_client

    def apply(self, *, doc_id: str, caller_open_id: str) -> dict:
        """遍历 pending 评论执行动作；逐条权限校验，单条失败不中断。"""
        applied = skipped = failed = 0
        details: list[dict] = []
        for c in self.comment_repo.list_pending(doc_id):
            parsed = parse_action(c.text)
            if parsed is None:
                skipped += 1
                details.append({"comment_id": c.comment_id,
                                "kind": None, "status": "skipped"})
                continue
            detail = self._execute(parsed, caller_open_id=caller_open_id)
            detail["comment_id"] = c.comment_id
            details.append(detail)
            if detail["status"] == "applied":
                applied += 1
                self.comment_repo.mark_processed(c.comment_id)
                self._send_receipt(doc_id=doc_id, comment=c, detail=detail)
            else:
                failed += 1
        return {"applied": applied, "skipped": skipped,
                "failed": failed, "details": details}

    def _send_receipt(self, *, doc_id: str, comment, detail: dict) -> None:
        """对已应用评论回写固定文案回执；失败仅记 warning 不阻断（ADR-0034）。"""
        if self.comment_client is None:
            return
        text = (f"已按此评论完成修改：模板 {detail.get('template_id', '')} "
                f"已更新至版本 {detail.get('version_number', '?')}。")
        try:
            self.comment_client.reply_comment(
                file_token=doc_id, comment_id=comment.comment_id, text=text)
        except Exception:
            logger.warning("receipt failed for comment %s",
                           comment.comment_id, exc_info=True)

    def _execute(self, parsed: ParsedAction, *, caller_open_id: str) -> dict:
        """执行单条动作；返回 {kind, status, reason?}。"""
        tpl = self.template_repo.get(parsed.template_id)
        if tpl is None or getattr(tpl, "archived_at", None) is not None:
            return {"kind": parsed.kind, "status": "failed",
                    "reason": "not_found"}
        if tpl.owner_open_id != caller_open_id:
            return {"kind": parsed.kind, "status": "failed",
                    "reason": "not_owner"}

        if parsed.kind == "replan":
            new_desc = f"{tpl.description}\n[replan by {caller_open_id}] {parsed.payload}".strip()
            new_steps = tpl.steps_json
        elif parsed.kind == "revise":
            new_desc = parsed.payload
            new_steps = tpl.steps_json
        elif parsed.kind == "add-step":
            if tpl.type != "subplan":
                return {"kind": parsed.kind, "status": "failed",
                        "reason": "not_subplan"}
            steps = json.loads(tpl.steps_json or "[]")
            steps.append({"tool": parsed.payload, "args": {}})
            new_desc = tpl.description
            new_steps = json.dumps(steps, ensure_ascii=False)
        else:  # pragma: no cover - 前缀表已限定
            return {"kind": parsed.kind, "status": "failed",
                    "reason": "bad_kind"}

        self.template_repo.upsert(
            template_id=tpl.template_id,
            owner_open_id=tpl.owner_open_id,
            name=tpl.name,
            type_=tpl.type,
            blocks_json=tpl.blocks_json,
            steps_json=new_steps,
            description=new_desc,
            scope=tpl.scope, chat_id=tpl.chat_id,
        )
        ver_no = self.version_service.on_template_upsert(
            template_id=tpl.template_id,
            name=tpl.name, description=new_desc,
            blocks_json=tpl.blocks_json, steps_json=new_steps,
            created_by=caller_open_id,
        )
        return {"kind": parsed.kind, "status": "applied",
                "template_id": tpl.template_id,
                "version_number": ver_no}
