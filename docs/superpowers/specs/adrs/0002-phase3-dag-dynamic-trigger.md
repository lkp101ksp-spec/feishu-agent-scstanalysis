# ADR-002: DAG 动态化触发机制

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-09）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 3 |
| 相关 spec | §3.3 动态节点追加流程 / §7.4 branch LLM 调用 |

---

## 背景

Phase 3 引入 branch 节点：执行到 branch 时，根据条件判定决定后续节点是否执行。两种触发机制可选：

1. **LLM 决策**：调 condition_eval role 的轻量模型
2. **表达式解析**：用户/Planner 写 Python 表达式，Runtime 静态解析

---

## 选项

### A. LLM 决策（已选）

branch 节点 condition_eval LLM 调用。轻量模型（如 Haiku / Mini）单次决策，token 成本 < 200。

- **优点**：表达力强；条件可以是任意自然语言；用户/LLM 写 prompt 而非代码
- **缺点**：增加 1 次 LLM 调用；决策延迟约 1-2 秒；决策可能不稳定

### B. 表达式解析

condition 是 Python 表达式（如 `len(blocks) > 0`），Runtime 静态求值。

- **优点**：快（< 10ms）；确定性
- **缺点**：能力受限；不能调 LLM；不能跨节点聚合；维护复杂

### C. 混合

LLM 决策 + 简单表达式 fallback。

- **优点**：兼顾两边
- **缺点**：复杂度高；不易调试

---

## 选择：**方案 A（LLM 决策）**

---

## 后果

### 正面

- 表达力强（自然语言条件）
- 决策可审计（audit_logs 记录 prompt 与 response）
- 与 Phase 4 RAG / 工具决策复用同一 LLM 通道

### 负面

- 每次 branch 决策增加 ~1-2s 延迟
- 增加 token 成本（每个 Plan 平均 +0.2 元 RMB）
- LLM 决策不稳 → 必须有 fallback（异常退化为 "false"）

### 中性

- LLMRouter 增加 `role_condition_eval` role；primary/fallback 配置独立

---

## 回滚条件

1. LLM 决策延迟导致用户体验明显变差（> 5s/branch）
2. 决策准确率 < 70%（用户反馈"经常选错分支"）
3. token 成本 > 0.5 元/Plan（预算失控）

回滚方案：切换到方案 B（表达式解析），需修改 Planner 输出 + Runtime 实现。