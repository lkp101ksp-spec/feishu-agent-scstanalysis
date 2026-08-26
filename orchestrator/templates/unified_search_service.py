"""Phase 9 T6: 融合检索服务（全文 + 标签 + 收藏 boost，ADR-0026）。"""
from __future__ import annotations

from typing import Optional


class UnifiedSearchService:
    """统一检索单入口：包装 TemplateRepo.search_v2 并补齐展示字段。"""

    def __init__(self, template_repo, tag_repo=None, favorite_repo=None) -> None:
        self.template_repo = template_repo
        self.tag_repo = tag_repo
        self.favorite_repo = favorite_repo

    def search(
        self, *,
        query: str = "",
        tag: Optional[str] = None,
        scope: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """返回 [{template_id, name, score, tags, favorite_count}]。"""
        normalized_tag = tag.strip().lower() if tag else None
        rows = self.template_repo.search_v2(
            query=query, tag=normalized_tag, scope=scope,
            limit=limit, offset=offset,
        )
        ids = [tpl.template_id for tpl, _ in rows]
        fav_counts = (
            self.favorite_repo.counts_by_templates(ids)
            if self.favorite_repo is not None else {}
        )
        out = []
        for tpl, score in rows:
            tags = (
                self.tag_repo.list_by_template(tpl.template_id)
                if self.tag_repo is not None else []
            )
            out.append({
                "template_id": tpl.template_id,
                "name": tpl.name,
                "score": round(score, 2),
                "tags": tags,
                "favorite_count": fav_counts.get(tpl.template_id, 0),
            })
        return out

    def render(self, results: list[dict]) -> str:
        """IM 文本渲染：- name (score=x.x, ❤N, #tags)。"""
        if not results:
            return "（无匹配模板）"
        lines = [f"检索结果（{len(results)} 条）："]
        for r in results:
            tag_part = f", #{','.join(r['tags'])}" if r["tags"] else ""
            lines.append(
                f"- {r['name']} (score={r['score']:.1f}, "
                f"❤{r['favorite_count']}{tag_part})"
            )
        return "\n".join(lines)
