# ADR-016: 模板搜索用 PG trigram + LIKE

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-09）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 7 |
| 相关 spec | §4 模板搜索 |

---

## 背景

Phase 7 引入模板搜索。需要选择搜索技术。

---

## 选项

### A. PG trigram + LIKE（已选 ✅）

启用 `pg_trgm` extension；name 列加 GIN trigram 索引；查询 = `name ILIKE '%q%' OR description ILIKE '%q%'`。

- **优点**：无额外依赖；中英文混合支持；MVP 足够
- **缺点**：不支持语义搜索；中文分词需另加方案

### B. PostgreSQL tsvector（全文检索）

启用 `tsvector` + 中文分词（如 zhparser）。

- **优点**：语义匹配；性能更好
- **缺点**：需额外扩展；中文分词依赖

### C. Elasticsearch

重量级。

- **优点**：强大分析能力
- **缺点**：MVP 杀鸡用牛刀；运维成本高

---

## 选择：**方案 A（PG trigram + LIKE）**

---

## 后果

### 正面

- 无额外依赖；MVP 快速落地
- 支持中英文、数字混合查询
- 性能对 1k 模板足够（GIN 索引）

### 负面

- 不支持语义匹配
- 中文模糊匹配依赖 trigram（3 字以下匹配差）

### 中性

- Phase 8 可升级到 tsvector / ES

---

## 回滚条件

1. 1k 模板后查询延迟 > 1s（≥ 10% 用户反馈）
2. 模糊匹配准确率低（用户反馈 ≥ 30%）

回滚方案：迁移到 PostgreSQL tsvector + 中文分词。