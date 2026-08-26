# ADR-0022: 标签与收藏用独立关联表（非 JSON 字段）

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-26）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 8 |
| 相关 spec | §6 标签与收藏 |

---

## 背景

Phase 7 公共模板只能按名称模糊搜索。Phase 8 增加标签（分类）与收藏（个人维度），需决定存储模型。

---

## 选项

### A. 独立关联表（已选 ✅）

`template_tags`（template_id ↔ tag 多对多，unique(template_id, tag)）+ `template_favorites`（template_id ↔ user_open_id，unique(template_id, user_open_id)）。

- **优点**：可建索引；按标签反查模板是简单 eq 查询；唯一约束天然幂等；后续可扩展（标签计数、收藏排序）
- **缺点**：多 2 张表；join 查询

### B. TemplateRow 加 JSON 字段

`tags: JSON` 数组 + `favorited_by: JSON` 数组。

- **优点**：不加表
- **缺点**：反查（by-tag）需全表扫描 JSON；无唯一约束（应用层幂等易漏）；收藏写放大（所有用户写同一行）

### C. 只做标签不做收藏

- **优点**：减一半工作
- **缺点**：收藏是公共库高频需求（个人常用模板集），不值得砍

---

## 选择：**方案 A（独立关联表）**

语义要点：

1. tag 归一化：`strip().lower()`，防 "Blast" / "blast " 分裂
2. attach/detach 仅 owner 可操作；重复 attach / 不存在 detach 均静默幂等
3. favorite 任意 scope 模板均可（public/user/chat）；幂等
4. `find_by_tag` 返回未归档模板行；不并入 Phase 7 trigram 搜索（独立入口，客户端组合）

---

## 后果

### 正面

- by-tag / favorites 查询走索引，性能可预期
- 唯一约束保证数据层幂等（并发安全）
- 与 13 张既有表风格一致（String PK + ULID）

### 负面

- 表数 13 → 16，迁移面变大（create_all 自动覆盖）

### 中性

- tsvector 统一检索（标签+全文+收藏融合）推迟 Phase 9
- admin 标签清理 / 标签推荐推迟

---

## 回滚条件

1. 单模板标签数暴涨（> 50）导致管理混乱 → 引入 admin 清理 + 标签白名单
2. 若 Phase 9 统一检索落地，by-tag 独立入口可并入（保留表结构不变）
