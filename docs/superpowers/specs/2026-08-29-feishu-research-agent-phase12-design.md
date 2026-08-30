# Phase 12 设计：科研执行主链路激活（T1 通路 MVP）

- 日期：2026-08-29
- 状态：设计已确认（触发方式/范围两项用户拍板）
- 前置：Phase 11 已收官（587 passed + ruff 0 error；评论闭环/群聊门控/全量提醒开关全部真机验证通过，spec §14.4 三项遗留全清）

## 1. 范围与目标

### 1.1 Phase 12 主题

Phase 2-4 投入的科研执行引擎（Planner → DAG → Scheduler → Executor → 工具）代码存在但**从未接入生产**：`process_phase2/3` 无任何调用点、生产构造路径因环境变量缺失从不初始化引擎、LLMRouter 的 tools 参数被忽略、L1 工具全是 stub。本 Phase 打通最小真实通路：**用户发 `/research <任务>` → LLM 规划 DAG → 真实工具执行（读文档/LLM 摘要/BLAST 检索）→ 结果写回文档 + IM 反馈**。

### 1.2 范围（6 板块）

| # | 板块 | 交付 |
|---|------|------|
| ① | /research 指令路由 | normalizer/process() 识别 `/research <任务描述>`，无参提示用法，群聊可用 |
| ② | 引擎生产初始化 | 放宽 `Orchestrator.__init__` 条件：adapter 缺失时跳过对应工具注册而非整块不初始化 |
| ③ | Planner 工具 schema 修复 | tools_schema 内联进 DAG prompt（现只有工具名，schema 信息丢失） |
| ④ | L1 工具真实现 | summarize_text/classify_intent 接真 LLM；run_python/run_blast（stub）移出 DAG 可见集 |
| ⑤ | 执行链路接线 + IM 反馈 | /research → 后台线程跑 process_phase2 → 受理即回 + 完成回结果摘要 |
| ⑥ | 配置与文档 | settings 装配补齐 Phase 2 可调项、.env.example 补段、联调指南更新 |

### 1.3 关键约束

- 单机单进程、Windows10 + Docker Desktop（daemon 25.0.3 已验证可用；但 T1 不依赖容器）
- ws 回调线程不得被长任务阻塞——研究任务必须放后台线程
- 既有 587 默认层测试零回归
- L1 执行不引沙箱（真实 run_python 是 T2/Phase 13 范畴）

### 1.4 关键决策（用户已批）

| 决策点 | 选择 |
|--------|------|
| 触发方式 | 显式 `/research` 指令（LLM 自动意图分流推迟） |
| 范围 | T1 通路 MVP（沙箱执行 T2、branch/while/for 控制流 T3 留 Phase 13） |

### 1.5 不做清单

- Docker kernel 镜像构建与 run_python 容器内真实执行（T2）
- Scheduler 消费 branch/while/for/subplan 节点语义（T3）
- L2 审批卡片真实化（request_sync stub 顺延；DAG schema 本就不含 L2 工具）
- LLM 自动意图分类（chat/research 分流自动化）
- LLMRouter 请求体级 function calling（T1 用 prompt 内联 schema 即可，与 Planner 现有「LLM 返回 DAG JSON 文本」解析模式兼容）

## 2. 总体架构

```
用户（私聊/群聊）
  │  /research 总结绑定文档并给研究建议
  ▼
run_im_pipeline（gateway/app.py，不变）
  └─ process()（orchestrator/app.py）
      ├─ ①~1.6 既有指令路由（bind-doc / market 12 条 / 群聊门控）——不变
      ├─ 1.65 新增：/research 分支 ──────────────┐
      └─ 2. 普通消息闲聊路径（不变）              │
                                                 ▼
                              ┌────────────────────────────────┐
                              │ ResearchRunner（新，后台线程）    │
                              │ ① im.reply「已受理，规划中…」     │
                              │ ② session/task(intent=research) │
                              │ ③ Planner.plan()                │
                              │    llm_call_A: intent 解析       │
                              │    llm_call_B: DAG JSON（含      │
                              │    工具 schema 内联 ③）           │
                              │ ④ Scheduler.run_until_done()    │
                              │    → LocalExecutor(线程池)       │
                              │      → ToolHandler               │
                              │        ├ read_doc  (L0 真实)     │
                              │        ├ summarize_text (L1 真LLM)│
                              │        ├ classify_intent(L1 真LLM)│
                              │        └ blast_search (L0 真实)   │
                              │ ⑤ TemplateEngine.render_plan_    │
                              │    summary → doc_adapter 写文档   │
                              │ ⑥ im.reply 结果文本摘要           │
                              └────────────────────────────────┘
```

