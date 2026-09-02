# 项目 roadmap：待办计划与实施清单

- 生成日期：2026-09-01
- 基线：Phase 14 已收官（真机验收），Phase 15 spec 已定稿待实施
- 来源：全部 17 份 spec 的「不做项/遗留」清单 + 测试总结后续建议 + 真机使用反馈

> 规划原则：按「价值/风险比」排序；每个 Phase 一轮真机验收后收官；范围蔓延项一律进「远期池」。

---

## Phase 15：写回审批体验优化 — 已实施（b1dbc1d），真机验收：单聊通过，群聊 2 场景顺延

spec：`docs/superpowers/specs/2026-09-01-feishu-research-agent-phase15-ux-polish-design.md`

### 实施清单

- [x] T1 operator 校验：`gateway/app.py` research_writeback 分支，decide 前查 `doc_writes.requested_by` 比对 `payload.open_id`，不匹配返回 `{"ok": False, "status": "forbidden"}` 且不进 broker
- [x] T2 决策 toast：`process_card_payload` 返回值增加 `decision` 字段；`card_result_to_response` 扩展三种 toast（decided→「已记录：将写入/跳过」、already_handled→「该卡片已处理过」、forbidden→「仅任务发起者可操作」）
- [x] 实现前先验证 CallBackToast.type 枚举支持（info 是否可用，不支持统一 success/error）——SDK type 为自由字符串，info 可用
- [x] T3 卡片富化：`_writeback_with_confirm` 签名加 `outputs_digest`/`task_text`/`status`/`node_count` 参数，卡片 elements 改结构化（任务摘要 + 执行状态 + Nodes 统计 + 关键输出前 5 条）
- [x] 单测：forbidden/decided/already_handled/unknown-id 四分支、toast 映射 4 例、卡片 elements 内容
- [x] 回归：card_confirm 全流程（approve/deny/timeout/bind 失效/无 broker 降级）不回归；全量 691 passed（+7）

### 真机验收（2026-09-01 批量轮）

- [x] 单聊同意 → toast + IM 回执 + 文档写入（回归通过，card_confirm → success）
- [x] 单聊重复点击 → toast「该卡片已处理过」（本轮修复 broker 终态幂等 + gateway 持久化兜底后通过）
- [ ] 群聊非发起者点击 → toast「仅任务发起者可操作」，文档不写（顺延：需测试群 + 第二账号）
- [ ] 群聊发起者点击 → 正常写入（顺延：同上）

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

## Phase 17：节点级 L2 审批（架构级）— 已实施（10c32dc），真机验收通过（2026-09-01）

spec：`docs/superpowers/specs/2026-09-01-feishu-research-agent-phase17-node-l2-approval-design.md`

### 实施清单

- [x] Scheduler l2_gate 机制：write_doc 节点执行前卡片审批（gate 拒绝 → DENIED，下游照常 SKIPPED）
- [x] write_doc 对 planner 重新开放（research_allow_node_l2 开关 + broker 装配条件）
- [x] 拒绝语义：DENIED 计入 plan partial（与 SKIPPED 同档）
- [x] 任务中断恢复：cancel_stale_pending 扩展覆盖 node_l2 模式孤儿清扫
- [x] 防双写：plan 含 write_doc 节点时 Runner 收尾跳过自动写回
- [x] 单测：挂起/拒绝/放行/下游 skip/回调 forbidden
- [x] 真机：write_doc 节点审批 approve → success 写入；deny（跳过）→ cancelled 不写
  （验收时暴露并修复 write_doc 工具路径 append_blocks 残留 + blocks repr 字符串解析，详见测试总结）

---

## Phase 18：评论与协作补全 — 已实施（ef97168），真机验收通过（2026-09-01）

spec：`docs/superpowers/specs/2026-09-01-feishu-research-agent-phase18-comment-collab-design.md`

### 实施清单

