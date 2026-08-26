# ADR-0021: 版本块级结构化 diff（非文本 unified diff）

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-26）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 8 |
| 相关 spec | §5 版本 diff |

---

## 背景

Phase 6 版本系统只有回滚（rollback），无法回答"两个版本改了什么"。Phase 8 补 diff 能力，需决定 diff 粒度。

---

## 选项

### A. 块级结构化 diff（已选 ✅）

按 (type) 分组配对、同 type 按出现顺序对齐；输出 added / removed / changed（含字段名）+ IM 渲染。

- **优点**：结构信息完整（直接复用 14 类 block schema）；字段级摘要精确（"text 变了"而非一大段红线）；IM 渲染紧凑
- **缺点**：块重排序时可能误报 changed（同 type 按序配对的局限）

### B. 渲染后文本 unified diff（difflib）

两版本渲染为文本 → unified diff。

- **优点**：通用、用户熟悉 diff 格式
- **缺点**：丢结构；长文档 diff 噪音大；IM 展示超长

### C. 元数据级 diff（名称/步骤数）

只比 name / description / 步骤数。

- **优点**：最简
- **缺点**：太粗，无实操价值

---

## 选择：**方案 A（块级结构化 diff）**

对齐算法（MVP，LCS 简化版）：

1. blocks 按 `type` 分组配对，同 type 按出现顺序一一配对
2. 配对块比较 dict 键集 → 差异键名记入 `changed.fields`
3. v_b 未配对 → `added`；v_a 未配对 → `removed`
4. steps 按 `tool` 键同理
5. meta：name / description 是否变化

---

## 后果

### 正面

- diff 输出为结构化 dict，API 直接消费；IM 渲染仅是投影
- 与 rollback 组成完整版本工作流（diff 确认 → rollback 决策）
- 同版本/空 blocks 等边界清晰可测

### 负面

- 块移动（重排序）会报 changed 而非 moved（无 move 语义）
- 大量同 type 块交错改动时对齐可能不准

### 中性

- 全量 LCS / Myers diff / move 检测推迟 Phase 9
- diff 仅展示用途，不驱动回滚逻辑

---

## 回滚条件

1. 用户反馈重排序误报不可接受（≥ 20%）
2. 届时升级为按 (type, 内容 hash) 配对或引入 difflib.SequenceMatcher