## 3. 板块① /research 指令路由

### 3.1 设计

- **识别位置**：`process()` 内，插在 1.6 `_try_market_commands` 之后、1.7 群聊门控之前（`/` 开头，群聊天然放行，群内可发起研究任务）
- **语法**：`/research <任务描述>`（一行）；`/research` 无参数 → 回用法提示，`status=research_usage`
- **不做** normalizer 级解析（无锚点/链接参数，纯前缀 + 正文，process() 内 `text.strip()` 判断即可，与 `/bind-doc-renew` 同级简单度）

### 3.2 群聊语义

群内 `/research` 允许（指令语义）；结果 IM 回复发起群（chat_id 即群 id，`im.reply` 天然支持）。

## 4. 板块② 引擎生产初始化

### 4.1 现状与问题

`app.py L72-73` 初始化条件：`settings + doc_adapter + base_adapter + drive_adapter` 四者全非 None。生产 `.env` 未配 `FEISHU_BASE_APP_TOKEN`/`FEISHU_DRIVE_PARENT_TOKEN` → base/drive 为 None → planner/registry/executor/template 整块不创建，`process_phase2` 一调就抛错。

### 4.2 方案

条件放宽为 `settings is not None and doc_adapter is not None`（这两项生产恒有）；base/drive 为 None 时**跳过对应工具注册**：

- `register_l0_read(doc_adapter=…, base_adapter=None, drive_adapter=None)` → 只注册 read_doc，跳过 read_base/list_drive（注册函数增加 None 守卫）
- `register_l2_side_effect` 同理：doc/im 恒有，base/drive 缺失时跳过 write_base_projection/upload_drive
- 未注册工具 Planner 自然不可见（schema 由 registry.list() 生成），无悬空引用风险

## 5. 板块③ Planner 工具 schema 内联

### 5.1 现状与问题

`Planner.plan(tools_schema=…)` 把 schema 传给 `llm_router.call(tools=…)`，但 `_call_once` 请求体只有 `{model, messages}`，tools **被忽略**；`_build_dag_prompt` 只内联工具名列表。模型拿不到参数结构，生成的 DAG inputs 字段只能靠猜。

### 5.2 方案

`_build_dag_prompt` 把每个工具的 `name/description/parameters(JSON schema)` 压缩内联进 prompt（JSON 序列化），明确要求「inputs 键必须严格取自对应工具的 parameters.properties」。不引入 tool_calls 解析（现有 DAG JSON 文本解析链路不变），`llm_router.call` 的 tools 形参标记 deprecated 并清理调用点。

## 6. 板块④ L1 工具真实现

### 6.1 工具处置表

| 工具 | 现状 | T1 处置 |
|------|------|---------|
| summarize_text | stub（`text[:max_words]` 截断） | **真实现**：llm_router.chat 摘要，入参 text/max_words |
| classify_intent | stub（恒 "unknown"） | **真实现**：llm_router.chat 返回类别标签 |
| run_python | stub（返回空 stdout） | **移出 DAG 可见集**（真实执行是 T2 沙箱范畴，假执行有害） |
| run_blast | stub（返回空 hits） | **移出 DAG 可见集**（已有真实 blast_search 替代） |

### 6.2 实现要点

- `register_l1_compute(reg, llm_router=…)` 生产已传 llm_router（app.py L84），真实现直接消费
- 移出方式：stub 工具加 `visible_to_planner=False` 开关（ToolSpec 新字段，默认 True；`to_openai_functions` 与 `registry.list(risk_level=None, planner_visible=True)` 过滤）——保留热加载/测试可直调能力
- LLM 失败时工具返回 `error_code="LLM_FAILED"`（ToolResult 失败语义，Scheduler 会置节点 FAILED 并传播 SKIPPED，不崩计划）

## 7. 板块⑤ 执行链路接线 + IM 反馈

