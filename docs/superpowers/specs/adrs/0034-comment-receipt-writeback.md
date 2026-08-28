# ADR-0034: 评论处理回执写回（修订 ADR-0015「不写评论」）

日期：2026-08-28
状态：已接受（Phase 11）

## 背景

ADR-0015 曾决策「不写评论」（agent 只读评论、修改写文档），避免 agent 在协作文档里产生噪音。但 Phase 7-9 评论闭环跑通后暴露体验缺口：`/comment-apply` 在 IM 侧回复成功，但**文档评论区无任何痕迹**——协作者（导师/合作者）看不到「这条评论已被处理」，闭环感知断裂。

## 决策

1. `CommentActionService.apply` 成功应用评论修改后，对**该条已处理评论**回写固定文案回执：
   `已按此评论完成修改：模板 {template_id} 已更新至版本 {version_number}。`
2. 范围约束：**仅被动回执**（apply 触发）、固定模板文案、不 LLM 生成、不主动发起评论。
3. 版本号数据源：`VersionService.on_template_upsert` 返回新版本号（Task 2 签名变更 `-> int`）。
4. 失败降级：回执发送异常仅 warning 日志，不阻断 apply 主流程（回执是锦上添花，不是正确性的一部分）。
5. 防循环：回执本身会触发 `comment_add_v1` 事件，由 ADR-0033 的 operator==bot 过滤兜住。

## 备选方案

- **维持不写评论**：弃——协作者无感知，闭环体验断裂。
- **LLM 生成个性化回执**：弃——成本与幻觉风险，固定文案已满足「已处理」信号。

## 负面后果

- 文档评论区出现 bot 回执（用户可见的行为变化，需在真机验证时确认导师侧观感可接受）。
- 依赖评论回复权限 scope（`docs:document.comment:create` 等，需后台发布生效）。

## 回滚条件

`CommentActionService(comment_client=None)` 注入即禁用回执（apply 主流程完全不受影响）。

## 关联

- ADR-0015（被修订的不写评论决策）
- ADR-0032（reply_comment 的 SDK 实现）
- ADR-0033（回执事件的防死循环）
