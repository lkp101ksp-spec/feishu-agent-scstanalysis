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

## Phase 16：安全加固轮（沙箱 + 权限）

来源：Phase 13 T2 不做项 + Phase 7 工具 ACL 推迟项

### 实施清单

- [ ] 沙箱网络白名单：run_python 容器默认断网，按工具/节点粒度放行域名（NCBI eutils 等白名单制）
- [ ] idle_sweep 周期调度：kernel_pool 空闲容器定期回收（替代当前单次超时 release 重建）
- [ ] workspace 产物回收：沙箱 workspace 主机挂载与产物生命周期管理（防磁盘泄漏）
- [ ] 工具 ACL：工具调用按用户/会话粒度授权（当前全开）
- [ ] 单测 + 真机：断网容器跑 run_python、白名单域名可访问、产物回收生效

### 真机验收

- [ ] run_python 内访问非白名单域名被拒
- [ ] 空闲 30 分钟后容器被回收，下次调用冷启动正常

---

## Phase 17：节点级 L2 审批（架构级）

来源：Phase 14 spec 明确排除项（§范围决策）

### 实施清单

- [ ] Scheduler 挂起机制：`run_until_done` 改造，支持节点执行前挂起等待外部事件（ApprovalBroker 泛化为事件总线）
- [ ] write_doc 等敏感工具对 planner 重新开放（当前唯一写回路径在 Runner 尾部）
- [ ] 挂起节点超时语义（安全侧失败，下游节点跳过还是取消的策略定义）
- [ ] 任务中断恢复：挂起中进程重启的孤儿清扫（复用 cancel_stale_pending 模式）
- [ ] 单测：挂起/唤醒/超时/重启清扫
- [ ] 真机：DAG 中段写文档节点触发卡片审批，deny 后下游分支走 skip 路径

### 风险提示

- 这是全项目剩余最大架构改动，建议单独 spec（届时按项目惯例先写设计文档再实施）

---

## Phase 18：评论与协作补全

来源：Phase 9/11 不做项

### 实施清单

- [ ] 评论分页（当前一次全量拉取）
- [ ] LLM 评论问答：文档评论 @agent 提问，基于绑定文档上下文回答
- [ ] 评论删除/编辑事件处理（当前仅新增）
- [ ] webhook 事件订阅通道：作为 ws 长连接的备选/容灾（需飞书后台配置，部分需人工）
- [ ] 真机：分页拉取、@agent 问答、删除评论后状态同步

---

## Phase 19：检索与模板升级

来源：Phase 6/7/9 不做项

### 实施清单

- [ ] ES / zhparser 拼音检索（视 tsvector 实际使用瓶颈决定是否做）
- [ ] 模板标签推荐
- [ ] 模板 merge/branch（fork 之上的协作流）
- [ ] 真机：按实际使用频率决定裁剪——低使用率功能直接砍不做

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