### 7.1 ResearchRunner（新组件，orchestrator/research_runner.py）

```python
class ResearchRunner:
    """研究任务后台执行器：受理即回，完成后回结果。

    在独立线程跑 Planner→Scheduler 全链路，避免阻塞 ws 回调线程；
    DB 落库走独立 session（复用 Phase 11 独立 session 收尾惯例）。
    """

    def __init__(self, *, orchestrator, session_factory, im_adapter): ...
    def submit(self, incoming: IncomingMessage) -> dict:
        """受理：im.reply 受理语 + 起后台线程；返回 {"status": "accepted", ...}"""
    def _run(self, incoming): ...  # plan → schedule → render → doc write → im.reply 结果
```

- **受理语**：`[研究任务] 已受理，正在规划执行…（任务越具体效果越好，完成后自动回复）`
- **完成回复**：`render_blocks_to_text(render_plan_summary(...))` 结果文本 + 节点状态摘要 + （绑定了文档时）「结果已写入文档」
- **失败回复**：plan_failed / scheduler failed 均回错误摘要（不静默）
- **超时保护**：线程内整体 wall-clock 上限 `research_task_timeout_sec`（默认 300s，settings 可调），超时置 CANCELLED 并回复超时提示
- **process_phase2 改造**：session/task 创建移入 Runner（intent 从 `phase2_plan` 改 `research`）；去掉 `_EmptySession` type() hack，改用 SessionService 正规查询

### 7.2 线程与并发

- 每条 /research 一个线程（`threading.Thread(daemon=True)`）；单机单进程约束下不设全局并发闸（Phase 13 若需要再加队列）
- DB：Runner 内 `session_factory()` 独立 session，成功 commit / 异常 rollback（Phase 11 惯例）
- ws 回调线程立即返回 `{"status": "research_accepted"}`，事件循环不被占住

## 8. 板块⑥ 配置与文档

- `load_settings()` 补读：`RESEARCH_TASK_TIMEOUT_SEC`（默认 300）、`MAX_CONCURRENT_NODES`（默认 4）
- `.env.example` 补 Phase 12 段（research 超时、并发；标注 base/drive token 为可选——不配则对应工具不注册）
- `docs/联调指南.md` 补 `/research` 用法与示例

## 9. 数据模型

无新表。复用：`sessions`（会话）、`tasks`（intent="research"，reply_text 存结果摘要）、`plan_runtime_states`（Scheduler 状态，现有）、`audit_records`。研究任务结果写文档复用 doc_write 链路。

## 10. 测试策略

| 层 | 内容 |
|----|------|
| 单测 | /research 路由（有参受理/无参提示/群聊可用）；init 条件放宽（base/drive None 时跳过注册）；schema 内联 prompt 断言；summarize/classify 真实现（mock llm_router）；planner_visible 过滤；ResearchRunner 受理即回 + 后台线程完成回调（mock orchestrator 链） |
| 集成 | e2e：mock LLM 返回固定 DAG（read_doc→summarize_text→join）→ 断言工具真实调用链 + IM 两次回复（受理+结果）+ 文档写入；超时路径；LLM 失败节点 FAILED 传播 |
| 回归 | 587 既有测试零回归；ruff 0 error |
| 真机 | ①私聊 `/research 总结当前绑定文档的核心内容并给出三条研究建议` → 受理语 → 结果回复 + 文档新增章节；②`/research 用 BLAST 检索 BRCA1 相关序列` → blast_search 真调 NCBI 返回 hits；③群聊发起同 ①；④闲聊路径不受影响 |

## 11. 实施顺序

1. ② init 放宽 + 注册 None 守卫（纯重构，先解锁引擎存在性）
2. ③ schema 内联 + ④ L1 真实现/stub 隐藏（引擎输入输出真实化）
3. ① /research 路由 + ⑤ ResearchRunner（接线）
4. ⑥ 配置文档收尾
5. 全量回归 + 真机验证 + 测试总结/spec 实施结果章节

## 12. 验收标准

- 真机四场景（§10 真机行）全部通过
- /research 全链路 wall-clock ≤ 300s，ws 事件循环无阻塞（评论事件在研究任务执行期间仍秒级响应）
- 587+ 新增测试全过，ruff clean

## 13. 风险与缓解

