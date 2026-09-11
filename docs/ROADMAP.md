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
- [x] 群聊非发起者点击 → toast「仅任务发起者可操作」，文档不写（2026-09-08 真机通过：日志 WARNING research_writeback forbidden operator≠owner）
- [x] 群聊发起者点击 → 正常写入（2026-09-08 真机通过：decided approve → doc_writes 行 success；发起者复点 → already_handled，非发起者复点已完成卡仍 forbidden——owner 校验优先于 dup 检查）

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

## Phase 27：Skill 失败诊断（半自动进化）— 已实施，真机验收 5/5 通过（2026-09-04）

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
- [x] 模板/评论命令 IM 路由真机验收（2026-09-08）：/template-list、/template-favorites、/comments-sync、/comments 四命令 IM 回复与 ws 日志 status 一一对应；裸 /template-rollback 走 unknown_command 兜底给可用命令列表（行为正确，非静默落闲聊）
- [x] 幂等重传验证（2026-09-08）：消息幂等真库级双投 PASS（r1 success/r2 duplicate、orchestrator 调用恒 1、pg idempotency_keys 留行正确）；卡片幂等 2026-09-01 单聊已验（already_handled toast）
- [ ] fasta 长度回填使每次检索多一次 efetch 调用——如做批量检索再评估合并请求

## 远期池（默认不做，出现真实需求再捞）

