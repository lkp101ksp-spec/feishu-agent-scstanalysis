# Phase 15 设计：写回审批体验优化轮

- 状态：已实施（自动化验收 691 passed；真机验收按用户决策延后，与 16-19 一起批量进行）
- 日期：2026-09-01
- 前置：Phase 14（card_confirm 写回审批流）已完成真机验收

## 1. 背景与现状

Phase 14 上线 card_confirm 后真机使用暴露的体验/安全缺口：

| # | 缺口 | 现状代码 | 问题 |
|---|---|---|---|
| 1 | 群聊无 operator 校验 | `gateway/app.py` research_writeback 分支不校验点击者身份 | 群里**任何人**都能点「同意写入」改用户绑定文档 |
| 2 | 重复点击无反馈 | `broker.decide` 幂等拒绝返回 False → `card_result_to_response` 返回 None（无 toast） | 用户重复点击没有任何反馈，不确定点没点上 |
| 3 | 决策即时反馈缺失 | toast 机制只有 renew_bind 在用 | 点「同意写入」后要等 research 线程 IM 回执才知道结果，期间是黑盒 |
| 4 | 预览卡片信息密度低 | `_writeback_with_confirm` 卡片正文 = `render_blocks_to_text` 纯文本截 600 字 | 一坨平文本，没有结构化展示任务/统计/关键输出 |

> 注：测试总结中「写回携带任务标识」已于 Phase 14 尾轮以 `task_label` 落地（template_engine L110-117），不重复做。

## 2. 设计

### 2.0 总览

```
用户点卡片按钮
  └ ws_client on_card → process_card_payload(research_writeback)
      ├ T1: 查 doc_writes.requested_by vs payload.open_id
      │    不匹配 → {"ok": False, "status": "forbidden"}（不进 broker）
      ├ 校验通过 → broker.decide(...)
      │    首次 → {"ok": True, "status": "decided", "decision": ...}
      │    重复 → {"ok": False, "status": "already_handled"}
      └ T2: card_result_to_response 按状态出 toast
           decided → success「已记录：将写入文档 / 已跳过写入」
           already_handled → 「该卡片已处理过」
           forbidden → error「仅任务发起者可操作」

research 线程（T3 在发送侧）
  └ _writeback_with_confirm 卡片改为结构化 elements：
     任务摘要 + 统计行 + 关键输出前 5 条（lark_md 列表）+ 按钮
```

### 2.1 T1：operator 校验（安全）

`process_card_payload` 的 research_writeback 分支，`broker.decide` 之前：

- 用现有 `session_factory` 查 `DocWriteRepo.get(doc_write_id)`
- `row.requested_by != payload.open_id` → 返回 `{"ok": False, "status": "forbidden"}`，**不调用 broker**（决策不生效）
- row 不存在 → 维持现状（broker 会因无等待方记录决策，超时兜底）

语义：**只有任务发起者能决定写不写自己的文档**。单聊场景 operator == requested_by 恒成立，行为不变；群聊其他人点击被拒。

### 2.2 T2：决策 toast（反馈）

`process_card_payload` research_writeback 分支的返回值增加 `decision` 字段（decided 时透传 approve/deny）；`card_result_to_response` 扩展：

| status | toast type | content |
|---|---|---|
| decided + approve | success | 已记录：将写入文档 |
| decided + deny | success | 已记录：跳过写入 |
| already_handled | success（info 可能不被 toast 枚举支持，统一 success） | 该卡片已处理过 |
| forbidden | error | 仅任务发起者可操作 |

> CallBackToast.type 实际枚举支持需实现时验证（renew_bind 只用了 success/error）；若 info 可用则 already_handled 用 info。

### 2.3 T3：预览卡片富化

`_writeback_with_confirm` 卡片 elements 改为结构化（数据在调用方 `_execute` 已有，作为新参数传入）：

```python
def _writeback_with_confirm(self, *, ..., outputs_digest: list[str], task_text: str)
```

- 第一段 div（lark_md）：`**任务**：{task_text 截断 80}` + 换行 `Nodes: N; Artifacts: M（{status}）`
- 关键输出：`outputs_digest` 前 5 条（每条截断 120），lark_md 列表；超出显示 `… 等共 N 条，完整内容将写入文档`
- 按钮、preview_text 落库（审计用）保持现状不变

### 2.4 非目标

- **卡片二次更新**（决策后按钮置灰/改文案）：需要卡片 update API，复杂度高收益低，不做
- **节点级 L2 审批**：后续独立 Phase
- **写回内容富化**：`render_blocks` 已写富 blocks（heading/table/list），不动

## 3. 测试计划

单测：
- `test_gateway`（或对应文件）：forbidden / decided / already_handled 三分支 + row 缺失维持现状
- `test_ws_client`：`card_result_to_response` 三种 toast 映射
- `test_research_runner`：卡片 elements 含任务摘要与关键输出行；preview_text 落库不变

集成回归：card_confirm 全流程（approve/deny/timeout/bind 失效）不回归。

真机验收场景：
1. 单聊同意 → toast「已记录：将写入文档」+ IM 回执 + 文档写入（回归）
2. 单聊重复点击 → toast「该卡片已处理过」
3. 群聊非发起者点击 → toast「仅任务发起者可操作」，文档不写
4. 群聊发起者点击 → 正常写入

## 4. 风险

- toast type 枚举支持需验证（实现第一步先确认，不支持则统一 success/error）
- operator 校验多一次 DB 查询：卡片回调低频路径，无性能影响
- `_writeback_with_confirm` 签名变化：仅一个调用方（`_execute`），同步改
