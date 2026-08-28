# ADR-0032: CommentClient 迁移 lark-oapi SDK（评论闭环零凭据）

日期：2026-08-28
状态：已接受（Phase 11）

## 背景

Phase 7 评论闭环的 `CommentClient` 走 httpx + 手工维护的静态 `FEISHU_API_TOKEN`（tenant_access_header 自管），与环境其余出站适配层（IM/Doc 已 SDK 化）不一致，且 token 过期需人工刷新。同时旧实现的 URL（`/docx/v1/documents/{id}/comments`）与响应结构假设（正文在顶层、有 block_id/user_name）与官方真机形态不符。

## 决策

1. `CommentClient` 整体重写为 **lark-oapi SDK BaseRequest 原始模式**（对齐 doc_adapter._sdk_request 惯例）：
   - 列表 `GET /open-apis/drive/v1/files/{file_token}/comments?file_type=docx`；
   - 回复 `POST .../comments/{comment_id}/replies`，body 为 text element 结构。
2. 适配层 `_to_flat`：正文取 `reply_list.replies[0]` 的 `content.elements[]`（type=="text" 的 text_run 拼接），`replies[1:]` 为后续回复；`block_id` 恒 None（官方无此字段）。
3. runtime 装配删除 `FEISHU_API_BASE_URL/TOKEN` 依赖，评论子系统**零凭据无条件组装**。
4. 避开 SDK 强类型 resource：`BaseResponse.success()` 语义有前科（ec55187），BaseRequest + `json.loads(resp.raw.content)` + `code != 0` 抛错是项目已验证惯例。

## 备选方案

- **强类型 file_comment resource**：弃——BaseResponse.success() 语义坑（ec55187 前科），且 SDK 1.7.3 对 drive comment 封装不全。
- **保留 httpx + 静态 token**：弃——token 手工维护，与全仓 SDK 化方向背离。

## 负面后果

- SDK 升级可能改变 BaseRequest/raw 内部约定（已锁 1.7.3 入 requirements-lock.txt）。
- `_to_flat` 承担官方结构→扁平 dict 的防腐层职责，官方字段变更需同步（诊断脚本 `scripts/diag_comments.py` 可真机校验）。

## 回滚条件

评论功能不可用时恢复 httpx 路径（git revert 单 commit，构造签名 `base_url/api_token` 与 SDK 模式互斥可辨）。

## 关联

- ADR-0031（SDK 引入与 BaseRequest 惯例来源）
- ADR-0033（本决策使评论事件/轮询双通道零凭据成为可能）