| 风险 | 缓解 |
|------|------|
| LLM 网关不产合法 DAG JSON | Planner 已有 validate_dag + max_retries 重试；失败回 plan_failed 错误摘要 |
| LLM 延迟叠加（intent+dag+summarize 多跳） | 后台线程 + 受理即回；整体超时上限 |
| 摘要质量差（截断→真 LLM 后不可控） | summarize prompt 固定角色词；max_words 约束写进 prompt |
| Windows 路径/编码坑（线程 + DB session） | 独立 session_factory 每线程新建；回归覆盖 |
| stub 工具误被 Planner 选用 | planner_visible=False 从 schema 根除 |

## 14. 实施结果（2026-08-30 真机验收）

### 六板块 + 真机四场景全部通过 ✅

| 场景 | 结果 |
|---|---|
| ① 私聊 `/research` 绑定文档总结 | success：read_doc → summarize×2，结果自动写入绑定文档 |
| ② `/research` BLAST 检索 BRCA1 | success：NCBI 真实命中 1257 条，records 完整展示，摘要准确 |
| ③ 群聊发起 `/research` | success：群聊门控放行指令，模型自选 gene 库命中基因条目 |
| ④ 闲聊回归 | 私聊正常走 LLM 对话；群内非指令按设计 skipped（group_non_command） |

自动化测试 616 passed + ruff clean；wall-clock 满足 ≤300s（规划 ~94s + 执行）。

### 真机迭代修复（六轮，核心价值）

实施后连续六轮真机验证暴露 LLM 输出与代码假设的系统性落差，逐轮修复：

| 轮次 | 失败现象 | 根因与修复 |
|---|---|---|
| 1 | ReadTimeout×2 + JSON 解析失败 | DAG 生成实测 ~94s 超 30s 硬编码超时；输出带 markdown 围栏。→ `llm_timeout_sec=120` 可配 + `_extract_json_object` 剥围栏/截取 + 重试附错误反馈（`5efeb70`） |
| 2 | `'kind'` KeyError | 纯 tool 节点模型常省略 kind。→ `kind/type` 缺省 tool + entry 自动推导 + prompt 显式格式示例（`9ded34e`） |
| 3 | inputs.max_words int 炸校验 | DAGNode.inputs 契约 dict[str,str]。→ `str()` 强转 + 调度器类型守卫（`245d4dd`） |
| 4 | n1 read_doc 失败无痕 | read_doc 调不存在的 `read_blocks`（真实 API 为 `get_block_tree`）；文档写回调不存在的 `append_blocks`（真实为 `render_blocks`）；模型无从得知绑定 doc_id。→ 真实 API + session_context 注入 + 节点失败日志/回复附错误详情（`eaa610c`） |
| 5 | `unknown block type: None` / L0 白等 30s | read_doc 输出键 `blocks` 撞 ToolHandler 富文本保留键被误解析；ExecutionTask.risk_level 默认 L1 且 Scheduler 不传。→ 输出改 `block_tree`/`text` + 解析降级不炸工具 + risk_level 以注册表为准 + kernel 探测失败一次记忆跳过（`e3a6eb2`） |
| 6 | n3 TOOL_DENIED / 摘要输入空 / NCBI 0 命中 | L2 工具名暴露给模型被规划又被 approval 拒；模型猜错输出字段名；`nr` 是 BLAST 库名非 Entrez 库（esearch 静默 0 命中）。→ L2 名单/schema 整体剔除 + prompt 链式示例 + `_resolve_inputs` 别名兜底（records/text/results/summary）+ 库名映射 nr→protein（`3c38375`/`ef182b0`/`8344d98`） |

### 沉淀的可观测性

- 规划结果 JSON 落 INFO 日志（`research plan: …`）——模型到底规划了什么可审计
- 节点失败 warning 日志 + IM 回复「失败详情」段（error_code/message）
- 关键输出摘要含 list/dict（JSON 序列化展示）

### 遗留（Phase 13）

- T2 沙箱执行：kernel 镜像构建 + run_python/run_blast 真实执行
- T3 控制流：branch/while/for 真机验证
- L2 审批流：研究任务内需写文档时的正式审批链路（当前由 Runner 自动写回替代）
- 节点级重试与部分失败的人工介入指令
