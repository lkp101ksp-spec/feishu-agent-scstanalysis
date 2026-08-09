# ADR-006: 富文本 block 类型选型（6 类 vs 完整 12 类）

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-09）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 5 |
| 相关 spec | §3 富文本块 schema |

---

## 背景

Phase 5 引入富文本块，让 Plan 结果以结构化形式呈现。飞书 doc API 支持的 block 类型有 12+ 类，需要选择 MVP 覆盖范围：

1. **基础 6 类**：heading + text + code + quote + table + list + image
2. **完整 12+ 类**：6 类 + embed + divider + callout + equation + 视频 + 文件附件

---

## 选项

### A. 完整 12+ 类

一次性覆盖所有飞书 doc block 类型。

- **优点**：未来不必扩展
- **缺点**：MVP 实现复杂、测试覆盖翻倍、与 Phase 6 边界模糊

### B. 基础 6 类（已选）

Phase 5 仅覆盖最常用的 6 类。

- **优点**：实现聚焦、测试 90% 覆盖、Phase 6 增量清晰
- **缺点**：用户首次接触新模板时可能需要"升级到 Phase 6"

### C. 8 类（基础 + embed + divider）

折中方案。

- **优点**：覆盖更全
- **缺点**：8 类无明显边界；不如 6/12 二分

---

## 选择：**方案 B（基础 6 类）**

---

## 后果

### 正面

- 实现聚焦：6 类 schema + 测试清晰
- Phase 6 边界明确：剩余 6 类增量（embed / divider / callout / equation / 视频 / 文件附件）
- 飞书 doc API 6 类是事实标准（业界最常用）

### 负面

- Phase 5 用户首次接触富文本时若需要其他类型 → 等待 Phase 6
- 子流程中工具直接产出 blocks 时受 6 类限制

### 中性

- Pydantic Union 类型便于 Phase 6 增量添加新 block 类型

---

## 回滚条件

1. Phase 5 上线后用户反馈"6 类不够用"（频次 ≥ 20% 用户）
2. 飞书 doc API 强制要求 6 类外的 block（如 equation 必须存在）
3. 6 类实现复杂度超预期（ > 1000 LOC + 200 测试）

回滚方案：增量添加 Phase 6 block 类型（与本 ADR 兼容，不需回滚 schema）。