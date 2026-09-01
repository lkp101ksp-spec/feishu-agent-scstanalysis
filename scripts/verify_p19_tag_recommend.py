"""P19 真机验证：TagRecommendService 推荐效果抽查（连真实 PG，事务回滚不落库）。"""
from collections import OrderedDict

from sqlalchemy.orm import sessionmaker

from orchestrator.templates.tag_recommend_service import TagRecommendService
from persistence.engine import get_engine
from persistence.models import TemplateRow, TemplateTagRow
from persistence.repositories.template_repo import TemplateRepo
from persistence.repositories.template_tag_repo import TemplateTagRepo


def main() -> None:
    """造 6 模板共现数据 → 验证共现优先/热度兜底/排除已有/404 四类行为后回滚。"""
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)
    s = factory()
    try:
        tag_repo = TemplateTagRepo(s)
        tpl_repo = TemplateRepo(s)
        svc = TagRecommendService(tag_repo, tpl_repo)

        # 造数：T1 目标模板（bio 分析类）；T2/T3 与其强共现；T4/T5 无交集；T6 热门单标签
        seeds = OrderedDict([
            ("zz_t1", ["单细胞", "转录组"]),          # 目标
            ("zz_t2", ["单细胞", "转录组", "聚类", "UMAP"]),
            ("zz_t3", ["单细胞", "QC", "聚类"]),
            ("zz_t4", ["空间转录组", "Visium"]),
            ("zz_t5", ["文献检索", "PubMed"]),
            ("zz_t6", ["热门标签", "X", "Y", "Z"]),  # 4 模板都打热门标签见下
        ])
        for tid, tags in seeds.items():
            if not tpl_repo.get(tid):
                s.add(TemplateRow(template_id=tid, owner_open_id="zz_test",
                                  name=tid, type="blocks", blocks_json="[]"))
            for tag in tags:
                tag_repo.add(tag_id=f"zz_{tid}_{tag}", template_id=tid,
                             tag=tag, created_by="zz_test")
        # 热门标签：再挂 3 个模板使其成为全站最热
        for tid in ("zz_t2", "zz_t4", "zz_t5"):
            tag_repo.add(tag_id=f"zz_{tid}_hot", template_id=tid,
                         tag="热门标签", created_by="zz_test")
        s.flush()

        # ① 共现优先：T1 已有 单细胞+转录组 → 聚类共现分3最高排第一；
        #   UMAP/热门标签(挂T2,交集2) 并列2分；QC(交集1)其后；第5位热度兜底
        got = svc.suggest(template_id="zz_t1", limit=5)
        print("① 共现+热度兜底:", got)
        assert got[0] == "聚类", got
        assert {"UMAP", "热门标签", "QC"} <= set(got[:4]), got
        assert got.index("聚类") < got.index("QC"), got

        # ② limit 截断
        got3 = svc.suggest(template_id="zz_t1", limit=3)
        print("② limit=3 截断:", got3)
        assert got3 == got[:3] and len(got3) == 3

        # ③ 排除已有：无交集的 Visium/文献检索 不出现（除非兜底挤入，limit=5 不会）
        print("③ 无交集标签未混入:", all(t not in got for t in ("Visium", "文献检索")))
        assert all(t not in got for t in ("Visium", "文献检索"))

        # ④ 零标签模板（own 为空 → 无共现可言）→ 纯热度推荐，最热=热门标签(4模板)
        s.add(TemplateRow(template_id="zz_t7", owner_open_id="zz_test",
                          name="zz_t7", type="blocks", blocks_json="[]"))
        s.flush()
        got_empty = svc.suggest(template_id="zz_t7", limit=3)
        print("④ 热度兜底路径:", got_empty)
        assert got_empty and got_empty[0] == "热门标签"

        # ⑤ 模板不存在 → ValueError（API 层 404）
        try:
            svc.suggest(template_id="zz_missing")
            raise AssertionError("should raise")
        except ValueError as e:
            print("⑤ 不存在模板 → ValueError:", e)

        print("ALL PASS")
    finally:
        s.rollback()
        s.close()


if __name__ == "__main__":
    main()