- GPU 节点 / rapids 加速（注：bio 容器 GPU 镜像已在 Phase 25 交付 bio:gpu-latest，此处指研究沙箱整体）
- gRPC 拆分、工具热加载
- 分布式锁、多实例部署、K8s
- ~~CI（远端）~~（2026-09-07 接入、09-08 转绿收官）、~~mypy 严格化~~（2026-09-08 方案 A 基线清零入门禁；--strict 全量 679 错存量不做）、uv/poetry 迁移
- 群聊共享 session、用户身份订阅
- 本地 BLAST+、AlphaFold 结构预测（原 Phase 20 方向，2026-09-01 取消）
- GHIST 组织学图像分析（2026-09-06 Phase 37 评估**暂缓**：toolsv1 实现为 PyTorch UNet3+ 从零训练框架，需 Xenium 级输入 5 类文件 + 核分割预处理链，无预训练权重、建议 24GB 显存——属独立赛道投入；待 GPU + Xenium 数据齐备后立项）

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
| 2026-09-05 | Phase 30 可视化模型切换：/model 管理卡（admin=FEISHU_ADMIN_OPEN_IDS 复用）+ llm.yaml providers 候选池（key 只在 .env 不落库不回显）+ llm_active 单行表持久化（迁移 0005）+ LLMRouter.reconfigure 原地热切换（共享引用全局生效）+ model_switch 卡片回调审计闭环（llm_model_switched/_denied）+ 启动 DB 记忆恢复；TDD 单测 19 + 集成 4，全量回归 1006 全过；真机验收 2026-09-07 通过（ut-7：admin 名单配置 + 双卡片交互改造 + ctx 双挂载修复，见统一测试轮行） |
| 2026-09-05 | Phase 31 富集分析 sc_enrichment（toolsv1 迁移第一批，用户确认只做富集/按类分流）：gseapy ORA+GSEA（hallmark/GO BP/KEGG 三基因集，构建期 Enrichr 预取 /opt/gene_sets 解决容器断网，MSigDB 直链国内 404 后的替代方案）+ 镜像重建 + 容器真跑冒烟（断网容器 ok:true，B 细胞 mock 数据富集出 BCR 通路生物学自洽）；TDD 4 用例，回归 1006 全过 |
| 2026-09-05 | Phase 32 常用分析三件套（toolsv1 第二批，用户决策真机验收延后统一逐个测）：sc_score_genes 基因集打分（多集 object 参数，等价 AddModuleScore）+ sc_metabolism KEGG 代谢活性（复用镜像 kegg.json 逐通路打分，簇均值+方差 top 热图防响应膨胀）+ sc_pseudotime 扩散伪时序（diffmap+DPT，root_marker 定根，inf→None 防 JSON 非法值）；本机三脚本假数据冒烟全过（B_cell 簇偏移/根细胞定位/断连 0 生物学自洽）+ 镜像重建（4f107c66，断网容器 import ok）；TDD 7 用例，回归 1019 全过；容器真跑与真机验收延后统一测试轮 |
| 2026-09-05 | Phase 33 常用分析四件套（toolsv1 第三批，全选确认）：sc_de 组间差异（rank_genes_groups 定向两组，列/取值不存在时错误列可用列引导自纠，火山图）+ sc_subcluster 亚聚类（raw 重建管线→**新 dataset_ref** `{id}_sub{n-m}`，下游 12 工具零改动可链，支持多级）+ sc_integrate 批次整合（bbknn→新 dataset_ref `{id}_bbknn`，双联 UMAP；annoy 无轮需 g++ 同层临时编译后 purge）+ sc_cellfreq 组成比较（比例表+堆叠图+每簇卡方）；本机四脚本冒烟生物学自洽（GZMB↑/MS4A1↓、簇 2 卡方 p=7.6e-12、新 ref 可回读）+ 镜像重建 edcdbc2e 断网容器 bbknn+四脚本 import ok；TDD 6 用例，回归 1025 全过；容器真跑与真机验收延后 |
| 2026-09-05 | Phase 34 B 类三件套（toolsv1 第四批，sc_* 13→16）：sc_cellchat 细胞通讯（liana 1.10.0 cellchat 方法+内置 consensus/mouseconsensus 资源库离线，assert_covered 要求基因覆盖率≥98%）+ sc_milo 差异丰度（KNN 邻域+QP-GLM（Poisson+全局 Pearson 离散度 floor=1）+BH，实施修正：固定 α=1 NB-GLM 在 2v2 小样本次数下无检验功效，非 edgeR QL/SpatialFDR 偏保守口径）+ sc_deconv bulk 解卷积（wnnls=MuSiC 式加权 NNLS / nusvr=CIBERSORT 式线性 NuSVR 简化版，bulk 走数据根白名单+/data 挂载，方向反自动转置）；宿主假数据冒烟生物学自洽（cellchat CXCL12 簇0→簇1 命中、milo top 邻域 majority=簇2 log2fc=2.58、deconv wnnls 还原误差 0.000）+ 镜像重建 342ef18a738f（断网容器 16 模块 import+liana 资源库 (4620,2) ok；pandas 3.0.5→2.3.3 满足 liana<3）；TDD 7 用例（累计 34），回归 1032 全过；真机验收延后统一测试轮 |
| 2026-09-05 | Phase 35 注释与质控补强四件套（toolsv1 第五批，sc_* 16→20）：sc_annotate 双路注释（celltypist 1.7.1 参考注释——Immune_All_Low/High.pkl 构建期预取 /opt/celltypist_models/ 断网可用，leiden 级 majority voting，写回 celltypist_label/celltypist_conf；markers 路用户基因集簇投票写回 out_col 默认 annotation）+ sc_meta 元数据编辑（merge_csv 白名单挂载/map_values/rename_col 链式写回 processed.h5ad）+ sc_doublet 双联体检测（scrublet 0.2.3，counts 走 filtered 链按 obs_names 写回，自动阈值失败分位数回退，只标记不删）+ sc_cellcycle 细胞周期（Tirosh S/G2M 43+54 基因内嵌离线，写回 S_score/G2M_score/phase）；宿主冒烟生物学自洽（markers 三簇投票全对 T/B/NK、celltypist n_labels=3、doublet 注入组均分 0.174>其余 0.039、S 期富集断言通过、meta 三 op 链式正确）+ 镜像重建 83369d55f9c5（断网容器四新模块 import+模型清单+annotate 迷你跑通）；TDD 9 用例（单测 43 passed=34+9），回归 1041 全过（55.33s）；真机验收延后统一测试轮（累计 15 工具） |
| 2026-09-06 | Phase 36 调控网络 sc_scenic（toolsv1 第六批，sc_* 20→21）：pySCENIC 0.12.1 三幕（GRNBoost2→cisTarget→AUCell）；DB 运行时只读挂载不进镜像（settings BIO_SCENIC_DB_ROOT + handler 挂 cisTarget_databases/motifAnnotations → /scenic_db/ 两路），TF 从 motif tbl 派生，species 人/鼠 + db 500bp/10kb/both；宿主 probe 实测五坑全绕（setuptools<81 锁、grnboost2 走 create_graph(include_meta=True)+client.compute 绕 dask-expr、prune2df from_delayed 物化 monkeypatch、np.object/np.float 别名 shim 经 site-packages sitecustomize 覆盖 dask worker 子进程、_prune 改线程式 LocalCluster(processes=False) 防 spawn 子进程不继承进程内 shim；另修 RSS 方向行=簇）+ 禁用 custom_multiprocessing；冒烟 200 细胞×300 基因 SPI1 注入模块 top-20 回收 6/9、2 regulons、AUC (200,2)；镜像重建 e18ef7b3d7aa（COPY 路径修正为上下文相对；断网容器 shim+import+带挂载真跑三项验证通过，dask loopback 在 --network none 下正常，结果与宿主一致）；TDD 4 用例（单测 47 passed=43+4），回归 1045 全过（51.80s）；中途 settings 字段被子代理并行写竞争丢失致 9 测试失败，补回后全绿；真机验收延后统一测试轮（累计 16 工具） |
| 2026-09-06 | Phase 37 WNN 多组学 + 虚拟敲除（toolsv1 第七批，sc_* 21→23，c429a8a）：sc_wnn（muon 0.1.9，RNA+ADT 双文件白名单挂载 + 幂等双 hash 新 dataset_ref，n_multineighbors<n_obs 堆腐化防御，raw=RNA lognorm 接入全生态）+ sc_knockout（scTenifoldKnk 1.1 R 容器保真链路：dense CSV→Rscript knk.R→diffRegulation 火山图，Debian trixie r-base 4.5 + P3M 二进制 R 层 68s）；wnn 宿主冒烟 ARI=1.000 + knockout 断网容器 86s 回收注入靶基因 7/9；TDD 6 用例，回归 1051 全过；真机验收延后统一测试轮（累计 18 工具） |
| 2026-09-07 | **统一测试轮（ut-1~ut-8）收官：18 工具×双物种验收 30/30 PASS**（人/鼠双物种 tiny 数据 + 基座链路，scenic 双物种各约 37-41 分钟为 pyscenic 固有开销）；ut-2 物种适配小修（sandbox 脚本人/鼠基因符号与列名适配）；ut-6 真 LLM 规划抽查 3/3（doublet/cellcycle/enrichment，确认 sc_* 仅 /research 与 /code 两入口）；ut-7 飞书端真机抽查发现并修复 4 项：.env 缺 FEISHU_ADMIN_OPEN_IDS、sc_load 对既有 dataset_ref 直通防 SC_PATH_FORBIDDEN、send_card dict header 渲染 bug、/model 双卡片交互改造（卡 1 槽位入口→卡 2 候选池全量，回调响应卡原地换面）+ build_runtime ctx 双挂载修复；全量回归 **1064 passed** |
| 2026-09-07 | Phase 38 自然语言意图预判（ut-6 产品决策点落地，用户选定「意图预判+自动转研究（确认闸）」）：IntentGateService LLM 轻量分类三路由（research/code/chat）→ 疑似任务意图发确认卡（确认执行（分类路径）/ 改用另一路径一键纠偏 / 忽略，owner 内嵌 + 内存幂等 + TTL 1800s 过期失效，对齐 code_approval 模式不落库）→ 确认后经 research_intent 回调按路由分发 ResearchRunner/CodingRunner（等同 /research 或 /code），回调非法 route 回退分类路由；分类/发卡异常一律回退闲聊（绝不阻断正常聊天）；单挂载点设计（只挂 orch，回调经 ctx.orchestrator 同源取，根上规避 ut-7 双挂载事故）+ 卡片原地换面去按钮；settings INTENT_GATE_ENABLED/TTL；审计 target_id 链补 intent_id + intent_approved 专项审计带路由；toolsv1/ 参考仓库按用户决策移出归档 I:\archive\toolsv1；TDD 新增 30 用例，全量回归 **1093 passed**；真机验收已通过（研究卡/代码卡/纠偏换路三场景用户确认） |
| 2026-09-07 | Phase 39 /code 过程反馈 v2 进度卡（用户批准方案：v1 节流文本刷聊天流 → v2 单张进度卡原地刷新）：im_adapter 新增 update_card（飞书主动更新 API PATCH /open-apis/im/v1/messages/:id，SDK-only，区别于回调换面无需用户点击；_card_json 静态助手与 send_card 共用）+ coding_runner 新增 _ProgressCard（每 3 工具事件且最小间隔 2s 节流原地刷新、最近 5 条事件滚动窗、tools_disabled/compressed 关键事件立即刷新、更新失败熔断不再重试、finish/finish_error 终态定格三卡面）+ _make_reporter 工厂（start 拿不到 message_id 或发卡异常回退 v1 _ProgressReporter，接口对齐 v1 补 no-op finish）；TDD 新增 18 用例（进度卡 10 + 工厂 5 + update_card 3），test_coding_e2e 五处卡片计数断言按 action 过滤修复（进度卡无 actions 元素，auto_approve 线程须跳过否则 KeyError 杀线程假红）；第三次同类 SearchReplace 落盘丢失事故后纪律固化（同文件单编辑+grep 回读）；全量回归 **1112 passed**；ws_client 重启 pid=37208；真机验收已通过（6 步多工具任务终态卡「✅ 完成 · 6 steps · 耗时 40s」渲染正确，用户贴出输出确认） |
| 2026-09-07 | run_cmd 工具描述加固（真机暴露 LLM 把 "python && gen.py" 整串塞单元素 2✗ 自愈）：SCHEMA 描述明确 cmd 独立参数数组 + 正例 ["python","gen.py"] + 禁用 shell 连接符（&&/||/|/>/;）+ 反例警示；回归测试 +1，全量 **1113 passed**；真机复测 6✓/0✗ 零失败（对比加固前 7✓/2✗）验收通过；ws_client pid=79228 |
| 2026-09-07 | Phase 40 未知斜杠命令兜底提示（真机 /clear 落闲聊回复"我没有清除功能"暴露体验缺口）：app.py 1.72 分支——已知命令（市场 12 条 + /research /code /model /bind-doc 系）此前已全部路由，走到这里的 / 开头消息即未注册命令，回「[未知命令] xxx + 可用命令清单 + 自然语言入口提示」而非静默落闲聊 LLM；TDD +4 用例（兜底提示/带参只回显命令词/群聊同提示/已注册命令回归保护），全量回归 **1117 passed**；ws_client pid=4840；真机验收已通过（/clear 与 /restart 均正确返回命令清单提示，用户贴出输出确认） |
| 2026-09-07 | Phase 41 /research 进度卡复用（用户选定「进度卡复用到 /research」，scenic 40 分钟级长任务最受益）：Scheduler 无事件钩子 → 观察线程模式（每 2s 拍节点快照喂卡），_ResearchProgressCard 与 coding _ProgressCard 同构（节流 min_interval=8s/PATCH 熔断/禁用回退）——受理即发「规划中」卡 → plan_done 转「执行中 节点x/y（✓✗⊘–）」→ 终态数变化立即刷新 → finish「研究任务完成」/finish_error「异常终止」三态定格；运行中节点带已耗时（12m34s 格式）、最近完成滚动 5 条；plan_failed/超时/异常三出口均定格；未 start 或 CLI 通道无 message_id 全 no-op 行为同旧版；TDD 17 用例（单测 14：生命周期/节流/熔断/卡面/快照/耗时格式化 + 集成 3：全流程/规划失败定格/禁用回退），既有 5 处审批卡断言按「末元素含 actions」过滤修复；全量回归 **1134 passed**；ws_client pid=45608；真机验收已通过（用户观察到规划中→执行中→终态流转；另暴露 SC_QC_OVERFILTERED 数据画像缺失问题 → Phase 42） |
| 2026-09-07 | Phase 42 数据画像注入规划提示（方案 B，根治真机 SC_QC_OVERFILTERED：planner 不知数据规模套 sc_qc 默认 min_genes=600，f1e89bf88edc genes/cell 中位数仅 66 → 全过滤）：新增 orchestrator/tools/bio/dataset_profile.py——extract_dataset_refs（12 位 hex，去重保序 ≤3）+ profile_dataset（h5py 直读 raw→filtered→processed 读取链，dense/CSR 双编码，>5000 行抽样分块统计，var['mt'] 布尔列优先否则 MT-/mt- 前缀派生，任何异常 → None 绝不影响主链路）+ build_profile_context（画像段 + 「min_genes ≤ median/2、max_mt_pct 参考 p95、严禁套默认值」指引）；research_runner._execute 在 session_context 追加画像段（try/except 包裹）；真数据冒烟 median=66 p90=73 max=83 与真机错误消息完全吻合；TDD 16 用例（单测 14：ref 提取/dense/CSR/mt 列优先/读取链/四类容错/上下文构建 + 集成 2：注入含画像/纯文本零影响回归）；全量回归 **1150 passed**；ws_client pid=51372；真机验收已通过（用户重发同任务（不带显式阈值）：n1 质控/n2 聚类/n3 双联体全 success，300 细胞零过滤、3 簇各 100、双联体 3 个 1%，planner 按画像自主选对阈值） |
| 2026-09-07 | CI 接入（用户选定「本地 CI + 预置云端配置」）：.githooks/pre-push 质量门钩子（→ scripts/check.ps1：ruff check + pytest 默认层+cov，SKIP_PRE_PUSH=1 紧急跳过）+ core.hooksPath 版本化 + .gitattributes 钩子锁 LF + 预置 .github/workflows/ci.yml（ubuntu+py3.12+postgres:16-alpine 5433 对齐 PG_TEST_URL 默认值可跑真库层，docker 层无 kernel 镜像自动 skip；依赖走 .[dev] 因 lock 含本地 -e 绝对路径自引用仅本机用）；副产物三修——h5py/numpy 补声明入 pyproject+lock（Phase 42 遗漏的静默缺失风险）、ruff 存量 26 违规清零（22 --fix + 4 手动，Phase 10 后门禁未执行劣化，含 Phase 39/42 自身违规）、check.ps1 basetemp 钉仓库内（WinError 5）+ 恢复 UTF-8 BOM（编辑丢 BOM 致 PS 5.1 解析失败）；钩子完整路径实测 exit=0（1144 passed + 6 deselected，71s），全量回归 1150 passed 零回归；ws_client 无需重启 |
| 2026-09-07 | /model 池扩充 Kimi（用户提供 coding plan url+key）：llm.yaml providers 加 name=kimi（LLM_PROVIDER_KIMI_* 三 env，Phase 30 预留扩展流程）+ .env 真值 + .env.example 示例更新为 api.kimi.com/coding/v1；模型名 GET /models 实测四款，用户选定 k3（1M 上下文，连通性 200 出 pong；另有 kimi-for-coding/highspeed/k3-256k 可追加）；reasoning_content 独立字段不污染 content，项目极简 payload 原生兼容（Windows curl -d 转义坑，验证走 Python httpx）；config_check OPTIONAL 遵循 Phase 30 决策不登记池键位；全量回归 **1150 passed**；ws_client pid=47568；真机验收已通过（/model 三候选显示与切换用户确认无问题） |
| 2026-09-07 | fallback 模型 glm-4.7 → glm-5.3（用户指定；GLM coding 端点连通性实测 200 出 pong）：仅 .env LLM_FALLBACK_MODEL 一处变更；settings 解析确认；ws_client pid=52204 |
| 2026-09-07 | Phase 43 /code 数据画像注入（任务 2，根治 /code 侧 sc_qc 套默认阈值隐患；调研澄清 bioqc 仅 skills 知识面文档无工具，/code 实际调 builtin sc_qc）：coding_runner.__init__ 存 bio_workspace_root（空=关闭向后兼容）+ run_sync system 追加画像段（复用 Phase 42 build_profile_context，try/except 包裹不影响主链路）；TDD 3 用例（ref 命中注入含 median=2 核对/纯文本零注入/失败静默跳过）先红后绿；全量回归 **1153 passed**；ws_client pid=81152；待真机验收 |
| 2026-09-07 | Phase 44 场景级 provider 静态双绑 A1（任务 3，用户选定；架构调研：全链路单 LLMRouter+reconfigure 全局生效，分场景须建第二实例；code_model 只能同 provider 换名跨 provider 不可行；planner role 路由 Phase 5 预留未实现）：新模块 scene_router.build_scene_router（命中池→独立 LLMRouter primary=池条目/fallback=出厂 fallback，空/miss→None 告警回退全局）+ settings CODE_PROVIDER/RESEARCH_PROVIDER + Orchestrator(research_llm=)（planner 走场景，orch.llm 保持全局管闲聊/意图闸）+ research_runner._research_llm（Scheduler condition/repair 双接）+ runtime 装配（CodingRunner/SkillDiagnoser 用 llm_code or llm）；部署分工：llm.yaml 加 kimi-coding 条目（与 kimi 共享 url/key env）+ .env CODE_PROVIDER=kimi-coding（kimi-for-coding 专精）/RESEARCH_PROVIDER=kimi（k3 1M 长上下文）；TDD 9 用例先红后绿（planner 重分支断言改 research_llm 属性+集成覆盖）；全量回归 **1162 passed**；ws_client pid=73668 启动日志确认 scene llm bound: code=kimi-coding research=kimi；**真机验收已通过**（ws_client.err.log 逐调用日志硬证据：/code 4 次调用全 model=kimi-for-coding、/research 2 次调用全 model=k3，同端点靠模型名区分场景路由成立） |
| 2026-09-07 | Phase 44 验收补强——LLMRouter 逐调用日志（调研发现 router 全程零调用日志/审计，场景路由无法实锤出话模型）：_call_once 与 _call_with_tools_once 各加 logger.info("llm call: model=%s host=%s")，价值=此后所有场景（闲聊/意图闸/code/research）每次出话均可从日志核对实际模型与端点；副产物修复 test_scene_router 环境隔离（真实 .env 含 CODE_PROVIDER 时 load_env_file setdefault 回注致默认空断言失败，monkeypatch load_env_file 为 no-op）；全量回归 **1162 passed**；ruff 绿 |
| 2026-09-07 | 任务 1 闭环：git 远程 + CI 双门禁激活（用户选定 GitHub + 浏览器协作建仓库）：内置浏览器登录建 Private 空仓库 lkp101ksp-spec/feishu-agent；推送前安全自查通过（.env gitignored/全库无真实密钥/.env.example 占位符）；排障三连——GCM 登错号 erase 后改 device code 流（内置浏览器正确账号授权）、git 不走系统代理改仓库本地 http.https://github.com/.proxy=127.0.0.1:17890（全局 .gitconfig 被沙箱拦）、device token 404 改内置浏览器直建 classic PAT（repo+workflow 30 天）经 credential-manager store 持久化；首 push 被本地 pre-push 拦下临时脚本 E501（门禁实证有效）；push 成功 master 建 track，Actions CI 首跑自动触发；**验收通过** |
| 2026-09-07 | CI 转绿排障收官（run1 红→run7 绿，全为 ubuntu/无 .env 环境与本地 Windows 差异）：① ctypes.WinDLL 仅 Windows 存在致 3 测试文件收集崩溃（exit 2）→ sys.platform 守卫置 None（18c0c99）；② research_runner 后台线程"先回复后写 audit"，测试见回复即过、fixture dispose 与线程写库竞态 → Linux sqlite C 层段错误（exit 139 SIGSEGV，Windows 不崩纯时序运气）→ 拆棚前 join 本测试新起线程（2c74fb2）；③ 诊断增强 ci.yml pytest -q→-v（崩溃无摘要时逐测试命名定位，44f663c）；④ CI 无 .env 致 6 测试 KeyError（FEISHU_APP_ID/DATABASE_URL）→ tests/conftest.py 哑值兜底（仅无 .env 时 setdefault 生效，本地零影响）；⑤ engine 池参数 pool_size/max_overflow 对 sqlite 非法 → 仅非 sqlite 传（真 bug：.env 配 sqlite 即启动崩）；⑥ _container_to_host_path PureWindowsPath 化试错→回退原生 Path（sc/st 测试 posix tmp_path 根被破坏），盘符语义单测限 win32；⑦ resolve_safe 跨平台显式拒绝 Windows 绝对路径（Linux 下 "C:/..." 非绝对漏判，真加固）；⑧ cmd shell 测试改 curl --version（跨平台 need_approval）；⑨ scheduler 测试驱动协程真竞态——FAILED 也是终态，_all_terminal 瞬时窗口致 Linux 下 drive 提前退出 RUNNING 永挂（Windows 靠 15ms 定时器分辨率侥幸），改 done 标志消竞态；**run 34147592219（af2249e）SUCCESS，默认层+pg 真库层双绿，任务 1 完全闭环**；诊断脚本 scripts/_ci_log.py 留存（GCM 读 PAT + 代理拉 Actions 日志，支持段错误上下文/FAILED 摘要/按测试名抓 traceback 三模式） |
| 2026-09-08 | CI 依赖锁定（64c8549，run 34173415492 绿）：requirements-lock.txt 由 Phase 10 的 61 行陈旧锁刷新为 161 钉版并剔除 "-e i:\飞书agent" 自引用，本机/CI 收敛共用一份（项目本体 pip install -e . --no-deps）；ci.yml 安装步骤改装锁文件 + setup-python 缓存键指向锁文件——消除 pip install -e .[dev] 每次装上游最新版的漂移面；更新流程写入锁文件头部注释（freeze 剔 -e 行；Windows 专属包需手动加 sys_platform 标记） |
| 2026-09-08 | mypy 方案 A 收官（评估后用户选定执行）：宽松基线 102 错/22 文件清零，门禁范围扩至 8 源码包 173 文件；修复方式≈90 处纯注解/收窄（TYPE_CHECKING 精确类型、Orchestrator 17 服务属性类级注解、anndata stub cast、TypedDict、_UNSET 哨兵）+ 3 处防御分支（原路径必崩改清晰异常）；**揪出两个真 bug**——① freeze_session 给 SessionRepo.upsert 传 4 个不存在参数且缺必填位置参数，真实库调用必 TypeError（SessionRow 早有列、repo 签名停在 Phase 1，测试因 MagicMock 未暴露）→ upsert 扩展十参数+哨兵语义修复契约漂移；② DocWriteRepo.mark_success 注解过窄（CLI 路径合法 None）放宽 Optional；门禁四挂点：pyproject [tool.mypy]（explicit_package_bases+ignore_missing_imports+packages 白名单）+ check.ps1 [3/4] 硬门禁 + ci.yml ruff 后 mypy 步骤 + pre-commit 钩子；锁文件刷新 166 钉版（含 mypy 2.3.1）；全量回归 **1162 passed**（72.12s）；过程：4 并行子代理分批修复，SearchReplace 假成功约 10 处全部 PowerShell 兜底验证；CI 首轮（28e737c）mypy 步骤红——门禁上岗即立功：ctypes.WinDLL/get_last_error 为 Windows 专属符号，三元式不触发 mypy 平台可达性特判（本机 Windows 验证存平台盲区）→ 0082586 改 sys.platform if/else 守卫块 + --platform linux 反模拟，**run 34218388229 全绿收官** |
| 2026-09-08 | freeze_session 真库回归 + AnyBlock 注解债合并：3 条真库测试（sqlite StaticPool 真 repo）红→绿，暴露第三个真 bug——freeze 继承判定 naive datetime 对 aware 直接比较必 TypeError（mock 测不出，pg 普通 DateTime 列同样中招），按 bound_doc_id() 同款补 UTC 修复；清除上轮假成功残留的 type: ignore；AnyBlock 扩为 16 类判别联合移至 schemas.py 末尾，serializer 摘除 FullBlock 别名（纯注解零行为变化）；scripts/_ci_log.py 收编版本库；SearchReplace 假成功新变种 4 处（diff 逼真但没落盘）全部 [IO.File] 直写兜底 |
| 2026-09-08 | Phase 3.1（freeze/compress 生产接线）**归档挂起**：真机验证前侦察发现链路从未接线且宿主架构不存在——SessionService.freeze_session / ContextCompressor.maybe_compress / FreezeRequired 生产零调用方，闲聊主链路单轮无状态（llm.chat 仅 [system, 当前消息] 无历史可压），coding agent_loop 自带 compress_messages 压缩在跑；此前三轮修复（upsert 契约漂移 / naive datetime 比较）修的是不可达路径，组件与真库测试保留备用，真做长会话 feature 时再接；修 app.py process_phase3 docstring 漂移（声称"ContextCompressor 在 LLM 调用前监控"与实际不符） |
| 2026-09-09 | **长会话记忆落地，Phase 3.1 由挂起转为正式落地**（spec `docs/superpowers/specs/2026-09-08-long-session-memory-design.md` + 8-Task 计划，方案甲服务层接入）：messages 表（迁移 0006，链已推进至 0005 故重编号）+ MessageRepo（append/list_all ULID 兜底次序/replace_all 压缩回写）+ ChatMemory 编排（prepare 装配历史/压缩回写/_freeze 摘要→freeze_session→新会话播摘要种子行→通知，一切外部故障降级不阻断聊天；append_turn 双写；clear）+ ContextCompressor.summarize_only（freeze 前强制摘要）+ Orchestrator 接线（/clear 路由、闲聊注入历史、freeze 后 session_id 换新下游随行走、未装配退化为无记忆单轮）+ runtime 装配 + settings 四阈值接 env（字段早已存在但 load_settings 从未读 env，真机暴露）；TDD 新增 18 用例（3 repo + 2 summarize + 6 chat_memory + 4 集成 + 3 真审计契约回归）；**真机验收两轮**：第一轮连撞 3 个真 bug（①四个 context_* 阈值 env 未接线默认 200k 永不触发；②freeze_session/maybe_compress 两处 audit_repo.write 缺 audit_id 必填参——Phase 3.1 归档代码从未真机跑、测试全 MagicMock/None 掩盖，同路径第三次契约漂移；③runtime 未传 freeze_repo 致 session_freezes 不落库），全部修复并以真 AuditRepo 钉回归；第二轮压缩/冻结通知/摘要继承（新会话能答出"单细胞课题"）/sessions 四代冻结链//clear 全部符合预期；全量回归 **1181 passed**；CI run 34247750820 绿；ws_client 阈值已还原默认（pid 51836） |
| 2026-09-09 | 长会话记忆加固二连：①服务层 __init__ 类型注解（de982b6，audit_id 缺参逃过 CI 的根因=三服务构造参数裸 Any，注解后 mypy 可静态拦截同类缺参；mypy 双平台 175 文件 + ruff + 回归 1181 绿，CI 34343017503 绿）；②**运维加固轮 2**（事故驱动：Docker 停运→pg 不可达→pytest 挂死 40min + ws_client 静默死亡无人知晓）——ws_client 文件日志（logs/ws_client.log 滚动 5MB×3，隐藏窗口启动 stderr 全丢的排查盲区补齐）+ gateway/health_monitor.py DBHealthMonitor（SELECT 1 探活状态机，健康↔故障跃迁给 FEISHU_ADMIN_OPEN_IDS 发飞书告警，故障期指数退避 30s→300s 封顶；**probe 走 psycopg connect_timeout=5 短超时直连而非共享 engine**——真机复现 docker stop 后 engine.connect() 无限挂起，监控线程被拖死等于没有监控）+ scripts/ws_guardian.py 守护进程（pidfile 探活+死亡拉起+30s 崩溃循环冷却+自身单实例守卫；登录自启走 Startup 目录 VBS——schtasks 需 UAC 被替代，免权限）+ pytest-timeout 2.4.0 全局单测 300s 硬上限（锁文件同步刷新，ADR-0028）；真机验证：pg 停机 5~10s 探活失败告警→退避探测不轰炸→pg 恢复 RECOVERED；杀 ws_client 后 guardian 首轮 tick 拉起新进程 12s 完成上线；副产物定位 wedge 点=ModelSwitchService 启动 DB 恢复调用无超时阻塞 130s（异常可吞但启动被拖住，遗留优化）；TDD 新增 15 用例（7 monitor 状态机 + 8 guardian 含单实例），全量回归 **1196 passed** |
| 2026-09-09 | 加固二连：①**启动期 DB 超时**（60328a8，wedge 类根治）——engine.py 抽 `_engine_kwargs()` 作 pg/sqlite 方言参数唯一出口，pg 统一 `connect_timeout=10`（此前 pg 黑端口靠 OS TCP 重传 130s 才报错，ModelSwitchService 启动恢复被拖两分钟），configure_engine 替换路径同出口防分叉；TDD 3 用例钉契约，回归 1199，CI 34354932815 绿；②**方案 B persistence 层 mypy strict**（6447517）——22 错清零（models.py 9 处 JSON 列 `dict[str, Any]`/`list[Any]` 泛型化 + 7 repo 签名同步 + engine.py 3 处），pyproject 挂 `persistence.*` overrides；**mypy 2.3.1 新坑：overrides 内 `strict = true` 泄漏为全局 strict（176 文件 0→554 错实证），改显式展开 10 个 per-module flags**（warn_redundant_casts 系全局 flag 被警告剔出）；探针实验验证门禁非摆设（裸 dict 注入被 no-untyped-def+type-arg 双规则抓获，备份法恢复不用 git checkout）；SearchReplace 假成功大爆发（批量 14 处约半数未落盘 + 验证正则漏匹配 `dict | None` 形态），纪律升级为单个替换+立即 Grep 验证；mypy 双平台 176 文件 0 错、ruff 绿、回归 **1199 passed** |
| 2026-09-09 | **Phase 15/27 验收状态对齐**（用户确认直接采信历史证据）：两 Phase 验收实际早已完成——P15 群聊 2 场景 2026-09-08 真机通过（日志 WARNING research_writeback forbidden operator≠owner / decided approve → doc_writes success 硬证据在段落内）、P27 真机验收 5/5 于 2026-09-04 晚收官（变更记录早有独立行）——唯段落标题未随勾选更新，致后续会话误判"待人工"挂账近一周；本次仅改标题为已验收收官，无代码变更。教训沉淀：验收完成后标题/勾选/变更记录三处须同次更新，防状态漂移 |
| 2026-09-10 | **方案 C gateway/orchestrator 层 mypy strict**（fcef71c）：433 错清零（type-arg 267 + no-untyped-def 170 + no-any-return 43 + no-untyped-call 6），6 并行子代理按子包分批（gateway 61/coding 70/templates 67/tools 83/planner+executor+runtime 43/根部 109，独立 cache-dir 防竞争）；零 type: ignore，鸭子类型注入点标显式 Any 防不实标注；pyproject overrides 扩为 [persistence.*, gateway.*, orchestrator.*]；排障两则——①子代理把 ApprovalBroker 挪 TYPE_CHECKING 却运行时 cast(ApprovalBroker, ...) 致 NameError（cast 首参求值名字，8 审批集成测试抓获）改字符串形式，教训：TYPE_CHECKING 名字绝不进运行时表达式；②.pytest_tmp 混入 mypy_cache 致 basetemp 清理 201 errors 虚惊（环境污染非代码问题），教训：basetemp 目录只给 pytest 用；**副产物：plan_runtime.py 5 处 audit_repo.write 缺必填 audit_id 契约漂移**（同 freeze_session 型，生产未接线未炸过，FakeAuditRepo 掩盖），以 _AuditSink Protocol 如实标注待决策；mypy 双平台 176 文件 0 错、ruff 绿、回归 **1199 passed**；ws_client 重启 guardian 自动拉起（37356→25808）自愈链路再验证 |
| 2026-09-10 | **全库 strict 收官 + audit_id 契约修复 + VBS 自启失效修复**（8230e47）：①plan_runtime 5 处 audit_repo.write 补 audit_id=new_ulid()（用户拍板调用方生成、契约必填不弱化），_AuditSink Protocol 对齐真签名，TDD 契约钉子测试覆盖全部 5 审计路径（含失败路径——首版漏 4/5 红得正确）；②第三批 strict 五包 115 错清零（sandbox 68/feishu_adapter 41/config 3/shared 3/skills 0 白捡，2 并行子代理），sandbox 用 callable 别名 cast 处理 dask/scanpy 第三方 untyped 调用（TYPE_CHECKING 导入+字符串首参，运行时延迟导入不动），feishu_adapter 38 处泛型化+10 处局部注解收窄零 cast；pyproject strict flags 升全局根除 mypy 2.3.1 overrides 泄漏坑；③**Windows Update 08:08 重启暴露 VBS 自启失效**——Startup 文件夹裸 VBScript 新版 Win11 不执行（StartupApproved 注册表 .lnk 有条目唯 .vbs 无；且设立以来从未经历真实登录验证，属"未验证的自愈承诺"），换 FeishuAgentWsGuardian.lnk 并 Invoke-Item 验证全链路（单实例守卫正确拒绝第二实例），待下次真实重启终验；Docker Desktop 手动拉起后 pg/sandbox 补测 10 项全过，**1200/1200 全数验证**；CI 34424015904 绿；运维知识：venv pythonw 是 redirector 壳、每逻辑进程=2 OS 进程勿误判双开 |
| 2026-09-10 | **B1 CNV 推断落地：sc_cnv 双后端工具**（spec docs/superpowers/specs/2026-09-10-scnv-design.md，commit e5fbd10）：infercnvpy（成熟默认）+ cnvturbo（对齐 R inferCNV HMM i6）同源起步——counts 走 filtered 链、双后端从同一矩阵出发保交叉验证可比；有参考模式（内置非恶性清单子串匹配 + ref_groups 显式覆盖，零匹配 SC_CNV_NO_REFERENCE 不静默降级）；恶性判定 infercnvpy 路参考分 mean+3sd+CNV 簇多数投票 / cnvturbo 路 HMM 细胞级；GRCh38 坐标 TSV 构建期烘焙（fetch_gene_pos.py，Ensembl GTF→~2MB，断网可用）；产物含 figS3B 式染色体热图/score/subclone UMAP/注释×恶性 csv/亚克隆×染色体 csv（D 报告汇编素材）；TDD 注册单测 3 + 真容器集成测试（合成 chr7 gain/chr10 loss 双后端判定吻合 ≥80% 精度）；CI 34477627304 绿 |
| 2026-09-10 | **D 分析报告自动汇编落地**（spec docs/superpowers/specs/2026-09-10-report-design.md，commit cbb8df7）：research_runner 收尾钩子条件触发（≥1 个 SUCCESS sc_*/st_* 节点）+ try/except 故障隔离——orchestrator/report 三文件模块：section_digest 泛化产物收集（图片四键 umap/dotplot/spatial/pngs + .csv 输出 + 标量关键数字自动分类，SECTION_TITLES 13 工具映射未知名回退工具名，csv 头 30 行摘要 + 4000 字符头 45% 尾 30% 截断；container_to_host 自带副本防循环依赖）→ report_builder LLM 逐节解读（MAX_LLM_CALLS=12 护栏、单节失败模板降级不中断、总评失败省略）→ md 底稿落 bio_workspace/{ds}/report/report_{ts}.md（图相对路径）+ DocAdapter.create_document/grant_doc_view（独立 BaseRequest）新建云文档插图交付，maybe_build_report 四态 skipped/sent/degraded/md_only 逐级降级；settings.report_folder_token（env REPORT_FOLDER_TOKEN）配目标文件夹；TDD 单测 7+8+11 + 钩子集成 3 用例（自动建文档发链接+授权+md 落盘 / 抛错隔离 / 非 sc 跳过），test_research_runner 全量 39 passed；全量回归 **1235 passed**；CI 34496175073 绿 |
| 2026-09-11 | **B1+D 真机验收（humantest HPV45+ 真实肿瘤 19149 细胞）+ sc_cnv 验收修复**（commit 52f3b84）：首跑 n4 BrokenProcessPool——容器 --memory 16g 内 20533×31884 float64 多份拷贝 OOM 杀 joblib worker，修复=基因表达预过滤（R inferCNV cutoff=0.1 语义 31878→11591）+全链路 float32+infercnvpy n_jobs=2（峰值降至 41%）；二跑暴露 infercnv 输出 NaN 列（PCA 拒绝）加防御性列剔除+chrom_col 对齐+note 记录；**pngs 聚合键补漏**——cnv emit 图键不在宿主收图四键内，IM 发图与 D 报告原均收不到 CNV 图（合成集成测试加断言钉死）；复跑全链路 success：三图到 IM、报告 5 图云文档链接可开、数字抽查零编造、恶性 6625/19149=34.6% 富集上皮（6405/10235）免疫误报 <2%；流程 B cnvturbo 交叉验证：主轴一致（上皮恶性、chr6 缺失主干事件两后端独立复现、chr17 扩共提），边界分歧（cnvturbo HMM 偏松全上皮+浆+pDC 判恶性 55.8% vs 34.6%，Jaccard 0.589）留口径评估挂账；运维疑点挂账：ws_client 8:18 崩溃 guardian 未拉起且双 guardian 并存（单实例守卫疑似失效）；教训：同文件并行 SearchReplace 互覆盖，必须串行；CI 34563897618 绿 |
| 2026-09-11 | **Phase 45 空间统计三分析 st_stats 落地**（spec docs/superpowers/specs/2026-09-11-st-stats-design.md）：st_* 第 9 工具单工具三分析——autocorr（Moran's I/Geary's C 空间自相关 + top4 基因空间分布图）/ cooccurrence（簇间共现曲线）/ nhood_enrichment（簇间邻域富集 zscore 热图，top_pair 取 |z| 最强非对角对——分离结构强耗竭负 z 即核心信号）；邻域图复用 st_process 已建 spatial_connectivities 缺失兜底；cluster_key 回退链 spatial_domain→banksy_domain→leiden→clusters 全灭报 ST_CLUSTER_KEY_MISSING 附可用列；容器探针先行核实 squidpy 1.8.3 签名与 uns 结构（occ/interval/zscore/count/moranI 列），冒烟抓获三坑——spatial_scatter 需 uns['spatial']（无则补占位壳+img=False）、pl.co_occurrence/nhood_enrichment 无 show 参数、fail() 也是 exit 1+stdout JSON（冒烟解析口径对齐 BioRunner）；断网冒烟全绿（注入空间梯度基因 Moran I 0.541 排第一、两簇分离 A~B z=-11.32 强耗竭、INVALID_INPUT 拒绝）；SECTION_TITLES 33 项；TDD 注册 5 用例，全量回归 **1251 passed** |
| 2026-09-11 | **Phase 46 空间 CNV 推断 st_cnv 落地**（spec docs/superpowers/specs/2026-09-11-st-cnv-design.md）：st_* 第 10 工具，infercnvpy 单后端最大化复用 sc_cnv 成熟件（GRCh38 坐标 TSV/内置非恶性清单/cutoff=0.1+float32 内存纪律/NaN 列防御/mean+3sd+CNV 簇多数投票/figS3B 热图）；空间版差异=UMAP 图换 spatial_scatter 组织定位图（st_stats _scatter_img_kwargs 占位壳模式复用）；annotation_key 回退链 cell_type→spatial_domain→leiden + deconv 特殊值读 deconv.h5ad 取 RCTD 权重最大型（生物名命中内置清单），零匹配 ST_CNV_NO_REFERENCE 不静默降级；**探针发现 st 镜像原缺 infercnvpy 与坐标 TSV**——st.Dockerfile 补 infercnvpy 0.6.1 + fetch_gene_pos 层（与 bio.Dockerfile B1 层同构）；断网冒烟一遍过零坑（合成 12x12 网格 chr7 gain/chr10 loss 恶性精度 1.0、NO_REFERENCE/NO_DECONV/INVALID_INPUT 三错误路径、obs 写回容器回读）；SECTION_TITLES 34 项；TDD 注册 5 用例；全量回归 **1250 passed**（-m 'not pg' 门禁口径，较 Phase 45 同口径 1245 恰 +5） |
