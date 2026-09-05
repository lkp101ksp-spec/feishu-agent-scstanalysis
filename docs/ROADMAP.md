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

## Phase 16：安全加固轮（沙箱 + 权限）— 已收官（2026-09-04 对齐：两项决策不做 + 一项已由 Phase 22 实现）

来源：Phase 13 T2 不做项 + Phase 7 工具 ACL 推迟项

### 实施清单

- [ ] ~~沙箱网络白名单~~（2026-09-01 spec §1 评估**决策不做**：沙箱默认断网 docker_network_mode=none 已是最严姿态，非欠债；如未来需要联网工具再捞）
- [x] idle_sweep 周期调度：kernel_pool 空闲容器定期回收（KernelPool 加锁线程安全 + 守护线程）
- [ ] ~~workspace 产物回收~~（2026-09-01 决策不做：workspace 为容器内 tmpfs 随容器销毁，无磁盘泄漏面）
- [x] 工具 ACL：全局禁用名单粒度（settings.disabled_tools，planner 过滤 + 执行层拦截）；按用户/会话粒度授权推迟
- [x] 单测覆盖（断网容器/白名单域名/产物回收需真机，未做）

### 真机验收

- [ ] ~~run_python 内访问非白名单域名被拒~~（随网络白名单决策不做）
- [x] 空闲 30 分钟后容器被回收，下次调用冷启动正常（kernel idle sweeper，Phase 22 ws_client 装配周期 300s 调度 + 单测覆盖；真机日志常驻 "kernel idle sweeper started"，2026-09-04 核实勾选）

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
- [x] GPU 镜像 bio:gpu-latest（rapids-singlecell，大规模数据）（Phase 25 完成，见下节）

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

## Phase 24：planner repr 串执行层纠正 — 已实施（2026-09-02）

spec：`docs/superpowers/specs/2026-09-02-planner-repr-coercion-design.md`

- [x] param_coerce 纯函数（array/object 声明 + str 值 → JSON/repr 双段还原，类型不匹配保留）
- [x] ToolHandler.execute() 集成 + warning 日志（`param coerced from repr-string`，复发率观测点）
- [x] planner prompt 源头减量指引（数组/对象参数必须输出真 JSON）

---

## Phase 25：GPU 镜像 bio:gpu-latest — 已实施，真机验收通过（2026-09-03）

spec：`docs/superpowers/specs/2026-09-02-bio-gpu-image-design.md`

- [x] BioRunner `--gpus` 透传（2ef7da8）+ `bio_use_gpu`/`bio_gpu_image` 开关（默认关，env BIO_USE_GPU/BIO_GPU_IMAGE）
- [x] bio:gpu-latest 镜像（fe5e611，10.3GB；rapids-singlecell==0.14.1 + RAPIDS cu12 <26 上限；Docker Desktop 4.28→4.89 升级排障后容器内 cuda True）
- [x] sc_tools 双栈自适应（af03144）：import 探测 rapids_singlecell 分流，emit 加 accelerator 字段；anndata nullable string 兼容修复
- [x] 双链对照（tiny）：n_clusters 3=3 一致、top10 markers 重合度 100%、GPU 链 accelerator=gpu
- [x] 大队列真机验收（2026-09-03）：93665 细胞 10x 合并全链 5m46s success；期间修复 WSL2 pinned-host 限制致 leiden OOM（GPU 链 leiden 回 CPU igraph，81fd2c8）

---

## Phase 26：/code agentic coding agent — 已实施并真机验收收官（2026-09-04）

spec：`docs/superpowers/specs/2026-09-03-phase26-code-agent-design.md`
计划：`docs/superpowers/plans/2026-09-03-phase26-plan-overview.md`（T1-T8 全部完成）

**目标**：A' 方案——在 `/research` DAG 链之外新增 `/code` agentic coding 链路：自研 LLM function-calling 循环 + SKILL.md 兼容 skill 系统 + 会话工作区，与既有链路零破坏并存。

**交付物**：