- [x] 评论分页：list_comments page_token 循环拉全量（max_pages=20 上限）
- [x] LLM 评论问答：评论 `/ask 问题`（或含 @agent）→ 绑定文档上下文作答 → reply 回写（processed_at 幂等）
- [x] 评论删除/编辑处理：sync 拉取后本地对账（远端消失评论本地清除；编辑由 upsert 覆盖）
- [x] webhook 事件订阅通道：/webhook/lark 支持 url_verification challenge + comment_add_v1 分流（飞书后台配置需人工）
- [x] 真机：/ask 问答命中（回复 BRCA1 文档摘要）、普通评论不误答、
  删除评论后本地对账清除、分页拉到 4 天前旧评论（详见测试总结）

---

## Phase 19：检索与模板升级 — 已实施（5914b65，裁剪版），真机验收通过（2026-09-01）

spec：`docs/superpowers/specs/2026-09-01-feishu-research-agent-phase19-template-upgrade-design.md`

### 实施清单

- [x] 范围决策：ES / zhparser 拼音检索**砍**（无瓶颈证据，trigram + search_v2 够用）；模板 merge/branch **砍**（fork 无使用数据，按裁剪原则不做）
- [x] 模板标签推荐：TagRecommendService（共现打分 + 热度兜底）+ GET /templates/{id}/tag-suggestions
- [x] 真机：`scripts/verify_p19_tag_recommend.py` 连真实 PG 验证 5 项行为
  （共现优先 / 热度兜底 / limit 截断 / 排除无交集 / 404），事务回滚不污染数据

---

## Phase 20：单细胞转录组分析 — 已实施（fc9a7d8），真机验收通过（2026-09-01）

方向调整：原 BLAST+/AlphaFold 方向取消（2026-09-01 用户决策，转入远期池），
改为单细胞转录组分析；空间转录组为后续扩展。

spec：`docs/superpowers/specs/2026-09-01-feishu-research-agent-phase20-scrna-analysis-design.md`

### 实施清单

- [x] bio.Dockerfile（scanpy 栈 CPU 镜像）+ sc_tools 5 参数化脚本（load/qc/process/markers/plot）
- [x] BioRunner：短命容器（-i stdin / --network none / 资源限额）、路径白名单、dataset_id 幂等
- [x] sc_* 5 工具注册（L1_compute）+ settings bio_* 配置组
- [x] 真机：toy 10x 数据 6 轮全链路（含 IM 图片回传、文档图片三步插入、
  分析正确性 3 群还原/marker 命中），详见测试总结

### 后续扩展（优先级降序）

- [x] 空间转录组 st_* 工具链（Phase 21 完成，见下节）
- [x] bio_workspace 磁盘治理（Phase 23 完成，见下节）
- [ ] GPU 镜像 bio:gpu-latest（rapids-singlecell，大规模数据）

---

## Phase 21：空间转录组分析 — 已实施，真机三批验收通过（2026-09-02）

spec：`docs/superpowers/specs/2026-09-01-feishu-research-agent-phase21-spatial-transcriptomics-design.md`

三批共 21 任务（Subagent-Driven 执行）：

- 批①：st.Dockerfile（squidpy 栈）+ st_tools 5 脚本（load/qc/process/markers/plot）+ st_* 5 工具注册，真机一批验收
- 批②：st_domains（banksy-lite 空间域）+ st_commot（配体受体通讯，COMMOT 0.0.3）+ raw 快照，真机 n1-n6 全 success（ARI=1.0、恰好检出造入的 3 对 LR）
- 批③：st_deconvolve（cell2location 0.1.5 反卷积，双参考来源 + 独立超时 3600s）+ tiny scrna 参考，真机 n1-n7 全 success（含场景 A 串联 + 文档写回）
- 累计 13 个 bio 工具（sc_* 5 + st_* 8）；全量回归 803 passed

---

## Phase 22：运维加固轮 — 已实施（2026-09-02）

spec：`docs/superpowers/specs/2026-09-02-feishu-research-agent-phase22-ops-hardening-design.md`

