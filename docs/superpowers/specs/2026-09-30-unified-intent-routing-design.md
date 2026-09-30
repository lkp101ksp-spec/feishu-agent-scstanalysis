# 统一入口路由设计：research 优先 · code 回退 · 共享工作区

日期：2026-09-30
状态：已获用户批准（对话确认"按这个方案来"）

## 1. 背景与动机

当前用户必须自己区分 `/research`（数据分析，DAG 计划 + 沙箱生信工具）与
`/code`（agentic coding，ReAct 循环 + 文件/命令工具）。真机暴露三类痛点：

1. 自然语言依赖意图闸确认卡，用户要点卡才执行，打断心流；
2. 误路由代价高（"把图加到文档里"被分到 /code → 幻觉 test_data.csv 假报告）；
3. 双工作区物理隔离：/code 的 code_workspace 看不到 bio_workspace 的数据，
   agent 隔空猜 dataset_ref，猜中已 GC 删除的旧 id 直接失败（2026-09-30 真机）。

用户目标（原话）：用户端不区分 /code 和 /research，模型自己区分且不询问；
research 走不通时内部自动走 code 纠错/改代码直到步数上限；分析同一数据时
二者共享同一工作目录。

## 2. 总体架构

```
自然语言消息
   │
   ▼
意图分类（LLM，既有 _CLASSIFY_SYSTEM 规则）
   │
   ├─ chat     → 闲聊（不变）
   ├─ code     → CodingRunner（明显代码活直接进，不绕 research）
   └─ research → ResearchRunner 执行
                    │
                    ├─ 终态 success → 正常交付（不变）
                    └─ 终态 failed / success_with_partial_failure
                         → 自动 code 回退环（P2）
                            1. 打包失败上下文交 CodingRunner
                            2. code 诊断/补救（步数上限=既有上限）
                            3. 判定可重试 → 自动重跑原 research 任务（≤1 次）
                            4. 合并结果推送（一条连续任务线）
```

显式 `/research`、`/code` 前缀保留为手动兜底，跳过分类直接路由（不变）。

## 3. P1：静默路由

### 3.1 IntentGateService 增静默模式

- 设置项新增 `intent_gate_confirm: bool`（env `INTENT_GATE_CONFIRM`，
  默认 `false` = 静默）。
- 静默模式下 `maybe_offer` 分类命中 research/code 后**不发卡不入
  _pending**，直接返回：
  `{"status": "intent_auto", "route": route, "incoming_kwargs": {...}}`——
  incoming_kwargs 复用 decide() 既有拼装（text 前缀 `/research `/`/code `），
  app.py 走与卡片批准相同的再分发路径，机制零新增。
- 路由可见性：发一条**不可交互**的 IM 文本提示
  （"已按分析任务处理 / 已按代码任务处理"），不发卡、不等待。
- `intent_gate_confirm=true` 时行为与现状完全一致（确认卡 + TTL），
  一键回退。
- chat / 分类异常 / `/` 开头：行为不变（落闲聊或命令路由）。

### 3.2 app.py 路由链改动

`maybe_offer` 返回值分支：新增 `intent_auto` → 直接以 incoming_kwargs
构造新 IncomingMessage 重入 handle_message（与 research_intent 回调
批准后的处理同构）。

## 4. P1：共享工作区

### 4.1 code 会话目录迁入 bio_workspace

- CodingRunner 的 WorkspaceManager 根从 `code_workspace_root`
  （`./code_workspace`）改为 `bio_workspace/_code/`；
  会话目录 = `bio_workspace/_code/code_<session>/`。
- CodeTools 读写策略：
  - **写**（写文件/run_cmd 落盘）限定会话子目录内；
  - **读**（读文件/列目录/run_cmd 只读命令）放开整个 bio_workspace——
    h5ad 元数据、报告、图直接可见可读。
  - run_cmd 的 cwd 仍为会话子目录；命令里引用数据文件用相对或绝对路径。
