"""Phase 8 T6: 版本块级结构化 diff（同 type/tool 按序配对，ADR-0021）。"""
from __future__ import annotations

import json
from typing import Optional


def _diff_lists(a: list[dict], b: list[dict], key: str) -> dict:
    """按 key 值分组配对（同值按出现顺序），输出 added/removed/changed。"""
    added: list[dict] = []
    removed: list[dict] = []
    changed: list[dict] = []

    # 分组：key 值 → (a 侧队列, b 侧队列)
    groups: dict[str, tuple[list[tuple[int, dict]], list[tuple[int, dict]]]] = {}
    for i, item in enumerate(a):
        k = str(item.get(key, ""))
        groups.setdefault(k, ([], []))[0].append((i, item))
    for i, item in enumerate(b):
        k = str(item.get(key, ""))
        groups.setdefault(k, ([], []))[1].append((i, item))

    for _, (qa, qb) in groups.items():
        paired = min(len(qa), len(qb))
        for (ia, item_a), (ib, item_b) in zip(qa[:paired], qb[:paired]):
            fields = sorted(
                {k for k in set(item_a) | set(item_b)
                 if item_a.get(k) != item_b.get(k)}
            )
            if fields:
                changed.append({
                    "index_a": ia, "index_b": ib,
                    "type": item_b.get(key), "fields": fields,
                })
        for ia, _item in qa[paired:]:
            removed.append({"index_a": ia, "type": _item.get(key)})
        for ib, _item in qb[paired:]:
            added.append({"index_b": ib, "type": _item.get(key)})

    return {"added": added, "removed": removed, "changed": changed}


def _loads_or_empty(raw: Optional[str]) -> list[dict]:
    if not raw:
        return []
    return json.loads(raw)


class VersionDiffService:
    """模板两版本块级 diff（blocks 按 type 配对，steps 按 tool 配对）。"""

    def __init__(self, version_repo, template_repo=None) -> None:
        self.version_repo = version_repo
        self.template_repo = template_repo

    def diff(self, *, template_id: str, v_a: int, v_b: int) -> dict:
        va = self.version_repo.get_by_version(template_id, v_a)
        vb = self.version_repo.get_by_version(template_id, v_b)
        if va is None or vb is None:
            raise ValueError(
                f"version {v_a if va is None else v_b} of "
                f"{template_id} not found"
            )
        return {
            "template_id": template_id,
            "v_a": v_a, "v_b": v_b,
            "blocks": _diff_lists(
                _loads_or_empty(va.blocks_json),
                _loads_or_empty(vb.blocks_json),
                key="type",
            ),
            "steps": _diff_lists(
                _loads_or_empty(va.steps_json),
                _loads_or_empty(vb.steps_json),
                key="tool",
            ),
            "meta": {
                "name_changed": va.name != vb.name,
                "name_a": va.name, "name_b": vb.name,
                "description_changed": va.description != vb.description,
            },
        }

    def render(self, diff: dict) -> str:
        """diff dict → IM 文本（+ / - / ~）。"""
        lines = [f"模板 {diff['template_id']} 版本 {diff['v_a']} → "
                 f"{diff['v_b']} diff："]
        for blk in diff["blocks"]["added"]:
            lines.append(f"+ [{blk['index_b']}] {blk['type']}")
        for blk in diff["blocks"]["removed"]:
            lines.append(f"- [{blk['index_a']}] {blk['type']}")
        for blk in diff["blocks"]["changed"]:
            lines.append(f"~ [{blk['index_b']}] {blk['type']} "
                         f"(字段: {', '.join(blk['fields'])})")
        steps = diff["steps"]
        if steps["added"] or steps["removed"] or steps["changed"]:
            lines.append(f"steps: + {len(steps['added'])} / "
                         f"- {len(steps['removed'])} / "
                         f"~ {len(steps['changed'])}")
        meta = diff["meta"]
        if meta["name_changed"]:
            lines.append(f"~ name: {meta['name_a']} → {meta['name_b']}")
        if meta["description_changed"]:
            lines.append("~ description 已变更")
        if len(lines) == 1:
            lines.append("（无差异）")
        return "\n".join(lines)
