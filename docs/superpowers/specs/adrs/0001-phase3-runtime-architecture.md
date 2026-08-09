# ADR-001: Runtime 抽象抽离 vs 原地增量

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-09）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 3 |
| 相关 spec | §3 PlanRuntime |

---

## 背景

Phase 2 Scheduler 已承担：
- ready 节点识别
- 节点提交给 Executor
- 状态机推进
- 终态汇总

Phase 3 新增 3 类行为：
1. **动态节点追加**：branch 条件为真时插入新节点
2. **循环节点**：while / for 反复触发同一组节点
3. **session 冻结**：超上下文时把 session 标 archived，开新 session

如果继续往 Scheduler 加代码，文件会从 ~200 行膨胀到 ~600 行；同时 ready/cancel/finish 三种状态推进逻辑混杂动态性，单元测试难以隔离。

---

## 选项

### A. 原地增量

继续往 Phase 2 Scheduler 加 if/elif 分支处理动态追加 / 循环 / 冻结。

- **优点**：改动最小；Phase 2 测试零回归
- **缺点**：Scheduler 单文件膨胀；动态化逻辑与 ready/cancel 混杂；不易测

### B. 抽 PlanRuntime（已选）

新增 `orchestrator/runtime/plan_runtime.py`，把"ready/cancel/finish + 动态性"全部抽走。Scheduler 退化为 Runtime 的驱动循环。

- **优点**：边界清晰；Runtime 单独可测；Phase 5 gRPC 拆分时正好以 Runtime 为边界
- **缺点**：+3 个新 Task（Runtime 抽象 / 接口契约 / 状态机测试）

### C. 引入响应式流（rxpy / aiostream）

把节点事件建模为流，动态追加 / 循环 / 冻结都是流算子。

- **优点**：表达力最强
- **缺点**：新依赖；Phase 2 现有测试几乎全要改；Phase 3 范围爆炸

---

## 选择：**方案 B（抽 PlanRuntime）**

---

## 后果

### 正面

- Scheduler / Runtime 边界清晰，单元测试可独立
- PlanRuntime 是 Phase 5 gRPC 拆分的天然边界
- 新行为（动态追加 / 循环 / 冻结）可单独 e2e 测试

### 负面

- +3 个 Task（实施成本 +1 天）
- 现有 4 个 Scheduler 测试要做小幅调整（接口保留，但内部结构变化）

### 中性

- Phase 2 `run_until_done()` 接口签名不变，调用方零修改

---

## 回滚条件

1. Runtime 抽象导致性能开销 > 20%（基准测试）
2. Phase 5 gRPC 拆分时发现 Runtime 边界不合理，需重新设计
3. Scheduler 与 Runtime 双向调用导致循环依赖风险（监控发现）

回滚方案：将 Runtime 内容合并回 Scheduler，回到方案 A。