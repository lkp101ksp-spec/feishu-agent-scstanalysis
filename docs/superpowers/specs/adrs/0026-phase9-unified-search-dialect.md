# ADR-0026: 统一检索单入口 + 方言分支降级（PG tsvector / SQLite LIKE）

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-26）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 9 |
| 相关 spec | §5 tsvector 统一检索 |

---

## 背景

Phase 7 trigram 搜索与 Phase 8 by-tag 是割裂入口，无法"一次查询融合相关度 + 标签 + 热度"。引入 tsvector 面临现实约束：**测试环境是 SQLite（无 tsvector），生产是 PG**，且项目承诺双库兼容（models.py 头注）。

---

## 选项

### A. 融合单入口 + 方言分支（已选 ✅）

`search-v2` 一个入口：全文按方言分支（PG `tsvector @@ plainto_tsquery + ts_rank`；SQLite 降级 `ILIKE`，name 命中=2 / 仅 desc=1），融合公式统一 `text_score + LEAST(fav_count,5)*0.5`，支持 tag EXISTS 过滤与 scope。旧接口保留。

- **优点**：测试全量可跑（SQLite 路径）；生产 PG 获得真全文能力；旧接口零破坏
- **缺点**：两分支需分别维护；SQLite 分支相关度是启发式（非真全文）

### B. 仅 PG tsvector（放弃双库）

测试全部要求 PG。

- **优点**：单实现
- **缺点**：破坏项目 SQLite/PG 双兼容承诺；本地测试基础设施全废

### C. 只做融合不引 tsvector（两侧都 LIKE）

- **优点**：最简单
- **缺点**：放弃 PG 全文能力，中文长文本检索质量差；"tsvector 统一检索"目标落空

---

## 选择：**方案 A（方言分支）**

实现要点：

1. 分支判据：`session.bind.dialect.name == "postgresql"`（`_is_postgres` 独立函数可单测）
2. 融合排序两方言共用：`text_score + LEAST(fav_count, 5) * 0.5`，次序键 `updated_at DESC`
3. `query` 为空 → 不筛全文，纯热度序（收藏维度单独可用）
4. tag 归一化 lower（沿用 ADR-0022）；scope 等值过滤；恒定排除 archived
5. 生产 PG 建议补 GIN 表达式索引（迁移脚本 Phase 10 联调时落地）
6. **测试策略**：SQLite 路径真库全量测；PG 路径做分支分发冒烟；真 PG 验证列 Phase 10 清单——诚实标注"PG 分支未经真库执行"

---

## 后果

### 正面

- 单入口覆盖三维度（相关度 / 标签 / 热度），客户端不再组合
- 测试矩阵保持 SQLite 全绿，CI 零基础设施变更
- 旧 `/templates/search`、`/templates/by-tag` 保留，渐进迁移

### 负面

- repo 内两分支逻辑（~30 行）需同步维护
- PG 分支上线前未经真库验证（风险已记录并列入联调清单）

### 中性

- zhparser 中文分词 / ES / 拼音推迟 Phase 10+

---

## 回滚条件

1. 真 PG 联调发现 tsvector 分支行为异常 → 临时切 dialect 分支为 LIKE（一行开关），修复后切回
2. 中文检索质量不达标 → Phase 10 评估 zhparser 或 ES
