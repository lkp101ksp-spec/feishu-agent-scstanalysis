# ADR-0023: 评论动作 owner 显式 apply（非评论作者自动触发）

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-26）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 8 |
| 相关 spec | §4.4 权限与幂等 |

---

## 背景

评论中可携带指令（ADR-0020）。需决定**谁**、**何时**触发执行。

---

## 选项

### A. owner 显式 apply（已选 ✅）

评论作者任意（导师/协作者）；执行需 owner 在 IM 发 `/comment-apply <doc_id>`；逐条权限校验 caller == 目标模板 owner。

- **优点**：执行权收口在资源所有者；防恶意评论直接改模板；错误可逐条报告（一个失败不中断批次）
- **缺点**：多一步手动操作（sync → apply 两步）

### B. sync 时自动执行

评论落库即解析执行。

- **优点**：零手动
- **缺点**：任何能评论的人可借 agent 之手改模板（越权写）；误评（"试试 /replan"）直接生效；无人工确认窗口

### C. 评论作者即执行人（author == operator）

评论谁写谁触发。

- **优点**：权限直觉
- **缺点**：协作者评论改 owner 的模板，权限模型冲突；与模板 owner 体系（Phase 5-7）不一致

---

## 选择：**方案 A（owner 显式 apply）**

执行语义：

1. `apply(doc_id, caller_open_id)` 遍历 pending 评论
2. 每条 parse → ParsedAction；逐条校验 `template.owner_open_id == caller`
3. 非 owner → 该条 failed（reason=not_owner），**不中断**其余评论
4. 成功 → 修改模板 + VersionService bump + `mark_processed`
5. 重复 apply → pending 已空，applied=0（幂等）

---

## 后果

### 正面

- 权限模型与 Phase 5-7 模板 owner 体系完全一致
- owner 有确认窗口（sync 后可先 `/comments` 查看再 apply）
- 幂等防重放（processed_at 不被 sync 覆盖，ADR-0019）

### 负面

- 闭环多一步手动操作
- 导师需知道 template_id（写评论时要填对）

### 中性

- "sync 后自动提醒有 pending 动作"（IM 推送摘要）推迟 Phase 9
- 白名单协作者（owner 授权列表）推迟 Phase 9

---

## 回滚条件

1. 用户反馈两步太繁琐（apply 使用率低但评论量大）
2. 届时引入"owner 一键 approve-all"卡片（仍非自动执行）
