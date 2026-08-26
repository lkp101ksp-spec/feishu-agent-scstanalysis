# ADR-0027: diff 升级为内容 hash 配对 + moved 检测

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-26）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 9 |
| 相关 spec | §6 diff 升级 |
| 前置 | ADR-0021（v1 同 type 按序配对的已知局限）|

---

## 背景

ADR-0021 v1 算法中"同 type 按出现顺序配对"在块**重排序**时会把未修改的块误报为 `changed`（内容没变只是换了位置）。Phase 9 修复此语义缺陷。

---

## 选项

### A. 内容 hash 两阶段配对 + moved（已选 ✅）

同 type 分组内：阶段1 按内容 hash（`md5(json.dumps(item, sort_keys=True))`）精确配对——命中且 index 变 → `moved`，index 同 → unchanged；阶段2 余量按序配对 → `changed(fields)`；剩余 → added/removed。输出新增 `moved` 键，渲染加 `↔`。

- **优点**：精确区分"改了内容"与"挪了位置"；hash 匹配 O(n)；输出向后兼容（仅增键）
- **缺点**：hash 对键序敏感（sort_keys 已消除）；同内容多副本需按序消耗（已处理）

### B. 全量 LCS / Myers diff

经典 diff 算法求最优对齐。

- **优点**：理论最优
- **缺点**：实现复杂度陡增；对 dict 块仍需自定义相等语义；收益边际低

### C. 保持 v1 不动

- **优点**：零改动
- **缺点**：重排序误报留存，diff 可信度受损

---

## 选择：**方案 A（hash 两阶段配对）**

语义边界（明确写死防歧义）：

1. 内容修改 + 位置同时变 → `changed`（hash 不匹配，落入阶段2 按序配对）
2. 纯位置变 → `moved`
3. 重复块（同 type 同内容多份）→ hash 多对多按序消耗
4. steps（按 `tool` 配对）同走 v2
5. `diff()` 返回仅**新增** `moved` 列表；`added/removed/changed` 键名与语义不变（Phase 8 测试零回归把关）

---

## 后果

### 正面

- 重排序场景 diff 可信（`↔ [0→2] heading` 直观可读）
- 与 rollback 工作流兼容性更好（先看 moved 再决策）
- v1 的分组框架保留，只升级组内配对逻辑

### 负面

- moved 语义让 diff 输出多一类行（渲染与"无差异"判断需同步覆盖）

### 中性

- 跨模板 diff / 语义级相似度（编辑距离）推迟 Phase 10+

---

## 回滚条件

1. hash 序列化在特殊字符/嵌套 dict 上不稳定（测试覆盖后预期不触发）
2. 用户反馈 moved 噪音多于价值 → 渲染层折叠 moved 为汇总行（算法不动）