- [x] 五新文件 `orchestrator/coding/`：workspace.py（路径防逃逸 + CommandPolicy 命令三态）、code_tools.py（六原语 read/write/edit/list/search/run_cmd）、skill_loader.py（SKILL.md + tools.yaml 解析/注册/热加载/知识面词元匹配）、agent_loop.py（步数/预算/超时三终止 + 观察截断 + 历史压缩 + 连续失败禁用）、coding_runner.py（受理即回 + 后台线程 + 三层工具面拼装 + L2 审批发卡 + 节流过程反馈）
- [x] 三处改动：`LLMRouter.chat_with_tools`（OpenAI tools 协议 + fallback + strip_think）；settings 七字段 + ws_client `/code` 路由 + `code_approval` 卡片回调分支（owner 内嵌 value 比对，不落库）；`gateway/runtime.py` build_runtime 装配
- [x] 测试：Phase 26 累计新增 81 用例（单测 77 + e2e 冒烟 4）；全量回归 **927 passed / 0 failed**（基线 846 零回归）
- [x] 真机验收 5/5：/code fib.py、curl 审批卡、skill bioqc、/code clear、/research 93665 细胞回归（过程中修 2 个 run_cmd bug：`shutil.which` 裸命令解析 + `shlex.split` 字符串兼容）

**限制与后续**：

- 过程反馈 v1 为节流文本；卡片原地更新留 v2（IMAdapter 无 update_card）
- skill 知识面 v1 词元匹配（命中 ≤3 全文注入）；语义检索（embedding）留远期
- registry 白名单默认 `sc_*`（settings `code_registry_tools` 可调），更细粒度配置化留后续；skill 子进程本机直跑无容器隔离；审批不落库无审计回溯
- ~~sc_load 对不存在路径报 WinError 2~~（2026-09-04 已修：compute_dataset_id 单文件版补 isfile 检查抛 SC_FILE_NOT_FOUND，对齐目录版）
- 真机验收已完成 5/5（fib / curl 审批 / skill 冒烟 / clear / /research 回归）；「他人不可批」需群聊环境暂缓

## Phase 26 补充：生信 skill 沉淀 — 已收官（2026-09-04）

**目标**：把 Phase 25 大队列验收流程（10x 合并 → 预处理 → markers）沉淀为 3 个可复用 skill，/code 直接调用。

**交付物**：

- [x] `skills/bio_10x_merge/`：10x 多样本合并（read_10x_mtx → concat → batch 列 → 去重 → h5ad）
- [x] `skills/bio_preprocess/`：标准预处理（QC/归一化/HVG/PCA/UMAP/leiden，CPU 栈 scanpy）
- [x] `skills/bio_markers/`：leiden 簇 wilcoxon 差异分析 → 每簇 top10 markers + dotplot
- [x] 宿主依赖：scanpy 1.12.4 + igraph 1.1.2 + leidenalg（清华源装宿主 venv，skill 直跑宿主 python）
- [x] 本地冒烟：93665 细胞 10 样本 → 59899 细胞 27 簇 → markers（Col1a1 成纤维/Cd79a B 细胞/Cd3g T 细胞等生物学合理）
- [x] 真机验收：/code 用 3 个 skill 全链跑通（9 次工具调用全成功，数字与本地一致，产物 4.35GB）
- [x] 全量回归 **930 passed**（基线 927 + 新增 3）

**限制与后续**：skill 子进程直跑宿主 python（需装包），容器隔离留后续；Phase 27 skill 失败诊断已实施（见下节）。

---

## Phase 27：Skill 失败诊断（半自动进化）— 已实施，真机验收待人工（2026-09-04）

**目标**：/code 任务失败时自动分析轨迹、定位 skill 缺陷、生成改进建议，经人工审批卡写回 skill 文件（Recuris 思想提炼，不套框架）。

**交付物**：

