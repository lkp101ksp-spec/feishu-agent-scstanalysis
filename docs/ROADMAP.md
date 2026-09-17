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
| 2026-09-11 | **Phase 47 空间生态位重构 st_niche 落地**（spec docs/superpowers/specs/2026-09-11-st-niche-design.md）：st_* 第 11 工具，读 deconv.h5ad obsm['q05_cell_abundance_w_sf'] 组成矩阵（列名去前缀）行归一化 → scipy ward linkage + fcluster(maxclust=k，默认 12)，niche 标签 N1..Nk 按尺寸降序写回 processed.h5ad obs['niche']（st_plot 可着色、st_stats 可作 cluster_key）；产物 niche 空间着色图 + niche×细胞型组成热图（行附 dominant 型）+ 矩阵 csv；**镜像零改动**（scipy/squidpy 已烘焙，COPY 层秒级重建）；错误码 ST_NICHE_NO_DECONV（引导先跑 st_deconvolve）/ k 越界 INVALID_INPUT；断网冒烟一遍过（合成三区组成 k=3 精确恢复 48/48/48、region-ok 容器回读、两错误路径）；SECTION_TITLES 35 项；TDD 注册 5 用例；全量回归 **1255 passed**（-m 'not pg' 门禁口径，较 Phase 46 同口径 1250 恰 +5） |
| 2026-09-11 | **Phase 48 肿瘤邻域分层 st_vicinity 落地**（spec docs/superpowers/specs/2026-09-11-st-vicinity-design.md）：st_* 第 12 工具，st_cnv 恶性 spot 为种子沿空间邻居图 BFS 分层（scipy shortest_path unweighted 一次求距离场：0=tumor、1..max=L1..Ln、其余=distal），obs['vicinity'] 有序 Categorical 写回 processed.h5ad（st_plot 可着色、st_stats 可作 cluster_key）；邻居图复用 obsp['spatial_connectivities'] 缺失按 coord_type 补建（st_stats 同款）；层×细胞型组成热图为条件产物（deconv.h5ad 缺失跳过不报错，has_composition 注明）；种子缺失/零恶性 ST_VICINITY_NO_SEED 不静默降级；镜像零改动（COPY 层秒级重建）；断网冒烟一遍过（连续第三个零坑——合成左半种子 max_layers=2 得 tumor 72/L1 12/L2 12/distal 48 教科书结构 + Tumor/T cells 组成梯度回读 + NO_SEED/INVALID_INPUT 两路径）；SECTION_TITLES 36 项；TDD 注册 5 用例；全量回归 **1260 passed**（-m 'not pg' 门禁口径，较 Phase 47 同口径 1255 恰 +5） |
| 2026-09-11 | **Phase 49 多视图空间建模 st_misty 落地**（spec docs/superpowers/specs/2026-09-11-st-misty-design.md）：st_* 第 13 工具，liana 1.10.0 纯 Python MISTy（用户拍板甲路线，免 mistyR R 依赖）——intra 视图=deconv.h5ad 组成矩阵（obsm['q05_cell_abundance_w_sf'] 去前缀，三 Phase 复用加载逻辑），extra 视图=processed.h5ad HVG 表达（var['highly_variable'] 优先、缺则方差 Top n_hvg 默认 50），genericMistyData(cutoff=0.05, n_neighs=6) + RandomForestModel(seed=42) 建 intra/juxta/para 三视图；bandwidth 缺省 0=auto（5×中位最近邻距离，tool-misty l=5 口径，cKDTree）；产物 target_metrics + interactions 两 csv + 贡献热图/para 互作热图两 png；错误码 ST_MISTY_NO_DECONV / HVG<10 INVALID_INPUT；**探针两轮前置实测 API**（uns['target_metrics'/'interactions'] 列结构、纯噪声 gain_R2 可为 0 甚至为负→断言结构不断言数值下界）；st.Dockerfile 增 liana==1.10.0 层（清华源）；**冒烟首败后修复**——合成信号加在计数空间经 log1p 方差稳定化压缩，g0 方差仅排 35/60 落选 HVG（工具行为正确，数据统计功效不足），改为 log 空间加梯度后一遍过（g0 回收 Tumor para top3、INVALID_INPUT/NO_DECONV 两路径）；SECTION_TITLES 37 项；TDD 注册 5 用例；全量回归 **1265 passed**（-m 'not pg' 门禁口径，较 Phase 48 同口径 1260 恰 +5） |
| 2026-09-12 | **Phase 50 st_misty PROGENy 通路视图落地**（spec docs/superpowers/specs/2026-09-11-st-misty-progeny-design.md）：Phase 49 非目标留位兑现——st_misty 扩 extra_mode=hvg|progeny 参数（非新工具，hvg 默认向后兼容），progeny 模式 decoupler MLM 算 PROGENy 14 通路活性（OmniPath human top=500）作 extra 视图，MISTy 三视图建模样式/产物结构一字不改；**断网纪律不破**：st.Dockerfile 增 decoupler==2.2.0 pip 层 + 构建期 dc.op.progeny 快照 /opt/progeny/progeny_human_top500.tsv（fetch_gene_pos 先例同构，运行期断网读 TSV）；**探针两轮前置实测**（dc.op.progeny→6463 行 14 通路；dc.mt.mlm 返回 None 写 obsm['score_mlm']——**dc.mlm 不存在于 2.2.0，v2 挪 dc.mt 命名空间**；断网 E2E 注入 EGFR 信号 corr=0.998 回收）；防御=net∩var_names<100 INVALID_INPUT（基因名非 symbol）/ TSV 缺失 ST_MISTY_NO_PROGENY；断网冒烟一遍过（builder 用真实 PROGENy 基因名 + EGFR weight×tumor 梯度注入，EGFR 回收 Tumor para top3、n_predictors=14、bad-mode/no-overlap/NO_DECONV/INVALID_INPUT 四错误路径；假名数据集复用作零交集对照——一份数据两用）；TDD 注册 +2 用例（extra_mode 默认/透传）；SECTION_TITLES 37 项不变；全量回归 **1267 passed**（-m 'not pg' 门禁口径，较 Phase 49 同口径 1265 恰 +2） |
| 2026-09-12 | **Phase 51 空间拟时序 st_trajectory 落地**（spec docs/superpowers/specs/2026-09-12-st-trajectory-design.md）：st_* 第 14 工具，sc_pseudotime（Phase 32）模式移植——scanpy diffmap+DPT+PAGA 在**表达邻居图**上推断 spot 进程序映射回组织坐标（UMAP 散点→空间散点、leiden→spatial_domain）；**表达图 DPT 不用空间图**（空间图 DPT≈BFS 距离场，与 st_vicinity Phase 48 语义重复）；root_mode 双模式：marker=root_marker raw 表达最高 spot（sc 同款）；vicinity=st_vicinity 层内表达图度中位 spot（列缺失 ST_TRAJ_NO_VICINITY）；obs 有 vicinity 时附分层 boxplot + Spearman ρ（表达进程×空间分层一致性，st 独有可解读指标）；**镜像零 pip 改动**（st_process 产物 DPT-ready：uns['neighbors']/raw/spatial_domain 齐备，仅 COPY 层秒级重建）；dpt_pseudotime 写回 obs 供 st_plot 叠加（实现期增补，st_cnv/st_vicinity 写回惯例）；断网冒烟两败后绿：①宿主端网格轴向取错（xs/ys 张冠李戴 ρ=0.063）②vicinity 细条层度中位 root 落层内中段 → 层内方差大、断言降为方向性（ρ>0.4 + distal>tumor 层均值）——marker 模式梯度恢复 ρ=0.907、NO_VICINITY/INVALID_INPUT 三路径；SECTION_TITLES 38 项；TDD 注册 5 用例；全量回归 **1272 passed**（-m 'not pg' 门禁口径，较 Phase 50 同口径 1267 恰 +5） |
| 2026-09-12 | **Phase 52 st_misty TF/collectri 调控子视图落地**（spec docs/superpowers/specs/2026-09-12-st-misty-tf-design.md）：Phase 50 非目标留位兑现——st_misty extra_mode 扩第三值 tf（hvg｜progeny｜tf），CollecTRI TF 活性（decoupler MLM，42990 边/1185 TFs，tmin=5 后 ~772 预测子）作 extra 视图，MISTy 三视图建模样式/产物结构一字不改；**断网纪律第三例**：st.Dockerfile 增 fetch_collectri.py 快照 /opt/collectri/collectri_human.tsv（零 pip 改动，decoupler Phase 50 已装，构建期快照模式 gene_pos→progeny→collectri 三例定型）；**探针两轮前置实测**（dc.op.collectri→42990 行 1185 TFs；断网 E2E 注入 DDIT3 100 靶基因 corr=0.9901 回收且高梯度端 top1、区分度 >14×；**MLM 秩约束坑**——overlap=100 时邻接矩阵秩<协变量数断言崩，overlap 阈值定 500、合成 fill 须用 4000 真实靶基因名）；规模应对=para 热图 top30 截断（772 列不可读，csv 全量+emit n_predictors_total/shown 双键）；错误码 ST_MISTY_NO_COLLECTRI/overlap<500 INVALID_INPUT；断网冒烟（DDIT3 weight×tumor 梯度注入回收 para top30、total=658 预测子、零交集对照复用假名数据集）；**SearchReplace 假成功三连**（DS_TF 常量/top_n 赋值/extra_mode 校验三处 diff 显示落盘而实际未落，全靠 Grep 读回验证+冒烟报错捕获）；全量回归遇 Temp pytest-of-Administrator 锁目录环境坑（PermissionError WinError 5 致 223 errors 虚惊，删目录+--basetemp=.pytest_tmp 解决，非代码问题）；TDD 注册 +2 用例（tf 透传/enum）；SECTION_TITLES 38 项不变；全量回归 **1274 passed**（-m 'not pg' 门禁口径，较 Phase 51 同口径 1272 恰 +2） |
| 2026-09-12 | **真实 Visium 全链验收收官**（挂账首项，驱动 scripts/_validate_st_real_chain.py）：OSCC_sample2 口腔鳞癌 Visium（1749 spots×15624 基因+病理金标准 pathologist_anno.x）+ HPV+ 肿瘤 sc 参考分层抽样 19149→1844 细胞，st_* 14 工具真实数据全链全绿（load→qc→process/banksy 8 domains→stats→markers→deconvolve→cnv 128 恶性→niche 12→vicinity→misty hvg/progeny/tf 三模式（683 TFs，872s 最贵单步）→trajectory）；**病理交叉对照通过**——CNV 恶性 spot 在病理 SCC 区富集 10.43% vs 2.59%（Fisher OR=4.372，p=7.06e-11）、Epithelial 丰度 SCC 区 1.604×（MWU p=0.0）、domains vs 病理 ARI=0.2227；dpt vs vicinity ρ=0.034 弱相关留改进挂账；**真机发现三件**：①st_load 对 .h5ad 文件路径 latent bug（宿主侧误走目录聚合 hash，18533d7 已修复+TDD）；②BioRunner subprocess.run(timeout) 超时只杀 docker CLI 客户端留僵尸容器抢 CPU（三次 deconvolve 超时实证，容器命名+超时 docker kill 改进挂账，deconvolve CPU 可行性参数化挂账——ref_epochs=250/num_samples=1000 硬编码、默认 30000 epochs 4 CPU 不可行）；③h5py 禁 obs 键含 "/"，真实标签 "Tem/Effector helper T cells" 致 deconvolve 训练 2h 后写出才炸——deconvolve.py `_clean_label` 进模型前两处赋值点统一净化（容器脚本改动须重建 st 镜像才生效），手动 docker 直跑即修复的 E2E 验收；工程沉淀：长任务手动 docker 直跑+日志轮询、驱动 `--start-from` 断点续跑 |
| 2026-09-12 | **三项挂账收官：BioRunner 僵尸容器修复 + deconvolve CPU 参数化 + cnvturbo 口径评估**：①bio_runner docker run 加 `--name bio-<uuid12>` + TimeoutExpired 兜底 `docker kill`（best-effort 不掩盖 SC_TIMEOUT，--rm 清尸），既有 gpus 测试改按值定位，TDD +2 用例；②deconvolve 三参数三层暴露（容器脚本/宿主 handler/schema）：ref_epochs=250/num_samples=1000 原硬编码可配 + ref_max_cells_per_type 分层限帽（groupby.head 确定性，类型全保留），emit 加 n_ref_cells，头注释落 CPU 可行性口径（默认 30000 epochs≈30h 仅 GPU 可行、CPU 实用 2000、万级参考必须限帽），新建 _smoke_st_deconvolve.py 小轮数 E2E（限帽 3×50=150 + 斜杠标签 T/NK→T_NK 净化双断言，spatial_scatter uns 占位壳一败后绿）；③cnvturbo 口径评估（bio_workspace/_eval 一次性脚本，复刻 cnv.py 预处理+断网容器直跑 <15min）：min_segments_for_tumor=1/2/3 判定**逐细胞完全一致**（肿瘤区段普遍 ≥3 旋钮不敏感），vs infercnvpy 基线 Jaccard 0.589/覆盖基线 96.8%/多判 4052 全落 Epithelial+Plasma+pDC 零误报——分歧源于细胞级 HMM vs 簇级投票判定逻辑非该参数，**维持口径=1** 结论钉入 cnv.py 注释（簇级平滑对齐列可选改进非 bug）；全量回归 **1278 passed**（-m 'not pg'，较验收行 1274 +4） |
| 2026-09-13 | **可选改进收官：trajectory/vicinity ρ 排查 + cnvturbo 簇级平滑**：①ρ=0.034 根因查明=**语义正交非 bug**（_eval/diag_traj_vicinity.py 诊断 JSON）——dpt 表达图推断（MWU 恶性 vs 其余 p=0.338 无差异、vs PC1 ρ=-0.9 组织组成轴主导、vs Epithelial ρ=-0.66），vicinity 层 cnv_score 梯度 0.00954→0.00161 递减 ρ=-0.306 p=3.7e-39 层结构有效，层内 dpt std 0.12-0.15 >> 层间均值差 0.02，|ρ| 低因层内表达异质性远超层间梯度，解读指南钉 trajectory.py 注释；②cluster_smooth 参数三层落地（cnv.py 平滑块与 infercnvpy 路同款 >0.5 多数投票语义 / handler+schema 透传 / 单测 payload 断言×2），真实 OSCC 效果评估（_eval/eval_cnv_smooth.py）12 簇翻转 379=2.0%、Jaccard 0.589→0.601、基线覆盖 96.8%→98.4%——温和修正收益边际，**默认关按需开**结论数字钉 cnv.py 注释；集成测试 +1（note 含"簇级多数投票平滑"+恶性集中度 ≥0.8）；SearchReplace 假成功再现 2 次（handler 签名/forwards_params 断言，Grep 读回二补）；全量回归 **1279 passed**（-m 'not pg'，较上段 1278 恰 +1） |
| 2026-09-13 | **真实重启终验：ws_client 连崩两天根因根治——yaml GBK 解码 + 启动期 stderr 可观测性**：用户重启后 guardian **lnk 自启成功**（09-12 09:43:45），但 ws_client 拉起即死循环超两天（机器人静默离线）；**控制变量四连排查**（手动 python✓/pythonw✓/CREATE_NO_WINDOW✓/终端 guardian✓）锁定差异=父进程环境——Trae 终端注入 PYTHONUTF8=1 而 explorer 自启 locale=GBK，`_load_yaml` 缺省 encoding 走 locale 读 420 非 ASCII 字节的 llm.yaml **UnicodeDecodeError**，traceback 随 CREATE_NO_WINDOW pythonw stderr 丢失静默死亡（ws_client.log 仅余 acquire_single_instance 一条 stale 日志恰在 load_settings 前）；历史闭环：09-11 连崩 189 次同源、"双 guardian 并存"系 venv redirector 双进程误判；**修复三件套**：_load_yaml 显式 utf-8（根治）+ guardian _restart stderr/stdout 落盘 ws_client_stderr.log（可观测性）+ PYTHONUTF8=1 注入（纵深防御）；TDD +2（spy open 断言 encoding / FakePopen 断言落盘+注入）；**干净环境 E2E**（剔除 PYTHONUTF8 模拟 explorer+CREATE_NO_WINDOW 拉起连飞书成功）代理终验通过，服务已恢复；全量回归 **1281 passed**（-m 'not pg'，较上段 1279 恰 +2） |
| 2026-09-13 | **Phase 46：st_stats 第四分析 centrality**（gr 层三件套补齐：autocorr/cooccurrence Phase 45 已有，真缺口=centrality；spec 2026-09-13-st-centrality-design.md 批准，并入合集否决独立工具）：容器探针先行核实 API 面（gr.centrality_scores → DataFrame 列 degree/average_clustering/closeness——clustering 列名反直觉；gr.interaction_matrix → ndarray (n,n)），stats.py `_do_centrality`（双 csv+degree 图+互作热图+top_hub/top_closeness 摘要）+ schema enum 四值，零新依赖零新参数；TDD 单测 +1、冒烟加两场景（interaction 对角主导 + tri 不对称三簇 top_hub="A" 鉴别力）；**SearchReplace 假成功三连**（头注释+函数体/enum 断言/tri 列定义，教训升级=批量编辑后逐文件逐处 Grep 验证）；真机验收真实 OSCC 1749 spots：spatial_domain top_hub=域 3（SCC 98%）、niche top_hub=N1（degree 0.655 双料枢纽，SCC 84%）——病理金标准确认肿瘤核心区=组织拓扑枢纽；全量回归 **1282 passed**（-m 'not pg'，较上段 1281 恰 +1） |
| 2026-09-13 | **pg 真库层补验**：pg marker 测试自建库以来从未真库验过（check.ps1 每轮提示"真库层未跑"），趁 pg 容器 healthy 跑 `pytest -m pg` → **6 passed** 一次全绿零修复；门禁基线维持 `-m "not pg"`（1282）不动，pg 容器可用时随时补验 |
| 2026-09-13 | **Phase 53：sc_pseudotime 增强——动态基因趋势 BEAM-lite + root_cluster 簇定根**（spec 2026-09-13-sc-trajectory-enhance-design.md 批准 c034e88；Phase 32 基座升级）：探针先行（statsmodels 现成/零方差 NaN 掩/BH 排外/19149×2000 Spearman 3.4s）；_dyn_genes=HVG∩raw 池 Spearman+BH → dyn_genes.csv+trend_heatmap（pt 排序×平滑 z-score+pt 色条）+trend_curves top6；root 三模式 marker/cluster（簇内邻居图度最高）/fallback+root_mode emit；互斥/簇不存在/缺 HVG → fail INVALID_INPUT；**真机验收险出大事：双场景 exit 137 OOM 零输出——probe_pt_rss 逐段打点定位逐基因 `adata.raw[:, g]` AnnData 切片每基因漏 13MB（500 基因 8.5GB），修复=整矩阵一次 tocsc+列索引字典，178s→34s 5.2×提速**；冒烟两败后绿（宿主无 leidenalg→人工三段分簇+X_umap 借 PCA；注入信号被 normalize_total 稀释 rho 0.25→替换式 amp60/48 背景 rho 0.90）；真机 19149 生物学自洽（root_cluster=2 top_dyn=TRAC/TRBC2/CD247 等 T 身份基因全负相关、root 簇 pt 均值全图谱最小）；全量回归 **1286 passed**（-m 'not pg'，较上段 1284 恰 +2） |
| 2026-09-13 | **Phase 47：sc_cellchat 增强——rank_aggregate 五方法共识 + 两组差异通讯**（spec 2026-09-13-sc-cellchat-enhance-design.md 批准 ae0be0c；方向校准：误判"sc 通讯空白"，Phase 34 已交付单方法，AskUserQuestion 用户选"都要，合并一轮"）：探针先行（合成 240 细胞踩 assert_covered 坑——资源 L/R 基因 var_names 缺失须 ≤0.98，背景并资源基因低表达；CCL5|CCR5 注入对检出 magnitude_rank 第 1 名；**19149×14 类全默认 n_perms=1000 实测 165s/4.2GB**，远低于 timeout 3600 无需上调，钉 cellchat.py 头注释）；cellchat.py 重构四函数（_prepare/_count_matrix/_infer_one 统一口径 lr_score 越大越强+sig_metric 越小越显著/_diff outer merge+delta_score+RdBu_r 红蓝热图）+ group_col 恰两取值硬约束，零 pip 变更零新依赖；修 bug 两枚（输入校验 5 处 ValueError→fail INVALID_INPUT 对齐 st 约定；merge 后缀吃 source/target 列致 KeyError，fillna 回填修复）；TDD +2 + 新建 **sc 侧首个冒烟** _smoke_sc_cellchat.py（宿主无 liana → 容器内建库 stdin 喂 builder，合成两群×两组注入 CCL5|CCR5，单组检出/差异 up_in=A/双非法输入三场景全绿）；真机验收 19149：单组 vs Phase 34 cellchat 交叉对照顶部 LR 高度一致（S100A8|CD69 双榜首、LR top30 交集 9、共识法更严 sig 1664→471 Jaccard 0.244 符合预期），group 差异 top_delta=NAMPT|INSR Classical monocytes→Endothelial up_in=A（delta 3.769，细胞类型有无差异主导，生物学合理）；全量回归 **1284 passed**（-m 'not pg'，较上段 1282 恰 +2） |
| 2026-09-13 | **建议清单三项收官：pytest 抖动根治 + 拟时序候选依赖探针 + 59900 鼠源图谱二轮**：①抖动取证链（ws_client 四文件 40 轮零失败排除 → 全量连跑第 5 轮复现留证 .flake_full_fail_5.log）锁定元凶=**测试侧对不存在文件调 Path.resolve()**——实现侧只 resolve 已存在的 ws_root 目录，断言侧 resolve 不存在完整路径触发 Windows 句柄解析非严格分支偶发保留 `\\?\` 扩展前缀（Expected `\\?\I:\...` vs Actual `I:\...` 参数误判，非竞态）；修复=两测试文件加 `_host_path` 与实现同式 `str(tmp_path.resolve()/rel)`（test_research_runner 4 处+test_report_digest 3 处，全库扫尾无残留），验证=**全量连跑 6 轮 × 1286 passed 零失败**；纪律沉淀"断言侧拼路径必须与实现同表达式，resolve 不存在文件禁入断言"；②依赖探针（_eval/probe_deps.py）：CytoTRACE2 拖 torch+gdown 且 numpy 二进制不兼容 **不推荐**、Palantir mellon broadcast_to import 失败**需 pin 矩阵二轮**、RNA velocity 两套图谱 layers 全空**数据前提不满足**；③59900 鼠源图谱（G1 30724/G2 29170，Ptprc 型符号）：sc_pseudotime 158s 绿（n_dyn=1542，top=Ptprc/Cd52/Dock2 泛免疫自洽），sc_cellchat 差异首跑资源全缺失反推鼠源 → species=mouse 重跑 2191s 绿（top_delta=Lgals1|Ptprc 37->21 Δ0.212 up_in=G1，G1 142212/G2 135682 显著对）；回归基线维持 **1286 passed**（纯测试侧修复无产品代码变更） |
| 2026-09-13 | **Palantir 二轮探针翻案 + 59900 pair 身份坐实**：①pin 矩阵（bio 镜像 py3.12.14+numpy 2.5.2，A=降级 numpy 1.26.4 vs B=镜像 numpy 不动+shim 预备，容器联网装 --target 实验库、断网跑 300 细胞合成全工作流）——**A/B 双绿 rho=0.9917，B plain 无 shim 直绿**：mellon 1.7.1 已修 broadcast_to import，上轮"需 pin"系宿主 Windows 旧版 mellon 解析假象（教训：依赖探针必须跑在目标运行时）；闭包 palantir 1.4.5+mellon+jax/jaxopt/ml_dtypes/igraph（~500MB 零 torch、零运行时下载）→ **判定可行零 pin 冲突，镜像层直加 palantir==1.4.5 可立独立 Phase**（sc_pseudotime 第三引擎）；②59900 top_delta 回查（_eval/cluster599_identity.py 断网容器）：**簇 37=浆细胞**（Igkc/Igha/Jchain 顶标、B 表面受体≈0、Lgals1 3.37 配体侧坐实、G1 131/G2 6 近缺失）、**簇 21=T/NK**（Nkg7/Il2rb/Skap1/Stat4 顶标、Ptprc 2.81 受体侧坐实）——Galectin-1→CD45 已知 T 免疫调节轴，up_in=G1 由浆细胞簇存在性差异驱动（与 19149 同模式）；回归基线维持 **1286 passed**（纯 _eval 探针无产品代码变更） |
| 2026-09-13 | **Phase 54：sc_pseudotime 第三引擎 Palantir**（spec 2026-09-13-sc-palantir-engine-design.md 批准 f5e5589；探针两轮钉注后落地）：集成循 cnv.py 双后端先例——sc_pseudotime 加 engine（dpt 默认/palantir）+start_cell（显式根条码、优先级最高、非法严格 fail）；bio.Dockerfile palantir==1.4.5 层（~500MB 闭包零编译）；pseudotime.py 重构定根四模式（root_mode 加 explicit）+_run_palantir（diffusion→multiscale→run_palantir waypoints=1200）三产物（palantir_pt.csv pt+ts_* 分支概率宽表/terminal_states.csv/palantir_umap.png 终末态黑叉）+_dyn_genes 加 prefix 防互踩+_cluster_stats 提取共用；TDD +2（透传+默认值零变化）；冒烟扩 9 场景（palantir rho=0.909/n_terminal=1/双定根模式/双非法拒）；**真机 19149 双引擎对照**（root_cluster=2 同款 T 簇，75.6s vs DPT 34s）：pt Spearman 0.654、根簇 pt 第 2 小（DPT 第 1 小一致）、终末态 2 个落远端簇（分支结构信号=DPT 给不出的增量）、dyn top10 重叠 6/10 全 T 身份基因；踩坑两枚（镜像先建于脚本变更前须重建再验；PS `>` 重定向 docker stdout 成 UTF-16，验收捕获改容器内 bash 重定向+emit 取末行）；全量回归 **1288 passed**（较上段 1286 恰 +2） |
| 2026-09-13 | **59900 palantir 交叉验证 + 分支概率可视化落地**（spec 增补 2026-09-13-sc-palantir-branch-viz-design.md 批准 063e3a5）：①59900 鼠源图谱 palantir 复跑（fallback 同款定根，186.5s vs DPT 158s）——pt Spearman **0.807**（19149 为 0.654）、dyn top10 重叠 **9/10** 全泛免疫基因（19149 为 6/10）、终末态 3 个含 leiden 21（即 cellchat pair T/NK 簇，pt=0.846），双图谱交叉一致性达标；②分支概率可视化方案 A UMAP 分面图：_run_palantir 第四产物 palantir_branch_umap.png（每终末态一 panel 封顶 6、0-1 固定色阶跨 panel 可比、终末态黑叉+根红圈），emit 加 branch_umap_png 零新参数；冒烟追加断言全绿+19149 复跑 82.4s 出图目检（双终末态命运分离清晰、过渡群体概率渐变可读）；回归基线维持 **1288 passed** |
| 2026-09-13 | **分支推断方案 A：Palantir BEAM-lite 命运决定基因**（spec 2026-09-13-sc-branch-inference-design.md 批准 741f84d，用户"按方案 A 执行"；RNA velocity 评估挂账——两套图谱 layers 全空数据前提不满足，用户明确"先不做"）：sc_pseudotime 加 branch_top_n（默认 0 跳过零回归变化，dpt+branch>0 INVALID_INPUT）——三步零新依赖：①argmax(branch_probs) 归属（max_prob<0.6→unassigned 过渡态不强行站队）；②分支内（≥30 细胞）Spearman+BH 动态基因（_dyn_stats 提取供 _dyn_genes/分支分析共用零行为变化）；③两两分支 pt 排序 20 等量桶、桶中位数差 score=median(|Δ|)、配对 wilcoxon(n=20)+BH → 命运决定基因；四产物 palantir_branch_assign/dyn/de.csv+branch_trend.png（显著最多那对双面板共享 ±2）；TDD +2（透传+默认 0）；冒烟扩 11 场景（独立双分支合成库 trunk100+A/B 各 100：归属率 0.64、DE top 命中 G_fateA/G_fateB 且 higher_in 方向正确、dpt 拒）；**真机 19149（root_cluster=2，92.6s）：归属率 67.4%、DE 显著 114 基因=PDAC classical（MUC5AC/AGR3/FCGBP）vs basal（S100A2/SERPINB3/KRT5/KRT13）两大分子亚型命运分离——生物学强验证**；重构事故两枚沉淀（凭失真 Read 写 old_str 致 chimera→git checkout 恢复+编辑前重读真实文件；同文件 SearchReplace 并行竞态丢编辑→严格串行）；全量回归 **1290 passed**（-m 'not pg'，较上段 1288 恰 +2） |
| 2026-09-13 | **分支归属写回 obs + 59900 三分支交叉验证**（用户"好的，执行吧"批准后续建议①+②）：①_branch_analysis 归属写 adata.obs["palantir_branch"]（Categorical）+ main 统一落盘 processed.h5ad（st_trajectory dpt 写回惯例），下游 sc_plot 可按分支着色，冒烟场景⑩追加重读断言；②59900 鼠源图谱 branch_top_n=50 复跑（3m41s vs 无分支 186.5s）：终末态 3 个与上轮逐字一致（复跑稳定）、归属率 33.2%（0.6 阈值下早期祖细胞簇近全 unassigned，与 19149 的 67.4% 差异=图谱过渡群体占比）、**三分支两两 DE 路径真机首验**（3 对 264/350/296 显著+branch_note 如 spec）、生物学=**T/NK（Cd3g/Ifngr1）vs B 谱系（Igkc/Ebf1/Bank1/Bach2）vs 髓系（Lyz2/Fcer1g/Tyrobp/Il1b）三大免疫谱系命运分离**，leiden 21 分支 T/NK 身份与上轮 cellchat pair 坐实互洽；回归基线维持 **1290 passed** |
| 2026-09-13 | **动态基因趋势聚类 + sc_cellfreq 命运偏向**（spec 2026-09-13-sc-dyn-modules-fatebias-design.md，对分支推断产物的挖掘升级两件，镜像零 pip 改动）：①sc_pseudotime 增 dyn_modules_k/modules_enrich——sig 动态基因全量平滑 z-score KMeans 聚类为早→晚表达程序模块（z>1 加权 pt 排序 M1..Mk）+ 可选逐模块 gp.enrich 离线 ORA（enrichment.GS_KEYS 复用、鼠源库 upper 对齐）；②sc_cellfreq 零新参数 Fisher+Ro/e 增强（group 恰 2 值时 fisher_q(BH)+Ro/e 两列+roe 热图+fate_bias top5，>2 值降级 note），celltype_col="palantir_branch" 即命运偏向。冒烟 pseudotime 13 场景+新 cellfreq 2 场景全绿；真机三跑：19149 六模块程序波（T/NK→炎症→表皮分化→血管→B→ECM，go_bp 富集连贯，4m14s）、59900 命运偏向 **G1 谱系定型/G2 祖细胞滞留**（三分支 OR 1.5-4.8 全显著，18.8s）、59900 六模块 kegg_mouse 三谱系程序与三分支逐一互洽（6m44s）；事故 1 枚（peak_pt 2D 权重矩阵乘→标量假设须宿主最小复现）；回归 **1292 passed**（1290+2） |
| 2026-09-13 | **R/Python 轨迹工具双探针**（用户"依次做了"批准，纯 _eval 探针零产品代码变更）：①Slingshot（R）四轮攻坚全绿——失败三连沉淀（BiocManager 版本映射静默失败仅装自身；清华/USTC 镜像仅托管当前 3.23 分支 3.21 已 404，R 4.5 配对仓唯官方 bioconductor.org/3.21；GenomeInfoDbData 在独立 data/annotation 仓）+ 预装 igraph.so 残废（ldd 全量列出缺 libxml2/libglpk40 一次性 apt 补齐，教训=动态库缺失勿逐轮补）→ 终绿配方 CRAN 清华+Bioc 官方 3.21 双仓+annotation 仓，INSTALL_MIN=3.4/CLOSURE_N=74/LIB_DELTA_MB=67/**LOAD_OK=2.16.0**，输入仅需降维坐标+簇标签 → **判定可立 Phase（成本中：镜像 +5min/+67MB）**；②CytoTRACE2 一轮死刑——包名 cytotrace2-py（导入名 cytotrace2_py）、numpy<2 与镜像锁 2.5.2 冲突、torch 闭包 1.2GB+nvidia-cu13（装后 site-packages 7132MB）、**生死项=17 个模型权重 gen_utils.py 硬编码 Google Drive URL gdown 运行期下载**（断网验证容器必死、GDrive 国内不可达、无合规替代源）→ **判定永久挂账**（潜能打分已由 palantir entropy 覆盖）；回归基线维持 **1292 passed** |
| 2026-09-13 | **Phase 55：sc_pseudotime 第四引擎 Slingshot**（spec 2026-09-13-sc-slingshot-engine-design.md 批准 cf8b59f；探针四轮钉注后落地）：集成循 palantir/knockout 双先例——engine 三引擎（dpt 默认/palantir/slingshot）+ start_cell 双引擎合法 + branch_top_n 维持 palantir 专属；pseudotime.py `_run_slingshot`（X_umap+leiden CSV 桥 → Rscript slingshot_bridge.R 簇级 MST getLineages+主曲线 getCurves → sling_pst 行均值主 pt 写回 obs 统一落盘；产物 slingshot_pt/curves.csv+umap.png 曲线叠加根红圈；dyn 相 slingshot_ 前缀复用）+ bio.Dockerfile 两 R 层（探针配方三源 74 包 + DelayedMatrixStats/sparseMatrixStats Suggests 级运行期增补——library 自检不触发、计算路径必需，构建自检绿≠运行绿）；**事故四枚**：并行 SearchReplace 竞态丢编辑×2（纪律=同文件编辑严格串行+批量后逐文件 Grep 验证）、镜像先于脚本变更构建（Phase 54 同款）、数字条码 csv 往返 int64 错位 reindex 全 NaN（astype(str)）；冒烟 15 场景绿（slingshot 双谱系 rho=0.968）；真机 19149（1m59s，12 谱系 root #16178 与 palantir 逐字一致，top_dyn 全 T 身份）+59900（10m19s，髓系负/基质正），交叉 19149 spearman(sl,pal)=**0.8934**、59900=-0.6443（自由推根方向任意钉注）；全量回归 **1294 passed**（-m 'not pg'，较上段 1292 恰 +2） |
| 2026-09-14 | **Phase 56：谱系×分支交叉聚合 + PAGA 相**（spec 2026-09-13-sc-pseudotime-cross-paga-design.md）：①slingshot 谱系 × palantir 命运支交叉双向触发（argmax 列联+主导支 frac≥0.5/mixed+Fisher+BH+Ro/e → cross csv/png+lineage_branch_map）；②paga=1 簇级连接度图+paga_pt=1 PAGA-initialized DPT（端点簇 min-pt 定根，scanpy 无 paga_paths 校准钉注；唯一互斥 paga_pt 需 paga）；TDD +3（l3 76 passed）、冒烟 15→18 场景绿；真机三跑：19149 palantir 反向 cross 12 谱系×2 支 sig=11/12、19149 slingshot 正向+paga 46 边、59900 全相 153 边+端点根 31+mixed 支降级正确；spearman 对照 0.6483/-0.2453（双任意根方向钉注）；全量回归 **1297 passed**（1294+3 恰） |
| 2026-09-14 | **59900 方向锚定复比 + Phase 57 交叉产物 obs 写回**（spec 2026-09-14-sc-cross-obs-writeback-design.md 批准 a2fb81d）：①定根簇 31=Flt3+ 祖细胞样小簇（0.975 vs 0.026），pal/sl 双跑锚定后 spearman 全正（pal-sl 0.7183 / pal-paga 0.8075 / sl-paga 0.5785，自由推根翻转彻底解除；归属率 33.2%→98.5%）；②slingshot_lineage（argmax 归属）+ lineage_branch（谱系→主导支物化）两列写回 obs，palantir 支落盘条件补 cross 触发；冒烟 18 场景断言扩写绿；真机 19149 十二谱系全覆盖 UMAP 目检绿、59900 sc_cellfreq celltype_col=lineage_branch 即插即用三支偏向全显著（G1 稀有支 OR 3.46）；发现 sc_plot 缺 umap_obs 列后续候选；全量回归 **1297 passed** 维持 |
| 2026-09-14 | **Phase 58：sc_plot umap_obs + lineage_branch × sc_cellchat 联用**（spec 2026-09-14-sc-plot-umap-obs-cellchat-cross-design.md）：sc_plot kind 扩 umap_obs（obs_cols ≤6 逐列校验 INVALID_INPUT，类别/连续自适应）+ l3 透传/schema enum；TDD +2（l3 78 passed）、冒烟扩场景⑲ 19 场景绿（镜像 b9d07226）；真机 19149 官方工具 12 谱系 UMAP 目检绿；cellchat 纯评估零代码——59900 鼠源 35m17s/596sig（H2-K1|Cd3g+Lgals1|Ptprc 复现）、19149 人源 13m15s/566sig（HLA-B|CD3D），**跨物种 MHC-I→CD3 轴互洽**命运支级通讯谱落地；门禁小插曲（check.ps1 单次抖动复跑 PASS）；全量回归 **1299 passed**（1297+2 恰） |
| 2026-09-14 | **Phase 59：trajectory_full 全景模式 + meta list_cols + 门禁抖动根治**（spec 2026-09-14-sc-trajectory-full-meta-discovery-design.md；用户"把1-6都做了"六项收官）：①sc_pseudotime trajectory_full（忽略 engine，同一 iroot 锚定串跑 palantir 分支推断→slingshot 谱系→cross 自动（triggered_by="trajectory_full"）→paga 跟随，单次落盘+嵌套 emit）——五次 Phase 链式调用收敛为一键全景；②sc_meta 只读 op list_cols（group_cols/trajectory_cols 发现，消除 agent 选列盲区）；③check.ps1 取证+**抖动根治**（PS5.1 Stop 偏好下守护线程 stderr 杂散行经管道抛 NativeCommandError——改 cmd /c 文件重定向+UTF-8 BOM 修复）；TDD +2（l3 80 passed）、冒烟 20 场景绿（镜像 f1154af4，DS2 root_cluster="trunk" 自由推根同族教训）；真机四线：19149 全景与分步链产物逐字一致（12 谱系 cross sig=11）、19149 spearman 矩阵全正（0.8934/0.6539/0.6483）、59900 六模块×三支 KW 全显著（M4 H=11443 支专属/M6 单支富集 2.935）、list_cols 双图谱绿；①叙事报告 _eval/trajectory_narrative_2026-09-14.md（跨物种 MHC-I→CD3 轴整合）；全量回归 **1301 passed**（1299+2 恰） |
| 2026-09-15 | **Phase 60 六项建议收官**（8cd375e）：①report 产物收集 _harvest 递归改造（嵌套 dict 前缀键/pngs 列表/.png/.csv 通用判定，12 单测）；②bio_trajectory_pipeline skill 三件套（SKILL.md/tools.yaml/run_pipeline.py 五步编排，docker 调镜像内 sc_tools 与 L3 同一份代码，19149 fast 冒烟 12 谱系 sig=11 一致）；③59900 条件命运概率（G1/G2 三分支×多基因 MW-U+BH 15/15 显著+Cliff's delta，batch×group Cramér's V=1.0 完全共线禁因果措辞钉注）；④19149 克隆程序（basal 亚型 rho=-0.809，C15=T 细胞污染克隆钉注——后续护栏转化来源）；⑤velocity 前置校验（load 层 _velocity_diag：spliced/unspliced 层存在性+非零基因覆盖，10x 总计数矩阵天然缺失）；⑥cellchat 资源分级护栏（>80000 细胞自动 max_cells_per_group=5000+auto_capped 钉注） |
| 2026-09-15 | **沙箱 .git 拦截定案与修复**：TRAE 内置文件策略 .git 只读+进程级盯防 git.exe 写行为（GIT_OBJECT_DIRECTORY 旁路仍拒）；修复=手工创建 `%USERPROFILE%\.trae-cn\sandbox.json` readWrite 放行 `$WORKSPACE_FOLDER/.git`（配置仅会话启动加载不热重载）；期间手工构造 git 对象路线两度兜底（8cd375e/bf500eb），七坑沉淀（CRLF→LF、树 mode 五位、[Array]::Copy、传播层根级键规约、根级 blobMap 分支、mode 归一、UTF-8 BOM），路线封存为应急备份 |
| 2026-09-16 | **新六项前二收官**（用户"按建议顺序来"）：①report 端到端真机验证——FakeLLM 记录型+doc_adapter=None 走 md_only 不碰真飞书：19149 真实 emit 10 图 11 csv 进 md、LLM 恰 2 调用（MAX_LLM_CALLS=12 内）、to_blocks 15 块含 10 ImageBlock 全绿；②cnv_subclone 纯度护栏（409b4e4）——_purge_immune_clones（T/B marker 绝对>0.5 且>max(5×基线, 基线+0.5) 判 suspect 回退 non-malignant），19149 真机命中 C10（immune_score 0.903 vs 恶性基线 -0.234，n=389 回退）n_malignant=10288 三口径自洽，purity_check 参数三层透传默认 True（免疫恶性肿瘤数据集须显式关）；**容器 git 提交路线开辟**（alpine/git 镜像 add/commit+宿主 push，较手工构造轻量一个量级）；③trajectory skill full 模式真机验收（异步运行中）④59900 供体级条件检验（pseudobulk）⑤19149 克隆进化时间轴 待做 |
| 2026-09-17 | **Phase 61：cellchat v2 集成**（第五十五段探针 r9 终判后落地；会话代号"Phase 57"撞号改记 61）：bio 镜像 GitHub 源装 CellChat 2.2.0.9001（apt 16 包+CRAN 清华/Bioc 官方双仓，2.66→3.49GB +830MB，DB 离线内置 human 3233/mouse 3379）——v2.2 断代八坑镜像内源码钉注（computeCommunProbPathway 改名/@LR$LRsig/@netP$centr 移位/subsetData features/矩阵输入不填 @data.raw 且 raw.use=FALSE 路径坏/presto 缺 do.fast=FALSE/spatial.factors 契约 ratio+tol/contact.knn.k=6）；桥 cellchat2_bridge.R（sc/st 共用双模式 mtx+CSV）+ cellchat_v2.py（模式自动判/数字标签 C 前缀还原/µm 折算/三图）；sc_cellchat_v2（l3_singlecell）+ st_cellchat_v2（l3_spatial 跨镜像分发 image=bio、R 栈单点安装 st 镜像零增重）+ section_digest 两映射 + 单测 +4；门禁 ruff/mypy 双平台/pytest 1316 全绿；**59900 真机三轮**（r1 leiden 数字标签拒收→C 前缀；r2 payload species 误写 human vs 鼠源基因符号→DB 交集<3，顺手 @data.raw as.matrix 11GiB 稠密化改稀疏直赋；r3 全绿 59900×38 类 n_lr=99851/通路 41506/user 25m34s），生物学对照 H2-K1|CD8B1（MHC-I→T 轴）复现、COL1A1 CAF 轴榜首 |
| 2026-09-17 | **遗留三项收官（执行面统一/记忆库/report sent 复验）**：①执行面统一（spec 2026-09-17-execution-plane-unification）——st 侧 7 工具 handler 缺 timeout_sec 真 bug（容器回退 900s 提前杀，process/commot 声明 1800）补齐 + 全工具分发合同测试（FakeRunner 按 schema 自动合成参数断言 run timeout==spec，41 工具零改动入列）+ skill run_pipeline 镜像 env 化+--name+kill 兜底；②数据集口径记忆库（Phase 61 教训）三层防线——画像层 detect_symbol_style（Title-case=mouse/全大写=human/ENSG）+obs 列+`_profiles/<ref>.json` 持久化（下划线前缀免疫 GC）注入 planner；handler 层六 species 工具默认空串=自动（记忆库猜测→human 兜底，显式传值透传）；容器层 species_style_guard 毫秒级快败 SC/ST_SPECIES_MISMATCH（对照 Phase 61：7 分钟白烧→毫秒自愈），bio/st 镜像 rebuild+三连冒烟；③report sent 真机复验（2026-09-11 后首次）：真 SDK 通道 12 ImageBlock 量级一次全绿（create 1.6s/render 30.9s≈36 API/grant 0.65s，doc WO0Bdjs66ogwj6xjmvRccTW7ntr），收尾增量 ~33s 墙钟无挤压；门禁 ruff/mypy 双平台/pytest **1326 passed**；顺带发现 50165/b7a44 真机目录已过 TTL 被 GC（结论已落档非事故） |
| 2026-09-17 | **执行面统一 spec 挂账三项收官**：①收尾墙钟挤压验证降级关闭（sent 复验数字回写：12 图 ≈33s 占 3600s 墙钟 0.9%，重评触发条件钉档：图量≥60/render>120s/单步顶满）；②双通道超时口径表落地 spec 附录 A（L3 40 工具四档 600×12/1200×8/1800×11/3600×9 + skill 每步 3900 + research 墙钟 3600 + BioRunner 兜底 900），**表守护测试**三方比对（registry 动态/skill 源码常量 STEP_TIMEOUT_SEC/settings 类默认 vs md 表，漂移即红——"活文档"模式首例），skill 通道 cpus/memory 硬编码顺带 env 化（BIO_CPUS/BIO_MEMORY 与 settings 同源）；③工具级资源覆写（ToolSpec 增 cpus/memory + BioRunner.run 透传 docker --cpus/--memory + 合同测试"声明必透传"第三断言 + 单测 2），首批声明 sc_cellchat_v2/st_cellchat_v2 memory=32g（Phase 61 59900 峰值依据）、sc_scenic cpus=8+memory=32g（宿主实测 64GB/24 核，docker 55GiB）；插曲：表守护初版正则漏数字首跑自证（_v2 工具名抓出）、沙箱拦 C 盘 Temp→--basetemp 项目内；门禁 ruff/mypy 双平台/pytest **1329 passed**（1326+3 恰） |
| 2026-09-17 | **32g 覆写真机首验 + CI 镜像冒烟落地**（report 链接用户确认，sent 复验开口关闭）：①sc_cellchat_v2 生产链路真机回归（_eval/run_cc2_registry.py 按 app.py 装配 ToolRegistry 直调 handler，数据集 50165a559c91_bbknn 59900 BBKNN 全集/leiden 24 类/mouse）——运行中 docker inspect 实证 Memory=34359738368（32g）/NanoCpus=4e9/Network=none 落容器，stats 峰值 **15.07GiB 越 16g 旧默认线**（32g 价值实证），wall **28m17s**（较 r3 快 11%）n_lr=44815/通路 17922 产物 6 CSV+3 PNG 全落盘；②ci.yml 双 job——docker-smoke 每次 push 构建 kernel 镜像激活集成四用例（本机 4 passed/9.7s），bio-image-smoke 仅 workflow_dispatch（R/CellChat 编译链数十分钟级不进 PR 门禁，PROXY build-arg 置空 CI 直连）；③_smoke_sc_cellchat.py WS/IMG env 化（BIO_WORKSPACE_ROOT/BIO_IMAGE 与 settings 同口径）本机自定义 workspace 实证 SMOKE OK |
| 2026-09-17 | **st 镜像冒烟补齐 + 冒烟脚本全量 env 化**（用户"scenic 先不管其它继续"）：①9 个 _smoke 脚本 WS/IMG env 化（st 七件走 BIO_ST_IMAGE、sc 两件走 BIO_IMAGE、WS 统一 BIO_WORKSPACE_ROOT，与 settings 覆写同口径——硬编码漂移面清零）；②ci.yml 增 st-image-smoke manual job（st.Dockerfile 检索实证无 ARG/PROXY/GitHub 源，CI 直连零代理处理；冒烟选最轻 st_stats 合成网格三分析）；③本机实证 st_stats 自定义 workspace SMOKE OK（G_grad Moran's I 0.541 居首）；两 manual job 首跑待用户 Actions 页面触发（本机无 gh CLI） |
| 2026-09-17 | **ws_client 守护线程收口**（第五十八段插曲③根治）：根因订正——会后杂散 traceback 非未捕获异常，而是 swallows_exception 用例的 mock side_effect 线程 0.01s 永动循环 logger.exception 刷日志洪峰；修复=五个 start_* 守护线程统一协作停机（threading.Event + stop.wait 替代 while True: time.sleep，语义不变）+ 模块级 _BG_THREADS 登记 + stop_background_threads()，两测试文件 autouse fixture 会后停线程 + 新增停机钉死用例（计数冻结+线程退出）；门禁 ruff/mypy 双平台绿、pytest **1330 passed**（1329+1 恰），输出尾部零杂散残留 |
| 2026-09-17 | **gh CLI 打通 + CI 全绿闭环**：gh 便携 zip 路线安装（winget 直连被墙→clash 代理下载，MSI 1603 沙箱拦截→zip 解压+用户 PATH）；认证走 GH_TOKEN env（git credential fill 管道直喂不落盘，auth login --with-token 因缺 read:org scope 被拒）；插曲=`gh workflow run` 文件名大小写敏感 404（CI.yml→ci.yml）；**曝光 CI 隐藏失败**——scenic 合同测试依赖 repo 根真实 DB 目录在本机侥幸绿、CI SC_CONFIG 红（环境泄漏典型），fixture 改 tmp_path 空目录显式传 bio_scenic_db_root 后 push 跑全绿；**两 manual job 首验全绿**：st-image-smoke 11m18s、bio-image-smoke ~50min（CellChat 层 PROXY= 置空 CI 直连实证） |
| 2026-09-17 | **monocle3 重启评估探针终判：可行，搁置判据推翻**：r1 sf 链 8.2min 绿+BPCells 绿、死于 Cairo（镜像 purge 掉 libcairo2-dev）；r2 补全 r1-r9 dev 链一轮全绿——**MONOCLE3_OK 1.4.27 / 21.1min / +56 包**（sf 仅 8.1min，"sf 重栈"搁置理由在 8 核+已铺方法论下不成立）；落地形态=Dockerfile 增一层（apt 19 包+sf+install_github+purge 显式清单），CI bio-image-smoke 预估 50→71min（manual job 可接受）；边际价值=learn_graph 分支树+graph_test（现有 PAGA/DPT/slingshot 之外），建议独立 sc_monocle3 工具+monocle3_bridge.R（slingshot 同款模式） |
| 2026-09-17 | **Phase 62：sc_monocle3 集成落地——sc_pseudotime 第四引擎**（评估终判后用户"做吧"立 Phase；形态偏离第六十段建议：独立工具→engine="monocle3" 第四引擎，iroot/start_cell/paga 跟随/obs 写回/dyn 模块全复用）：bio.Dockerfile 增 monocle3 层（apt 19 包+sf 清华 CRAN+install_github 走 PROXY+purge 显式清单，本机 ~31min，1.4.27）；monocle3_bridge.R（PCA 前 50 维当表达建 cds→灌 reducedDims→cluster_cells→learn_graph→order_cells→导出 pt+MST 折点，Python 侧画线免 sf 绘图栈）；冒烟排障五坑（COPY 层时序/Size_Factor rep 回收/pData 单列 connect_tips 丢维度补列/principal_graph[["UMAP"]] 本身即 igraph/aux$dp_mst_coords 1.4.27 已改名 dp_mst——容器内 str() 探针钉死+vidx 双路防御）；_smoke_sc_monocle3.py 绿（edges=23/rho=0.878/na=0/dyn 含 fateA·B/explicit root/branch_top_n 拒收）；门禁 ruff+mypy 193 文件+pytest **1336 passed**（Temp ACL 损坏 --basetemp 绕行挂账）；**同日真机对照补记**：59900 三引擎同根（簇 16=Kit+Cd34+Hlf 打分断层第一）串跑——palantir 135.6s/slingshot 458.6s/monocle3 381.1s，sl~mono rho=**0.8394**、pal 对两图引擎仅 0.15（bbknn 口径钉注）；**捕获 palantir stdout 污染真 bug**（1.4.5 unconditional print 在 n>1200 waypoints 触发，生产链路必挂 SC_OUTPUT_INVALID，冒烟末行容错口径掩盖）——redirect_stdout→stderr 修复+冒烟场景㉑ 1300 细胞严格口径防回归；**slingshot 全集锚定失效钉档**（参数链无罪、1000 子集正常、59900 谱系起点全落簇 8，挂账待查，全集暂以 monocle3 替代）；monocle3 根锚定完美（root-cluster median=0=全局 min）；**同日 graph_test 增量**：真表达 mtx 可选通道（mmwrite cells×genes+genes.csv，graph_top_n 默认 0 零开销）→ bridge readMM 真表达重建 cds → graph_test 沿主图基因级 Moran's I（q_value 升序 top N + n_graph_sig）；排障 csr 双轨/genes.csv 无表头被 header=TRUE 吞首行；冒烟④⑤绿（sig=10 三注入基因全命中+他引擎拒收）；门禁 ruff/mypy/pytest **1336 passed** 维持；**同日冒烟口径系统性收口**：12 脚本 13 处 helper 末行容错解析统一改 BioRunner 严格口径（整体 json.loads），全量复跑 11 冒烟 5 组并行全绿——除 palantir（已修）外零 stdout 杂散，"冒烟绿≠生产绿"双口径掩盖连根拔 |
| 2026-09-17 | **Phase 62 收官四任务**（用户全选+新纪律）：①project_rules.md 钉大库纪律——59900 量级一律 subset ~1000 固定种子（参考 pt1ksmoke seed=7）；②graph_test 真机验证（pt1ksmoke 1000）：25.5s / n_graph_sig=**201** / top 基因 Lyve1·Gpihbp1·Cldn5 等真实干祖内皮相关；③**slingshot 锚定失效翻案=非 bug**：10k 子集复现后 R 探针（PseudotimeOrdering@metadata$mst igraph，写文件挂载规避 PS 转义）实证 start.clus="16"/start.given=TRUE/10 谱系 path 全以 16 开头——真根因=验证口径缺陷（nsmallest 簇构成假设根簇独占 pst 最小段），簇 16(83)与簇 8(804)在 UMAP 重叠带+pst 零点落重叠带+簇大小 10 倍悬殊，1k 密度不足故"正常"；生产链无需改码，正确 sanity 口径=metadata$lineages 路径起点；④Temp ACL/st_cellchat_v2 32g 双受阻钉档（无权限/无 st 大库）；⑤CI 闭环 run 35210556186 三 job 绿、bio-image-smoke（monocle3 层直连构建实证）进行中，gh 凭据丢失改 git credential fill 直调 REST 轮询 |
