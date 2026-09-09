"""Phase 19: 模板标签推荐（共现 + 热度兜底，纯规则无 LLM，spec §2.2）。"""
from __future__ import annotations

from collections import Counter, defaultdict

from persistence.repositories.template_repo import TemplateRepo
from persistence.repositories.template_tag_repo import TemplateTagRepo


class TagRecommendService:
    """基于全站标签共现的推荐：与已有标签同模板出现的标签优先。"""

    def __init__(self, tag_repo: TemplateTagRepo,
                 template_repo: TemplateRepo | None = None) -> None:
        self.tag_repo = tag_repo
        self.template_repo = template_repo

    def suggest(self, *, template_id: str, limit: int = 5) -> list[str]:
        """返回推荐标签（共现分排序，热度兜底，排除已有，截断 limit）。

        模板不存在/已归档时 template_repo 给定则抛 ValueError（调用方 404）；
        template_repo 为 None（测试/降级）跳过校验。
        """
        if self.template_repo is not None:
            tpl = self.template_repo.get(template_id)
            if tpl is None or getattr(tpl, "archived_at", None) is not None:
                raise ValueError(f"template {template_id} not found")

        pairs = self.tag_repo.list_all()
        # 同模板标签分组
        by_tpl: dict[str, list[str]] = defaultdict(list)
        popularity: Counter[str] = Counter()
        for tid, tag in pairs:
            by_tpl[tid].append(tag)
            popularity[tag] += 1

        own = set(by_tpl.get(template_id, []))

        # 共现分：与已有标签同模板出现的标签按次数累加
        cooc: Counter[str] = Counter()
        for tid, tags in by_tpl.items():
            if tid == template_id:
                continue  # 自己模板内的标签即已有标签，不计
            tag_set = set(tags)
            if not (tag_set & own):
                continue  # 与已有标签无交集的模板不贡献共现
            for tag in tag_set - own:
                cooc[tag] += len(tag_set & own)

        # 共现优先；不足 limit 用全站热度补齐（排除已有）
        ranked = [t for t, _ in cooc.most_common()]
        if len(ranked) < limit:
            fallback = [t for t, _ in popularity.most_common()
                        if t not in own and t not in ranked]
            ranked.extend(fallback)
        return ranked[:limit]