- [x] `orchestrator/coding/skill_diagnoser.py`：SkillDiagnoser（diagnose 无 skill 短路不调 LLM / 失败事件压缩 / JSON 围栏剥离；apply 写回 SKILL.md 追加改进记录 或 tools.yaml 字段合并，均先备份 .bak）
- [x] `coding_runner.py`：run_sync 末尾 status≠final 时 `_maybe_diagnose_skill` 诊断发卡（全程 try 静默不影响主流程）；`_cap_suggestion` 字段裁剪防按钮 value 超限
- [x] `gateway/app.py`：`skill_improve` 回调分支（owner 内嵌比对 forbidden → broker 幂等 → approve 时 apply，结果 toast 返回）
- [x] `runtime.py`：CodingRunner 装配注入 SkillDiagnoser（llm + code_skills_dir）
- [x] 测试：新增 18 用例（diagnoser 单测 9 + e2e 3 + gateway 回调 6），全量回归 **948 passed**
- [x] 真机验收 5/5（2026-09-04 晚）：验收桩 skill（qc_stat 必崩脚本）构造连败 → 诊断卡触发 → 批准写回 tools.yaml+.bak → 重复点击幂等 → 忽略分支 → 成功任务不误触发；验收后桩已删除
- [x] 真机修复（TDD）：连败禁用后模型按引导文字收尾得 final，旧触发条件 `status != final` 漏掉该主路径 → LoopResult 新增 `tools_disabled` 字段（agent_loop 5 返回点统一携带），触发条件改为 final+tools_disabled 仍诊断；新增 3 用例，回归 **954 passed**
- [x] 后续小修（875b280）：诊断写回 tools.yaml 白名单过滤 + prompt schema 约束（真机发现 LLM 幻觉 max_retries 等字段写入永不生效）

**已知限制**：~~LoopResult.tool_events 仅 {step,name,ok} 无观察细节~~（2026-09-04 Phase 28 已补：失败事件携带 error 摘要进诊断 prompt）；~~ToolHandler 对 handler 返回 ToolResult 非 dict 时包装丢 error_code~~（2026-09-04 已修：ToolResult 直接透传，TDD 2 单测 + 子进程真失败 e2e 全链用例，回归 951）；~~skill_improve 审批不落库无审计~~（2026-09-04 已修：点击审计本就走公共段落库（此前记录不准确，但 target_id 恒空 + apply 结果无审计）；现补 skill_improve_id 进 target_id 候选链 + applied/apply_failed 追加 system 审计，TDD 4 用例回归 972）。

---

## Phase 28：失败观察摘要 + Working Memory — 已收官（2026-09-04）

**目标**：解 Phase 27 真机验收暴露的诊断质量问题（诊断器只见 ok 标志、模型重复试错），Recuris Working Memory 思想本地化收尾。

**交付物**：

- [x] T1 失败观察摘要：agent_loop 失败事件追加 `error` 摘要（error_code+error_message 拼接，截断 500）；diagnoser `_condense_events` 送入诊断 prompt——诊断 LLM 可见真实 stderr
- [x] T2 Working Memory：滚动失败记忆消息（"## 已试路径"紧随 system，窗口 5 行，原地替换防消息膨胀，压缩丢失后下次失败自动重插）——模型每轮可见已试路径，减少重复调用
- [x] 测试：新增 8 用例（ErrorSummary 3 + WorkingMemory 3 + diagnoser 2），全量回归 **965 passed**
- [x] 真机验收 4/4+1（2026-09-04 晚，f9361ae）：qc_stat 必崩桩（stderr 带 0x1F4A 标识）→ 诊断卡逐字引用真实报错 `SCRIPT_ERROR: ... checksum mismatch at offset 0x1F4A`、逐事件分别归因（qc_stat_run 数据损坏 / run_cmd Windows 命令不兼容分开分析）、基于注入定义判断"不修改 tools.yaml"不再盲猜参数、qc_stat_run 仅 1 调零重复（T2 生效）；附赠验证 final+tools_disabled 触发路径；验收后桩已删除
- [x] 验收暴露并修复（f9361ae，TDD 3 用例，回归 968）：诊断 prompt 归因纪律（issue 逐字引用 error、多事件逐个归因禁合并）+ 注入涉及工具的真实定义（YAML，单工具截断 800 字符）防 patch 参数名盲猜
- [x] 后续：skill_improve 审计闭环（TDD 4 用例，回归 972）：`_audit_event` helper 抽取；卡片点击审计 target_id 候选链补 `skill_improve_id`（原先恒空检索断链）；apply 成功/失败追加 `skill_improve_applied` / `skill_improve_apply_failed` system 审计（detail 含 skill/file/backup/reason）——按 skill_improve_id 检索可得"点击→写回结果"完整链

**已知限制**：~~Working Memory 只记失败路径，成功路径摘要跨步复用等真机反馈再议~~（2026-09-04 Phase 29 T3 已补：成功段 ≤3 行"可复用结果"）。

