# ADR-008: sub-Plan 模板与现有 DAG 的关系

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-09）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 5 |
| 相关 spec | §4 模板市场 + §8 Scheduler 展开 |

---

## 背景

Phase 5 sub-Plan 模板复用现有 Phase 4 tool_name。两种实现方式：

1. **引用现有 tool_name**：模板存 `tool_name` + `inputs` 占位符；展开时由 Planner 调用现有工具
2. **内嵌 Python**：模板自带执行逻辑

---

## 选项

### A. 内嵌 Python（已选 ❌）

模板内嵌 Python 代码；运行时执行。

- **优点**：表达力最强
- **缺点**：**重大安全风险**（AST P0 拦截+ P1/P2 提示在 Phase 3 已实施；模板不应绕过）；用户上传 .py 文件 = 远程代码执行

### B. 引用现有 tool_name（已选 ✅）

模板存 `tool_name` + `inputs` 占位符；展开时由 Planner 调用现有工具。

- **优点**：安全边界清晰（与 Phase 1-4 tool 注册一致）；用户只能复用已有工具
- **缺点**：表达力受限（不能调 LLM 直生成）

### C. 嵌套 PlanRuntime

sub-Plan 模板可嵌入另一层 PlanRuntime。

- **优点**：嵌套可执行复杂子流程
- **缺点**：实现复杂；状态机复杂度翻倍；Phase 5 范围爆炸

---

## 选择：**方案 B（引用现有 tool_name）**

---

## 后果

### 正面

- 安全边界清晰：模板仅复用 Phase 1-4 已注册的 tool；Phase 3 AST 守卫仍生效
- 实现简单：sub-Plan 模板展开 = DAGNode 列表构造
- 与 Phase 4 BLAST 工具无缝集成

### 负面

- 不能"模板中嵌入 LLM 调用"（仅能复用现有 tool_name）
- 用户上传模板的内容受 Phase 1-4 工具清单限制

### 中性

- Phase 6 可引入 "llm_call" 虚拟 tool_name（统一为 tool）

---

## 回滚条件

1. 用户反馈"模板需要嵌入 LLM"需求 ≥ 5%
2. Phase 5 模板场景用例 80% 需要 LLM 调用

回滚方案：Phase 6 引入 `llm_call` tool 注册（虚拟 tool_name），与本 ADR 兼容。