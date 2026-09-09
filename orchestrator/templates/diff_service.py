"""Phase 8 T6 / Phase 9 v2: 版本块级结构化 diff。

v1（ADR-0021）：同 type/tool 按序配对。
v2（ADR-0027）：组内两阶段——先按内容 hash 精确配对（moved/unchanged），
余量再按序配对（changed）。输出新增 moved 键，旧键语义不变。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from persistence.repositories.template_repo import TemplateRepo
from persistence.repositories.template_version_repo import TemplateVersionRepo


def _content_hash(item: dict[str, Any]) -> str:
    """块的稳定内容指纹（键序无关）。"""
    return hashlib.md5(
        json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _diff_lists(a: list[dict[str, Any]], b: list[dict[str, Any]],
                key: str) -> dict[str, list[dict[str, Any]]]:
    """两阶段配对 diff（ADR-0027）。

    阶段1：组内同内容 hash 精确配对 → index 变化为 moved，相同为 unchanged；
    阶段2：余量按序配对 → 键集差异为 changed；
    剩余：b 侧 added，a 侧 removed。
    """
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []

    # 分组：key 值 → (a 侧队列, b 侧队列)
    groups: dict[str, tuple[
        list[tuple[int, dict[str, Any]]],
        list[tuple[int, dict[str, Any]]],
    ]] = {}
    for i, item in enumerate(a):
        k = str(item.get(key, ""))
        groups.setdefault(k, ([], []))[0].append((i, item))
    for i, item in enumerate(b):
        k = str(item.get(key, ""))
        groups.setdefault(k, ([], []))[1].append((i, item))

    for _, (qa, qb) in groups.items():
        # 阶段1：hash 精确配对（a 侧队列被消耗）
        b_by_hash: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for ib, item_b in qb:
            b_by_hash.setdefault(_content_hash(item_b), []).append((ib, item_b))
        remaining_a: list[tuple[int, dict[str, Any]]] = []
        consumed_b: set[int] = set()
        for ia, item_a in qa:
            h = _content_hash(item_a)
            candidates = [c for c in b_by_hash.get(h, []) if c[0] not in consumed_b]
            if candidates:
                ib, _ = candidates[0]
                consumed_b.add(ib)
                if ia != ib:
                    moved.append({
                        "index_a": ia, "index_b": ib,
                        "type": item_a.get(key),
                    })
                # index 相同 → unchanged，不进输出
            else:
                remaining_a.append((ia, item_a))
        remaining_b = [(ib, item) for ib, item in qb if ib not in consumed_b]

        # 阶段2：余量按序配对 → changed
        paired = min(len(remaining_a), len(remaining_b))
        for (ia, item_a), (ib, item_b) in zip(
                remaining_a[:paired], remaining_b[:paired]):
            fields = sorted(
                {k for k in set(item_a) | set(item_b)
                 if item_a.get(k) != item_b.get(k)}
            )
            if fields:
                changed.append({
                    "index_a": ia, "index_b": ib,
                    "type": item_b.get(key), "fields": fields,
                })
        for ia, _item in remaining_a[paired:]:
            removed.append({"index_a": ia, "type": _item.get(key)})
        for ib, _item in remaining_b[paired:]:
            added.append({"index_b": ib, "type": _item.get(key)})

    return {"added": added, "removed": removed,
            "changed": changed, "moved": moved}


def _loads_or_empty(raw: Optional[str]) -> list[dict[str, Any]]:
    if not raw:
        return []
    loaded: list[dict[str, Any]] = json.loads(raw)
    return loaded


class VersionDiffService:
    """模板两版本块级 diff（blocks 按 type 配对，steps 按 tool 配对）。"""

    def __init__(self, version_repo: TemplateVersionRepo,
                 template_repo: Optional[TemplateRepo] = None) -> None:
        self.version_repo = version_repo
        self.template_repo = template_repo

    def diff(self, *, template_id: str, v_a: int, v_b: int) -> dict[str, Any]:
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

    def render(self, diff: dict[str, Any]) -> str:
        """diff dict → IM 文本（+ / - / ~ / ↔）。"""
        lines = [f"模板 {diff['template_id']} 版本 {diff['v_a']} → "
                 f"{diff['v_b']} diff："]
        for blk in diff["blocks"]["added"]:
            lines.append(f"+ [{blk['index_b']}] {blk['type']}")
        for blk in diff["blocks"]["removed"]:
            lines.append(f"- [{blk['index_a']}] {blk['type']}")
        for blk in diff["blocks"]["changed"]:
            lines.append(f"~ [{blk['index_b']}] {blk['type']} "
                         f"(字段: {', '.join(blk['fields'])})")
        for blk in diff["blocks"].get("moved", []):
            lines.append(f"↔ [{blk['index_a']}→{blk['index_b']}] {blk['type']}")
        steps = diff["steps"]
        if (steps["added"] or steps["removed"] or steps["changed"]
                or steps.get("moved")):
            lines.append(f"steps: + {len(steps['added'])} / "
                         f"- {len(steps['removed'])} / "
                         f"~ {len(steps['changed'])} / "
                         f"↔ {len(steps.get('moved', []))}")
        meta = diff["meta"]
        if meta["name_changed"]:
            lines.append(f"~ name: {meta['name_a']} → {meta['name_b']}")
        if meta["description_changed"]:
            lines.append("~ description 已变更")
        if len(lines) == 1:
            lines.append("（无差异）")
        return "\n".join(lines)
