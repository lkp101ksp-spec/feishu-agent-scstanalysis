# ADR-009: DocAdapter 6 类 block → 飞书 API 映射策略

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-09）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 5 |
| 相关 spec | §7 DocAdapter 飞书渲染 |

---

## 背景

Phase 5 DocAdapter 需要把我们的 6 类 Block 映射为飞书 doc API 调用。飞书 doc API 的 block 类型映射有两种策略：

1. **类型直映射**：heading level=1 → heading1；heading level=2 → heading2；等
2. **统一映射**：所有 heading → heading2（统一规格）

---

## 选项

### A. 统一映射（heading level=1/2/3 → heading2）

所有 heading → heading2。

- **优点**：实现简单；飞书 doc 看起来更一致
- **缺点**：丢失语义信息（heading level=1 应更突出）

### B. 类型直映射（已选）

heading level=1 → heading1，level=2 → heading2，level=3 → heading3。

- **优点**：保留语义；用户视觉感受一致（与 Markdown 风格一致）
- **缺点**：实现稍复杂

### C. 完全自定义样式

每个 heading level 用自定义字号 + 颜色（不用飞书原生 heading）。

- **优点**：完全控制样式
- **缺点**：破坏飞书 doc 原生体验；用户切换到飞书 app 看到不同样式

---

## 选择：**方案 B（类型直映射）**

---

## 后果

### 正面

- 语义保留（heading level 视觉上一致）
- 与 Markdown / 通用富文本风格一致
- 用户从飞书 app 看 doc 与 Agent 写入样式一致

### 负面

- 实现稍复杂（需 dispatch heading level）
- 飞书 doc API 的某些边界情况（如 level=1 vs level=3 视觉差异）需用户验证

### 中性

- DocRateLimiter 包装（复用 Phase 1）保障 3 req/s

---

## 回滚条件

1. 用户反馈"heading level 视觉差异太大 / 太小"
2. 飞书 doc API heading level 不支持（如新版 API 改为 heading 块）

回滚方案：调整 `_to_feishu_payload()` 映射函数（不影响外部接口）。