- [x] ws_client pidfile 单实例守卫（OpenProcess 探活 / stale 接管 / --force 杀旧，真机验证拒绝+接管通过）
- [x] bind-doc 存在性探活（绑定时 list_root_children，远早于写回时发现）
- [x] FastAPI `on_event` → lifespan + ToolSpec ConfigDict 迁移（弃用警告 8→3）+ sc_qc/st_qc 小数据 min_genes 调参指导
- [x] st 镜像 torch 固定 2.14.0+cpu（SJTUG 镜像；7.17GB→3.19GB），冒烟 8 步 PASS（deconvolve 309s）

---

## Phase 23：bio_workspace 磁盘治理 — 已实施（2026-09-02）

spec：`docs/superpowers/specs/2026-09-02-bio-workspace-gc-design.md`

- [x] sweep 核心（TTL 7d + LRU 10GB + 宽限期 2h，12hex 候选集，纯函数式 now 注入）
- [x] BioRunner .last_access 打点（12hex 校验防路径穿越，best-effort 不阻断任务）
- [x] settings 五配置项（env 全可覆盖）+ ws_client bio-workspace-gc sweeper 线程
- [x] 真机验证：造假超期目录 60s 周期删除通过，活动数据集无损

---

## 持续项（随手做，不占 Phase）

- [x] bind-doc 存在性校验（Phase 22 完成：bind() 时 list_root_children 探活，2026-09-01 nzb/nkb 一字之差踩坑闭环）
- [x] 双 ws_client 防复发（Phase 22 完成：pidfile + OpenProcess 探活互斥；另查明历史"双实例"部分为 venv shim 父子进程对，非真双连接）
- [x] FastAPI `on_event` → lifespan 迁移（Phase 22 完成，警告 8→3）
- [ ] 模板/评论命令 IM 路由 vs REST API 集成验证（需人工参与）
- [ ] 幂等重传手动测试（飞书事件重发，需人工触发）
- [ ] fasta 长度回填使每次检索多一次 efetch 调用——如做批量检索再评估合并请求

## 远期池（默认不做，出现真实需求再捞）

- GPU 节点 / rapids 加速（注：bio 容器 GPU 镜像已在 Phase 20 后续扩展中排期，此处指研究沙箱整体）
- gRPC 拆分、工具热加载
- 分布式锁、多实例部署、K8s
- CI（远端）、mypy 严格化、uv/poetry 迁移
- 群聊共享 session、用户身份订阅
- 本地 BLAST+、AlphaFold 结构预测（原 Phase 20 方向，2026-09-01 取消）

---

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-01 | 初版：由 17 份 spec 待办汇总生成，Phase 15-20 + 持续项 + 远期池 |
| 2026-09-01 | Phase 15-19 批量实施完成（真机验收统一推迟）：P16 部分完成（网络白名单/产物回收未做），P17=10c32dc，P18=ef97168，P19 裁剪版=5914b65（ES/merge 砍掉） |
| 2026-09-01 | Phase 20 方向调整 + 实施：BLAST+/AlphaFold → 单细胞转录组（fc9a7d8），真机验收通过；P17/P18/P19 真机验收通过，P15 单聊通过（群聊 2 场景顺延）；write_doc 工具路径修复（append_blocks→render_blocks + parse_blocks）+ 审批卡终态幂等双保险 |
| 2026-09-02 | Phase 21 空间转录组三批收官（批① st_* 5 工具 / 批② domains+commot / 批③ deconvolve，真机三批验收）+ Phase 22 运维加固轮（pidfile 单实例守卫 / bind-doc 探活 / lifespan+ConfigDict / qc 调参指导 / st 镜像瘦身 7.17GB→3.19GB） |
| 2026-09-02 | Phase 23 bio_workspace 磁盘治理（TTL 7d + LRU 10GB + 宽限期 2h 周期清理；.last_access 打点防误删；sweeper 线程；真机验证通过） |