---

## Phase 29：skill 容器隔离 + 语义检索 + Working Memory 成功路径 — 已收官（2026-09-04）

**目标**：三项遗留项收尾——skill 执行安全、skill 召回质量、成功结果复用。均渐进兼容（不配置即走旧行为）。

**交付物**：

- [x] T1 容器隔离：tools.yaml 工具条目可选 `image` 字段 → `docker run --rm -i --network none --cpus 2 --memory 4g -v <skill_dir>:/skill:ro -w /skill <image> <command> <args>`；未配 image 本机直跑（存量 skill 零迁移）；错误处理与本机模式一致（SCRIPT_ERROR + stderr 尾部）。**限制**：skill 目录只读挂载，需写宿主路径的 skill 不适用容器模式
- [x] T2 语义检索：`build_system_knowledge(task_text, llm=...)` 一次纯文本 LLM 调用从候选清单（name+description）选 top-N（JSON `{"skills": [...]}`，幻觉 name 过滤）；LLM 异常/解析失败/全空一律回退词元法 v1（`_rank_by_tokens` 抽取复用），绝不抛出；CodingRunner 已装配 `llm=self.llm`
- [x] T3 Working Memory 成功路径：成功事件追加 `- step N: name(args) → OK: <产出摘要>`（stdout/result 截 80）；消息扩展两段——"### 失败（勿重复）"（≤5 行，格式与 Phase 28 一致）+ "### 成功（可复用结果）"（≤3 行）；零工具调用不注入
- [x] 测试：新增 14 用例（T1 3 + T2 5 + T3 6），调整 3 个既有用例适配新语义（消息数 4→5、记忆注入条件、_refresh_memory_message 双列表签名）；全量回归 **986 passed**
- [x] 真机验收 3/3（2026-09-04 晚，8ea5483）：纯英文任务 `perform quality control on the merged single-cell data`（词元交集设计为 0，只有语义法能命中）→ 模型调用 run_qc（T2 ✅ 语义检索选中 bioqc）；run_qc 仅 1 调零重复、总结直接引用 stdout 数字 93665→90000（T3 ✅）；审批卡点击走新 `_audit_event` 公共段落库成功（审计 ✅）
- [x] 验收发现并修复：code_approval 点击审计 target_id 同样恒空（F1 只补了 skill_improve_id 漏了 sibling）→ 候选链补 `code_approval_id`，TDD 1 用例，回归 **987 passed**

**排除**：容器写宿主路径（挂 code_workspace，等真机需要再议）；嵌入向量检索（skill <50 个 LLM 选择足够）；跨任务持久记忆。

---

## 持续项（随手做，不占 Phase）

- [x] bind-doc 存在性校验（Phase 22 完成：bind() 时 list_root_children 探活，2026-09-01 nzb/nkb 一字之差踩坑闭环）
- [x] 双 ws_client 防复发（Phase 22 完成：pidfile + OpenProcess 探活互斥；另查明历史"双实例"部分为 venv shim 父子进程对，非真双连接）
- [x] FastAPI `on_event` → lifespan 迁移（Phase 22 完成，警告 8→3）
- [ ] 模板/评论命令 IM 路由 vs REST API 集成验证（需人工参与）
- [ ] 幂等重传手动测试（飞书事件重发，需人工触发）
- [ ] fasta 长度回填使每次检索多一次 efetch 调用——如做批量检索再评估合并请求

## 远期池（默认不做，出现真实需求再捞）