- **GC 天然豁免**：bio_workspace_gc 只处理匹配 `^[0-9a-f]{12}` 的目录，
  `_code` 前缀本来就不在清扫面内——无需改 GC，仅补一条回归单测锁死该
  行为（防止未来正则变动误扫会话目录）。
- 旧 code_workspace 目录不迁移、不删除（历史产物保留，自然淘汰）。

### 4.2 数据集清单双平面互通

- `build_workspace_context()`（dataset_profile.py，现仅 research_runner
  注入）同步注入 CodingRunner 的系统上下文——两个平面看到同一份
  dataset_ref 清单 + 数据根目录 + "严禁重新发现"指引。
- /code 的 sc_* registry 白名单保留（有清单后不再瞎猜 id）。

## 5. P2：失败自动 code 回退环

### 5.1 触发条件

ResearchRunner 任务终态为 `failed` 或 `success_with_partial_failure`
（至少一个节点 failed）且**非用户主动取消**时触发。

### 5.2 回退流程

1. research_runner 终态落点（task done）处 hook：构造回退包
   `{"task_text", "plan_json", "failures": [{node_id, tool, error_code,
   error_message_tail}], "workspace_context"}`；
2. 调 CodingRunner 执行诊断补救，任务文本模板：
   "研究任务失败，诊断并补救：…（附回退包）。允许：读 bio_workspace、
   写会话目录、跑只读检查脚本；禁止：删除数据文件。"；
3. code 终态解析产出 `retryable: bool`（约定输出标记，如末条消息含
   `[RETRYABLE]` / `[NOT_RETRYABLE]`，缺失按 false）；
4. retryable → 以原 task_text 重跑 ResearchRunner 一次；
5. 全程结果合并推送：research 失败摘要 → code 补救过程 → 重试结果。

### 5.3 防循环

- 每个原始任务**最多自动回退 1 次、重跑 1 次**（回退环内 research 再失败
  不再二次回退，直接推送终态）；
- code 步数上限沿用既有 `coding_runner` 配置，不再叠加；
- 回退各环节日志打 `fallback` 标记，可审计。

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| 分类 LLM 异常 | 回退闲聊（现状不变） |
| 静默路由后 research 立即失败 | 正常触发 P2 回退环 |
| code 回退自身崩溃 | 推送 research 原失败 + "自动补救未能完成"，不再重试 |
| IM 路由提示发送失败 | 仅 warn，不阻断任务 |
| GC 与 _code 目录 | 既有 `^[0-9a-f]{12}` 正则已豁免，补回归单测锁死 |

## 7. 测试策略

- 静默路由：maybe_offer 静默模式不发卡、返回 intent_auto；confirm=true
  回归发卡；chat/异常不变。
- app 路由链：intent_auto → 正确分发到 research/code runner。
- 共享工作区：WorkspaceManager 新根会话目录创建；写越界拒绝；读
  bio_workspace 数据文件放行；GC 跳过 `_` 前缀目录。
- 清单注入：coding_runner 系统上下文含 dataset_ref 清单。
- 回退环：失败终态触发回退包构造；retryable 标记解析；重跑 ≤1 次；
  回退崩溃不炸主流程。

## 8. 分期与验收

- **P1**（本 spec 先行实施）：静默路由 + 共享工作区 + 清单互通。
  验收：自然语言"提取导管细胞重聚类"无确认卡直接执行成功；/code 会话
  内 `dir` 可见 bio_workspace 数据；GC 跑一轮 _code 目录完好。
- **P2**：失败回退环。验收：构造一个必失败的 research 任务（如引用不
  存在的 dataset_ref），观察自动 code 回退、诊断输出、不超过 1 次重跑。

## 9. 不做的事（YAGNI）

- 不动 GC 的 TTL/LRU 策略本身；不动 research 工具面与审批流；
- 不做 code→research 方向的反向回退（代码活失败不转 research）；
- 不物理合并两个工作区根目录；不迁移历史 code_workspace 产物。
