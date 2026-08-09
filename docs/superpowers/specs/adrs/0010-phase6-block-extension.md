# ADR-010: 富文本 14 类 vs 仅扩展 4 类

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-09）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 6 |
| 相关 spec | §3 富文本扩展 |

---

## 背景

Phase 5 6 类 block（heading/text/code/quote/table/list/image）。Phase 6 需要扩展。飞书 doc API 支持 14+ 类 block：

- Phase 5 已实现：6 类
- Phase 6 增量 8 类：embed + divider + callout + equation + math + Mermaid + 视频 + 文件附件

---

## 选项

### A. 仅扩展 4 类（embed + divider + callout + equation）

只加最常用的 4 类。

- **优点**：实现聚焦、测试覆盖窄
- **缺点**：用户首次接触富文本时可能缺 math / 视频

### B. 14 类总（已选）

Phase 5 6 类 + Phase 6 8 类 = 14 类全覆盖。

- **优点**：一次性解锁最强表达力（math + 视频 + 文件附件）
- **缺点**：实现复杂、测试覆盖翻倍

### C. 折中（8 类增量）

只加 Phase 6 8 类，不覆盖 Phase 5 已有的。

- **优点**：与 B 等价
- **缺点**：实质等同 B

---

## 选择：**方案 B（14 类总）**

---

## 后果

### 正面

- 一次性解锁 14 类；与飞书 doc API 能力对齐
- math / 视频 / 文件附件是科研场景硬需求
- Mermaid 退化方案降低用户期待

### 负面

- 实现周期长（~1 周）
- 测试覆盖翻倍（~24 新测试）

### 中性

- Pydantic Union 类型便于 Phase 7 增量添加

---

## 回滚条件

1. 用户反馈"8 类中部分不需要"（≥ 30%）
2. 14 类实现复杂度超预期（> 800 LOC + 250 测试）
3. 飞书 doc API 强制要求某类（无）

回滚方案：保留已实现的 8 类；从 Phase 7 spec 删除未使用的。