- GPU 节点 / rapids 加速（注：bio 容器 GPU 镜像已在 Phase 25 交付 bio:gpu-latest，此处指研究沙箱整体）
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
| 2026-09-01 | Phase 15-19 批量实施完成（真机验收统一推迟）：P16 安全加固实施（网络白名单/产物回收经 spec §1 评估**决策不做**——沙箱默认断网 docker_network_mode=none + workspace 为容器内 tmpfs 随容器销毁，已是最严姿态，非欠债），P17=10c32dc，P18=ef97168，P19 裁剪版=5914b65（ES/merge 砍掉） |
| 2026-09-01 | Phase 20 方向调整 + 实施：BLAST+/AlphaFold → 单细胞转录组（fc9a7d8），真机验收通过；P17/P18/P19 真机验收通过，P15 单聊通过（群聊 2 场景顺延）；write_doc 工具路径修复（append_blocks→render_blocks + parse_blocks）+ 审批卡终态幂等双保险 |
| 2026-09-02 | Phase 21 空间转录组三批收官（批① st_* 5 工具 / 批② domains+commot / 批③ deconvolve，真机三批验收）+ Phase 22 运维加固轮（pidfile 单实例守卫 / bind-doc 探活 / lifespan+ConfigDict / qc 调参指导 / st 镜像瘦身 7.17GB→3.19GB） |
| 2026-09-02 | Phase 23 bio_workspace 磁盘治理（TTL 7d + LRU 10GB + 宽限期 2h 周期清理；.last_access 打点防误删；sweeper 线程；真机验证通过） |
| 2026-09-02 | Phase 24 planner repr 串长期方案：执行层 schema 驱动纠正（param_coerce + execute 集成 + warning 日志）+ prompt 源头减量 |
| 2026-09-03 | Phase 25 GPU 镜像 bio:gpu-latest：BioRunner --gpus 透传 + bio_use_gpu 开关 + sc_tools 双栈自适应（rapids-singlecell），RTX 3090 双链对照通过（markers 重合 100%） |
| 2026-09-04 | Phase 26 /code agentic coding agent 全链落地（T1-T8）：orchestrator/coding 五件 + LLMRouter.chat_with_tools + code_approval 回调 + build_runtime 装配；新增 81 用例，回归 927 全过；真机验收 5/5 收官（修 run_cmd 裸命令/字符串兼容 2 bug） |
| 2026-09-04 | sc_load 不存在路径修复（compute_dataset_id 单文件版补 isfile 检查抛 SC_FILE_NOT_FOUND，618d41f）+ 生信 skill 三件套沉淀（bio_10x_merge/bio_preprocess/bio_markers，宿主 venv 装 scanpy+igraph 直跑，3608ca8）；真机 3-skill 全链验收通过（93665→59899 细胞→27 簇，产物 4.35GB）；Phase 27 skill 失败诊断计划落盘（42bdd55，暂缓开发）；回归 930 全过 |
| 2026-09-04 | Phase 27 skill 失败诊断实施（T1-T4）：SkillDiagnoser（diagnose/apply/.bak 写回）+ coding_runner 诊断发卡 + gateway skill_improve 回调 + runtime 装配；新增 18 用例，全量回归 948 全过；真机验收待人工 |
| 2026-09-04 | Phase 27 真机验收 5/5 收官：发现并修复连败禁用后 final 收尾不触发诊断的设计缺陷（LoopResult.tools_disabled 标记，TDD 3 用例，回归 954 全过）；验收桩 qc_stat 验后删除；发现诊断 LLM 幻觉 schema 外字段等 3 项后续优化点 |
| 2026-09-04 | ToolHandler ToolResult 透传修复（Phase 26 既有怪癖：skill 子进程 SCRIPT_ERROR 被 dict 包装丢 error_code 误判成功）→ 透传保留 error_code，连败禁用链路贯通；TDD 新增 3 用例（单测 2 + 子进程真失败 e2e 1），全量回归 951 全过 |
| 2026-09-04 | Phase 28 失败观察摘要 + Working Memory：失败事件带 error 摘要进诊断 prompt（诊断 LLM 可见真实报错）+ 滚动失败记忆消息（窗口 5 行，模型防重复试错）；前置小修诊断写回白名单过滤（875b280）；新增 8 用例，回归 965 全过；真机轻量验收可选 |
| 2026-09-04 | Phase 28 真机验收 4/4+1 全过 + 修复诊断归因（f9361ae）：验收首跑意外发现运行中 ws_client 为旧代码（18:18 启动早于当日 3 个修复 commit，首跑结论作废——教训：**验收前必须核对进程启动时间与 HEAD**）；重启后诊断卡逐字引用真实报错、逐事件归因、不盲猜参数，附赠验证 final+tools_disabled 触发；新增归因纪律+工具定义注入 TDD 3 用例，回归 968 全过 |
| 2026-09-04 | skill_improve 审计闭环：查库发现点击审计本就落库（此前"不落库"记录不准确），真缺口是 target_id 恒空 + apply 结果无审计；补 skill_improve_id 候选链 + applied/apply_failed system 审计 + `_audit_event` helper 抽取，TDD 4 用例，回归 972 全过 |
| 2026-09-04 | Phase 29 三项遗留收官：T1 skill 容器隔离（tools.yaml 可选 image → docker run network-none 资源限额 ro 挂载）+ T2 语义检索（LLM 选 skill + 词元 fallback）+ T3 Working Memory 成功段（可复用结果 ≤3 行）；新增 14 用例调整 3 个，回归 986 全过 |
| 2026-09-04 | Phase 29 真机验收 3/3（纯英文任务词元交集设计为 0，语义检索选中 bioqc→run_qc；成功记忆零重复引用 stdout；审批点击审计落库）+ 修复 code_approval 审计 target_id 恒空（候选链补 code_approval_id，1 用例，回归 987） |
| 2026-09-04 | 一键部署/启动脚本：setup.ps1（Python≥3.10→venv→清华源依赖→.env 模板引导→config_check 硬门禁→external 卷+compose+healthcheck→alembic 迁移）+ start.ps1（预检→PG 自动拉起→后台 --force 启动 ws_client→60s 日志确认→pid/HEAD 输出）；幂等可重跑、兼容 Windows PowerShell 5.1；真机两轮验证通过（修确认窗口 20s→60s、taskkill /T 杀树两处） |
| 2026-09-05 | Phase 30 可视化模型切换：/model 管理卡（admin=FEISHU_ADMIN_OPEN_IDS 复用）+ llm.yaml providers 候选池（key 只在 .env 不落库不回显）+ llm_active 单行表持久化（迁移 0005）+ LLMRouter.reconfigure 原地热切换（共享引用全局生效）+ model_switch 卡片回调审计闭环（llm_model_switched/_denied）+ 启动 DB 记忆恢复；TDD 单测 19 + 集成 4，全量回归 1006 全过；真机验收待配置 admin 名单 |
| 2026-09-05 | Phase 31 富集分析 sc_enrichment（toolsv1 迁移第一批，用户确认只做富集/按类分流）：gseapy ORA+GSEA（hallmark/GO BP/KEGG 三基因集，构建期 Enrichr 预取 /opt/gene_sets 解决容器断网，MSigDB 直链国内 404 后的替代方案）+ 镜像重建 + 容器真跑冒烟（断网容器 ok:true，B 细胞 mock 数据富集出 BCR 通路生物学自洽）；TDD 4 用例，回归 1006 全过 |
| 2026-09-05 | Phase 32 常用分析三件套（toolsv1 第二批，用户决策真机验收延后统一逐个测）：sc_score_genes 基因集打分（多集 object 参数，等价 AddModuleScore）+ sc_metabolism KEGG 代谢活性（复用镜像 kegg.json 逐通路打分，簇均值+方差 top 热图防响应膨胀）+ sc_pseudotime 扩散伪时序（diffmap+DPT，root_marker 定根，inf→None 防 JSON 非法值）；本机三脚本假数据冒烟全过（B_cell 簇偏移/根细胞定位/断连 0 生物学自洽）+ 镜像重建（4f107c66，断网容器 import ok）；TDD 7 用例，回归 1019 全过；容器真跑与真机验收延后统一测试轮 |
| 2026-09-05 | Phase 33 常用分析四件套（toolsv1 第三批，全选确认）：sc_de 组间差异（rank_genes_groups 定向两组，列/取值不存在时错误列可用列引导自纠，火山图）+ sc_subcluster 亚聚类（raw 重建管线→**新 dataset_ref** `{id}_sub{n-m}`，下游 12 工具零改动可链，支持多级）+ sc_integrate 批次整合（bbknn→新 dataset_ref `{id}_bbknn`，双联 UMAP；annoy 无轮需 g++ 同层临时编译后 purge）+ sc_cellfreq 组成比较（比例表+堆叠图+每簇卡方）；本机四脚本冒烟生物学自洽（GZMB↑/MS4A1↓、簇 2 卡方 p=7.6e-12、新 ref 可回读）+ 镜像重建 edcdbc2e 断网容器 bbknn+四脚本 import ok；TDD 6 用例，回归 1025 全过；容器真跑与真机验收延后 |
