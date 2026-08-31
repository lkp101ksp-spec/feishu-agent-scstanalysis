# Phase 19：检索与模板升级（裁剪版）— 设计文档

- 日期：2026-09-01
- 状态：已定稿（真机验收推迟）
- 来源：ROADMAP Phase 19（Phase 6/7/9 不做项）

## 1. 范围决策（按 ROADMAP「价值/风险比 + 按使用频率裁剪」原则）

| 候选项 | 决策 | 理由 |
|---|---|---|
| ES / zhparser 拼音检索 | **砍** | 重基建（ES 容器 + 中文分词插件）；现有 PG trigram + search_v2 融合检索无瓶颈证据，无真机使用数据支撑投入 |
| 模板 merge/branch | **砍** | fork 协作流刚落地尚无真机使用；无使用数据的低频功能按 ROADMAP 原则直接不做，待 fork 有真实使用后再评估 |
| 模板标签推荐 | **做** | 轻量纯规则（标签共现 + 热度兜底），无新基建无 LLM 依赖，直接提升打标体验 |

## 2. 标签推荐方案

### 2.1 数据源

- `TemplateTagRepo.list_all()`（新增）：返回全部 `(template_id, tag)` 行——
  单表全量扫描，标签表规模小（模板数量级），无需聚合表。

### 2.2 算法（TagRecommendService，纯规则）

```
输入：template_id, limit（默认 5）
1. own = 该模板已有标签集合
2. 全站标签对统计：
   - 热度：tag → 出现模板数
   - 共现：同模板内标签对 (a, b) 双向计数
3. 候选打分：score(b) = Σ_{a ∈ own} cooc(a, b)   （与已有标签的共现次数和）
4. 共现分 > 0 的按分排序；不足 limit 用全站热度 top 补齐
5. 排除 own 中已有标签，返回前 limit 个（归一化小写）
```

### 2.3 API

- `GET /templates/{template_id}/tag-suggestions?limit=5`
  → `{"template_id": "...", "suggestions": ["pcr", "rna"]}`
- 模板不存在/已归档 → 404；任意人可读（标签本身公开读）。

### 2.4 组装

- runtime：`TagRecommendService(TemplateTagRepo(session))` 注入 create_app。
- AppContext 新增 `tag_recommend_service`。

## 3. 不做项

- LLM 语义标签推荐（规则共现已够用，LLM 版远期有需求再做）。
- 推荐结果缓存（全量扫描规模小，每次实时计算）。
- ES / merge/branch（见范围决策）。

## 4. 测试计划

- [ ] list_all 返回全部行
- [ ] 共现推荐：与已有标签同模板出现的标签优先且按共现分排序
- [ ] 热度兜底：无共现时返回全站热门标签
- [ ] 排除已有标签 / limit 截断
- [ ] API：正常 200、模板不存在 404
- [ ] 回归：全量测试通过

## 5. 风险

- 全量扫描随标签表增长变慢——模板数量级（< 万级）下可忽略，真出现瓶颈再加缓存。
