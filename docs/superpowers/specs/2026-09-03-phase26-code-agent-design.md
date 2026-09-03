# Phase 26：`/code` Agentic Coding Agent（自研 loop + SKILL.md 兼容）设计

> 状态：待用户审阅
> 日期：2026-09-03
> 决策路径：A/C 对比（A=全自研单引擎 / C=桥接 Pi CLI 双引擎）→ 用户定 **A'**（自研 loop + SKILL.md 兼容格式）；Pi CLI v0.84.2 已装但 `~/.pi` 未配置，不引入依赖。

## §0 背景与动机

现有系统是"研究任务执行机"：`/research` 走 Planner 单次 DAG 规划→整图执行，工具全部编译期注册（改工具=发版）。用户诉求：

1. **skill 可安装**：能力以数据文件形式存在（放目录即生效），兼容 TRAE/pi 生态的 SKILL.md 格式（用户 skill 库已有大量此格式 bio skills）
2. **agentic loop**：coding 场景需要多轮 think→act→observe（边看结果边决定下一步），非一次规划
3. **工具闭环**：既有大量编好的工具（sc_*/st_*）保持复用；新写的分析代码可沉淀为新工具

用户确认的场景默认值：**两者都要**（先写脚本跑一次性分析，验证好用后固化为 sc_tools 新工具）。

## §1 目标 / 非目标

**目标**
- [ ] `/code <任务>` 指令：飞书触发 agentic loop，写代码/跑命令/迭代修复，结果回流飞书
- [ ] skill 系统：`skills/` 目录 SKILL.md 知识面 + 可选 tools.yaml 工具面，启动扫描 + 热加载
- [ ] CodeTools 原语：read_file/write_file/edit_file/list_dir/search_files/run_cmd
- [ ] 会话工作区：`code_workspace/<session_id>/`，授权卡 + 命令策略 + 单步敏感审批
- [ ] 沉淀流程：`固化成工具` 指令生成 sc_tools 草案 + tools.yaml，人工 review 后注册
- [ ] 过程卡片流式反馈 + 结果卡 + 图表直传

**非目标**
- 不改 `/research` DAG 模式与 bio 链路（零破坏）
- 不做通用 IDE 级体验（不做 diff 视图编辑器、不做多文件重构代理）
- 不自动注册未经人工 review 的工具（bot 不自动扩自己的能力面）
- 不引入 Pi CLI / 外部 agent 引擎依赖

## §2 总体架构

```
飞书消息 "/code <任务>"
  → ws_client process() 路由（新分支，仿 /research）
  → CodingRunner（新，orchestrator/coding/runner.py）
       受理即回 + 后台线程（daemon）+ wall-clock 超时（复用 3600s 逻辑）
  → AgentLoop（新，orchestrator/coding/agent_loop.py）
       LLM 多轮 function calling：
         messages = [system(含 skill 知识面), user(任务)]
         while not done:
           resp = llm.chat(tools=[code_tools + skill_tools + 白名单现有工具])
           resp 含 tool_calls → 执行 → append tool result → continue
           resp 纯文本 → done
  → WorkspaceManager（新，orchestrator/coding/workspace.py）
       目录创建/锁定/清理 + 命令执行策略 + 授权状态机
  → 卡片更新（过程）→ 结果卡（结论+产物清单+图片）→ 可选写回文档
```

组件落点（全部新文件，现有文件仅加路由接线）：

| 组件 | 文件 | 职责 |
|---|---|---|
| CodingRunner | `orchestrator/coding/runner.py` | 指令受理/后台线程/超时/收尾 |
| AgentLoop | `orchestrator/coding/agent_loop.py` | 循环状态机/上下文管理/预算护栏 |
| CodeTools | `orchestrator/coding/code_tools.py` | 6 个文件与命令原语（含安全检查） |
| WorkspaceManager | `orchestrator/coding/workspace.py` | 工作区生命周期/授权/命令策略 |
| SkillLoader | `orchestrator/coding/skill_loader.py` | SKILL.md+tools.yaml 解析/热加载/注册 |

## §3 skill 系统

### 3.1 目录与格式

