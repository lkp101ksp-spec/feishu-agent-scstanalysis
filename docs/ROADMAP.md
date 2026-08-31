# 项目 roadmap：待办计划与实施清单

- 生成日期：2026-09-01
- 基线：Phase 14 已收官（真机验收），Phase 15 spec 已定稿待实施
- 来源：全部 17 份 spec 的「不做项/遗留」清单 + 测试总结后续建议 + 真机使用反馈

> 规划原则：按「价值/风险比」排序；每个 Phase 一轮真机验收后收官；范围蔓延项一律进「远期池」。

---

## Phase 15：写回审批体验优化（spec 已定稿，待实施）

spec：`docs/superpowers/specs/2026-09-01-feishu-research-agent-phase15-ux-polish-design.md`

### 实施清单

- [x] T1 operator 校验：`gateway/app.py` research_writeback 分支，decide 前查 `doc_writes.requested_by` 比对 `payload.open_id`，不匹配返回 `{"ok": False, "status": "forbidden"}` 且不进 broker
- [x] T2 决策 toast：`process_card_payload` 返回值增加 `decision` 字段；`card_result_to_response` 扩展三种 toast（decided→「已记录：将写入/跳过」、already_handled→「该卡片已处理过」、forbidden→「仅任务发起者可操作」）
- [x] 实现前先验证 CallBackToast.type 枚举支持（info 是否可用，不支持统一 success/error）——SDK type 为自由字符串，info 可用
- [x] T3 卡片富化：`_writeback_with_confirm` 签名加 `outputs_digest`/`task_text`/`status`/`node_count` 参数，卡片 elements 改结构化（任务摘要 + 执行状态 + Nodes 统计 + 关键输出前 5 条）
- [x] 单测：forbidden/decided/already_handled/unknown-id 四分支、toast 映射 4 例、卡片 elements 内容
- [x] 回归：card_confirm 全流程（approve/deny/timeout/bind 失效/无 broker 降级）不回归；全量 691 passed（+7）

### 真机验收（4 场景）

- [ ] 单聊同意 → toast + IM 回执 + 文档写入（回归）
- [ ] 单聊重复点击 → toast「该卡片已处理过」
- [ ] 群聊非发起者点击 → toast「仅任务发起者可操作」，文档不写
- [ ] 群聊发起者点击 → 正常写入

---

## Phase 16：安全加固轮（沙箱 + 权限）— 部分完成，剩余项见下

来源：Phase 13 T2 不做项 + Phase 7 工具 ACL 推迟项

### 实施清单

- [ ] 沙箱网络白名单：run_python 容器默认断网，按工具/节点粒度放行域名（NCBI eutils 等白名单制）
- [x] idle_sweep 周期调度：kernel_pool 空闲容器定期回收（KernelPool 加锁线程安全 + 守护线程）
- [ ] workspace 产物回收：沙箱 workspace 主机挂载与产物生命周期管理（防磁盘泄漏）
- [x] 工具 ACL：全局禁用名单粒度（settings.disabled_tools，planner 过滤 + 执行层拦截）；按用户/会话粒度授权推迟
- [x] 单测覆盖（断网容器/白名单域名/产物回收需真机，未做）

### 真机验收

- [ ] run_python 内访问非白名单域名被拒（依赖网络白名单实施）
- [ ] 空闲 30 分钟后容器被回收，下次调用冷启动正常

---

## Phase 17：节点级 L2 审批（架构级）— 已实施（10c32dc），待真机验收

spec：`docs/superpowers/specs/2026-09-01-feishu-research-agent-phase17-node-l2-approval-design.md`

### 实施清单

- [x] Scheduler l2_gate 机制：write_doc 节点执行前卡片审批（gate 拒绝 → DENIED，下游照常 SKIPPED）
- [x] write_doc 对 planner 重新开放（research_allow_node_l2 开关 + broker 装配条件）
- [x] 拒绝语义：DENIED 计入 plan partial（与 SKIPPED 同档）
- [x] 任务中断恢复：cancel_stale_pending 扩展覆盖 node_l2 模式孤儿清扫
- [x] 防双写：plan 含 write_doc 节点时 Runner 收尾跳过自动写回
- [x] 单测：挂起/拒绝/放行/下游 skip/回调 forbidden
- [ ] 真机：DAG 中段写文档节点触发卡片审批，deny 后下游分支走 skip 路径

---

## Phase 18：评论与协作补全 — 已实施（ef97168），待真机验收

spec：`docs/superpowers/specs/2026-09-01-feishu-research-agent-phase18-comment-collab-design.md`

### 实施清单

- [x] 评论分页：list_comments page_token 循环拉全量（max_pages=20 上限）
- [x] LLM 评论问答：评论 `/ask 问题`（或含 @agent）→ 绑定文档上下文作答 → reply 回写（processed_at 幂等）
- [x] 评论删除/编辑处理：sync 拉取后本地对账（远端消失评论本地清除；编辑由 upsert 覆盖）
- [x] webhook 事件订阅通道：/webhook/lark 支持 url_verification challenge + comment_add_v1 分流（飞书后台配置需人工）
- [ ] 真机：分页拉取、/ask 问答、删除评论后状态同步

---

## Phase 19：检索与模板升级 — 已实施（5914b65，裁剪版），待真机验收

spec：`docs/superpowers/specs/2026-09-01-feishu-research-agent-phase19-template-upgrade-design.md`

### 实施清单

- [x] 范围决策：ES / zhparser 拼音检索**砍**（无瓶颈证据，trigram + search_v2 够用）；模板 merge/branch **砍**（fork 无使用数据，按裁剪原则不做）
- [x] 模板标签推荐：TagRecommendService（共现打分 + 热度兜底）+ GET /templates/{id}/tag-suggestions
- [ ] 真机：标签推荐效果抽查

---

## Phase 20：生物工具扩展

来源：Phase 7/9 不做项

### 实施清单

- [ ] 本地 BLAST+（容器化，脱离 NCBI API 限流）
- [ ] AlphaFold 结构预测接入
- [ ] 真机：本地 blast 比对一条序列 + AlphaFold 预测结果写回文档

---

## 持续项（随手做，不占 Phase）

- [ ] FastAPI `on_event` → lifespan 迁移（剩余 8 条弃用警告，下次动 gateway/app.py 时顺手带上）
- [ ] 模板/评论命令 IM 路由 vs REST API 集成验证（需人工参与）
- [ ] 幂等重传手动测试（飞书事件重发，需人工触发）
- [ ] fasta 长度回填使每次检索多一次 efetch 调用——如做批量检索再评估合并请求

## 远期池（默认不做，出现真实需求再捞）

- GPU 节点 / rapids 加速
- gRPC 拆分、工具热加载
- 分布式锁、多实例部署、K8s
- CI（远端）、mypy 严格化、uv/poetry 迁移
- 群聊共享 session、用户身份订阅

---

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-01 | 初版：由 17 份 spec 待办汇总生成，Phase 15-20 + 持续项 + 远期池 |
| 2026-09-01 | Phase 15-19 批量实施完成（真机验收统一推迟）：P16 部分完成（网络白名单/产物回收未做），P17=10c32dc，P18=ef97168，P19 裁剪版=5914b65（ES/merge 砍掉） |
