# ADR-018: fork 仅 public + lineage 不可改

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-09）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 7 |
| 相关 spec | §6 模板 fork |

---

## 背景

Phase 7 引入模板 fork。需要决定 fork 来源限制与 lineage 行为。

---

## 选项

### A. 仅 fork public + lineage 不可改（已选 ✅）

仅 public 可 fork；lineage 字段由 ForkService 独占写入。

- **优点**：lineage 不可伪造；fork 链清晰
- **缺点**：私有 / 群聊共享不能 fork

### B. 所有模板可 fork + lineage 可改

- **优点**：复用场景广
- **缺点**：lineage 可能伪造；fork 链混乱

### C. 全做（merge / branch / conflict）

- **优点**：完整版本控制
- **缺点**：复杂度爆炸；Phase 8 推迟

---

## 选择：**方案 A（仅 public + lineage 不可改）**

---

## 后果

### 正面

- lineage 字段可信（仅 ForkService 写入）
- fork 链清晰（public → user）
- 简化实现（无需 merge / conflict）

### 负面

- 私有 / 群聊共享不能 fork
- fork 的 fork 不允许（user scope 不能再 fork）

### 中性

- Phase 8 可扩展 merge / branch

---

## 回滚条件

1. 用户反馈"私有模板也需要 fork"（≥ 30%）
2. lineage 字段成为高频需求（≥ 50% 用户关心）

回滚方案：Phase 8 引入全 scope fork + lineage UI。