```
skills/
  <skill-name>/
    SKILL.md        # 必需。frontmatter(name, description) + 指令正文（知识面）
    tools.yaml      # 可选。工具面声明
    scripts/        # 可选。skill 自带脚本（handler 引用）
    assets/         # 可选。静态资源（参考数据等）
```

- **SKILL.md**（知识面）：frontmatter 兼容 TRAE/pi 生态惯例（`name`/`description` 必填，`when_to_use` 等字段容忍忽略）。正文是给 loop 的操作指令。**现有 TRAE bio skills 拷贝进来零改造可用**
- **tools.yaml**（工具面）：

```yaml
tools:
  - name: my_analysis
    description: 对 h5ad 做 XX 分析
    risk_level: L1_compute        # L0/L1/L2；L2 走审批卡
    handler: scripts/run.py       # 相对 skill 目录；stdin JSON → stdout JSON
    parameters:                    # OpenAPI subset，同 ToolSpec.parameters
      type: object
      properties: {path: {type: string}}
      required: [path]
    timeout_sec: 600
```

### 3.2 加载与注册

- 启动扫描：ws_client 启动时 SkillLoader 扫 `skills/`，解析并注册 tools.yaml 声明的工具进 ToolRegistry（`planner_visible=true`，与 sc_* 同池，`/research` 也能用）
- 热加载：`/code reload skills` 指令重扫（新增/更新/删除都生效），需授权卡确认（注册工具属敏感操作）
- 知识面注入：v1 用简单关键词匹配（任务文本与 skill description/name 的词元重叠数排序，命中≤3 个，正文全文注入）；未命中不注入（控制 token）。语义检索（embedding）留远期
- 失败语义：单 skill 解析失败→跳过并 warning，不影响其他 skill 与启动

### 3.3 安全边界

- skill 工具 handler 脚本**在本机直接执行**（python 子进程，cwd=skill 目录），非容器——与工作区一致的本机策略（§5）
- L2 skill 工具每次调用走现有审批卡机制
- tools.yaml 注册时校验：handler 路径必须落在 skill 目录内（防路径逃逸）

## §4 AgentLoop 设计

### 4.1 状态机

```
INIT → THINK ⇄ ACT → FINAL → DONE
         ↓        ↓
       (预算耗尽/超时/连续失败) → ABORT（带部分结果报告）
```

- 每轮 = 一次 LLM 调用（含 tools schema）；`tool_calls` → ACT（依次执行，结果以 tool role 回喂）；纯文本 → FINAL
- **步数上限 25**（env CODE_MAX_STEPS 可调）；**token 预算护栏**（累计输出超 env CODE_TOKEN_BUDGET 默认 200k → 优雅终止：要求 LLM 用现有信息收尾）
- 超时 wall-clock 3600s（与 sc_* 同口径）

### 4.2 工具面（loop 可见）

| 来源 | 工具 | 说明 |
|---|---|---|
| CodeTools | read_file / write_file / edit_file / list_dir / search_files / run_cmd | 新原语，全部过 WorkspaceManager 安全检查；search_files=工作区内正则内容搜索（grep 语义，返回文件:行号:匹配行，上限 50 条） |
| SkillLoader | tools.yaml 声明的工具 | §3 |
| ToolRegistry | 白名单子集（env CODE_TOOL_ALLOWLIST，默认 `sc_*`） | 复用现有工具；dataset_ref 语义不变 |

- write_file/edit_file 走"写前路径校验 + 生成后回显首尾行"（结果里给 LLM 看改动概要，不回显全文省 token）
- read_file 默认 head 500 行 + 超出提示 offset 分页读取

### 4.3 上下文管理

- 工具结果截断：单结果超 8KB 截断（保留头尾 + 中间省略标记）；run_cmd stdout/stderr 合并截 4KB
- 轮次压缩：对话轮数 > 12 时，旧轮工具结果替换为一行摘要（`[tool read_file xx.py → 200 lines]`），保留全部 think 文本与最近 6 轮原文
- 复用 LLMRouter（primary MiniMax-M3 function calling；env CODE_MODEL 可覆盖路由选择）

### 4.4 错误恢复

- 工具执行异常：异常信息作为 tool result 回喂（`{"error": "..."}`），LLM 自行调整——连续 2 次同一工具失败 → 该工具本会话禁用并在 FINAL 报告
- LLM 调用失败：复用 LLMRouter 重试（现有 max_retries）
- 语法性输出（非 JSON tool call）：一次纠正提示重试，再失败 ABORT

