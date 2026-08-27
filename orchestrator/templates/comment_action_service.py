"""Phase 8 T5: 评论动作服务（前缀解析 + owner apply + 打标，ADR-0020/0023）。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

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

    def __init__(self, comment_repo, template_repo, version_service) -> None:
        self.comment_repo = comment_repo
        self.template_repo = template_repo
        self.version_service = version_service

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
            else:
                failed += 1
        return {"applied": applied, "skipped": skipped,
                "failed": failed, "details": details}

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
        self.version_service.on_template_upsert(
            template_id=tpl.template_id,
            name=tpl.name, description=new_desc,
            blocks_json=tpl.blocks_json, steps_json=new_steps,
            created_by=caller_open_id,
        )
        return {"kind": parsed.kind, "status": "applied"}