## §5 工作区与安全（已确认的 4 个默认决策）

| 决策 | 取值 | 理由 |
|---|---|---|
| 工作区位置 | 本机 `code_workspace/<session_id>/`（**非 Docker**） | 写码+跑测试需文件敏捷；容器挂 I: 盘 IO 慢；GPU 镜像 10GB 启动重 |
| 授权粒度 | `/code` 开场一张授权卡：本工作区读写 + 白名单命令；**敏感操作单步卡**（工作区外路径/非白名单命令/L2 工具） | 每步都卡会把 loop 卡死 |
| run_cmd 策略 | cwd 锁工作区 + 白名单自动放行（python/pytest/pip/git 只读类/git status·log·diff）+ 其余单步审批 + 明确黑名单（rm -rf 工作区外/格式化/注册表/网络下载可执行） | 平衡流畅与安全 |
| 沉淀方式 | 工作区脚本 → `/code ... 固化成工具` 或会话结束语提示 → 生成 sc_tools 草案 + tools.yaml 草案到 `skills/_staging/`，**人工 review 后** `/code reload skills` 生效 | bot 不自动改能力面 |

补充：
- 授权卡超时 10 分钟未确认 → 会话终止
- 会话隔离：session_id 来自现有 SessionService（同人同 chat 复用 session，工作区跨任务持久，`/code clear` 清空）
- bio 数据访问：`bio_test_data` 白名单目录只读挂接进 loop 工具面（read/list 可达，写不可达）

## §6 交互形态

- **受理卡**：任务摘要 + 授权按钮（approve/deny）
- **过程卡**：单张卡节流更新（≤1 次/10s），显示最近 5 步动作摘要（`write analysis.py (84 行)` / `run pytest → 12 passed 3 failed` / `read umap.png`）+ 当前步数/预算余量；内嵌"终止"按钮
- **结果卡**：结论文本 + 产物文件清单（可下载路径）+ 图片直传（复用现有图片回传通道）+ 提示语（`好用的话说"固化成工具"`）
- 写回文档：复用 bind-doc（session 绑定文档时结果文本写入）

## §7 持久化与可观测

- PG 复用现有表：tasks 表记 intent=`code_agent`；audit 记录每步工具调用（tool 名/参数摘要/结果状态/耗时）
- 过程日志：logging 结构化输出（step_no/tool/duration/bytes），进 ws_client.log
- 幂等：`/code` 消息级幂等复用现有 idempotency_keys

## §8 测试策略

- 单测（mock LLM）：loop 状态机（正常/步数上限/预算耗尽/超时/工具连续失败禁用/语法错误重试）；上下文截断与压缩；WorkspaceManager 路径逃逸/白名单/黑名单命令；SkillLoader 解析（正常/缺字段/handler 逃逸/热加载增删改）；CodeTools 各原语
- 集成：fake skill（SKILL.md+tools.yaml+脚本）端到端——loop 用 scripted LLM 驱动调用 skill 工具产文件
- 真机验收（人工）：`/code 写个脚本统计 bio_test_data 下 h5ad 的细胞数` 全链 + 固化流程 + 审批卡三场景（开场授权/敏感命令/超时）

## §9 验收标准

1. `/code` 全链真机跑通（授权→loop→过程卡→结果卡）
2. 拷贝一个 TRAE bio skill（纯知识型）进 skills/，loop 任务命中其知识面
3. tools.yaml 工具注册后 `/research` 也能规划调用
4. 敏感命令被单步审批拦截；工作区外写入被拒
5. 25 步上限/预算护栏/超时三终止路径单测覆盖
6. 全量回归通过（现有 846 用例零破坏）

## §10 实施分期建议（详细任务由 plan 拆）

- **批①**：WorkspaceManager + CodeTools + 安全策略（TDD）
- **批②**：AgentLoop 状态机 + 上下文管理（TDD）
- **批③**：SkillLoader + tools.yaml 注册（TDD）
- **批④**：CodingRunner + 飞书卡片交互 + /code 路由接线
- **批⑤**：fake skill 集成冒烟 + 真机验收 + 文档
