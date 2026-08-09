# 飞书科研闭环 Agent — Phase 2~5 归档（历史草案）

> **归档说明**：本文件保留了 2026-08-08 第一次完整设计稿（43KB）里关于 Phase 2~5 的全部内容。GPT 在二次评审时认为这些内容超出 Phase 1 范围，建议移到独立文件。此处仅作背景参考，不作为 Phase 1 实施依据。
>
> Phase 1 的当前设计在 [`2026-08-08-feishu-research-agent-design.md`](./2026-08-08-feishu-research-agent-design.md)；Phase 1 的实施计划在 [`../plans/2026-08-08-feishu-research-agent-phase1.md`](../plans/2026-08-08-feishu-research-agent-phase1.md)。

---

## 原 Phase 2: 计算能力接入 (Week 3-4)

**目标**：让模型能"动手"做计算，不只是"动嘴"说。

**交付物**：用户说"帮我画个箱线图"，模型自动从文档提取数据、生成图表、插入文档。

### 涉及模块（已在第一版设计中详细定义）

- **执行层（Executor Pool）**
  - Docker 沙箱 + Jupyter Kernel 池
  - 工具分类：L0 只读 / L1 计算 / L2 副作用
  - 产物回收路径：<1MB 文本 → Base；1~500MB 文件 → Drive

- **飞书适配层扩展**
  - Drive Adapter：分片上传 >50MB 文件
  - Template Engine：根据产物 kind（figure/table/code/text）转飞书块

- **Agent 升级**
  - Planner 支持 L1 工具调用（如 `run_python`）
  - 卡片回调：用户审批 L2 操作

### 关键技术决策（保留）

| 决策 | 内容 |
|---|---|
| 工具 schema 格式 | OpenAI function calling JSON |
| Kernel 生命周期 | 新建 / 空闲（30min）/ 重连 / 崩溃重试 / 清理 |
| 沙箱网络 | 默认断网，例外白名单（BLAST/AlphaFold/UniProt） |
| 静态分析 | P0 硬阻塞（os.system/subprocess/socket/ctypes/外部 pickle.loads） |
| DLP 扫描 | API key / 内部 IP / 中国身份证 / 飞书 secret |

---

## 原 Phase 3: Agent 智能升级 (Week 5-6)

**目标**：让系统能自主规划复杂研究任务。

**交付物**：用户说"分析这批数据并建模"，系统自动完成：数据清洗 → EDA → 特征工程 → 模型训练 → 结果可视化 → 全部写入文档。

### 关键能力（已在第一版设计中详细定义）

- **Planner 内部结构**
  - intent_parser → dag_builder → static_dag ∪ conditional_dag → dispatcher 循环
  - 条件分支示例：`if p_value < 0.05 then run sensitivity_analysis`
  - dispatcher 评估条件后动态追加 DAG 节点

- **LLM Router 路由策略**
  | 任务 | 主模型 | 备用 |
  |---|---|---|
  | intent 解析 | 轻量（Haiku/Mini） | 同主 |
  | Plan 生成 | 强推理（Sonnet/Opus） | 主→次→本地 Qwen |
  | 结果润色 | 中等（GPT-4.1/Sonnet） | 主→本地 |
  | 代码生成 | 强代码（Sonnet/o3） | 主→本地 DeepSeek-Coder |

- **上下文管理**
  - 单会话 token 预算默认 200k
  - 压缩顺序：折叠 tool_results → 截代码 → 总结旧消息
  - 子会话拆分：每完成一个 DAG 节点开新 session_id，但 variables 跨 session 共享

---

## 原 Phase 4: 科研场景深化 (Week 7-8)

**目标**：适配真实科研 workflow。

**交付物**：一个生物信息学研究者可以说"用 BLAST 比对这些序列，把结果整理进文档"，系统全自动完成。

### 关键能力（已在第一版设计中详细定义）

- **科研场景示例**：BLAST 批量比对端到端 walkthrough
  1. 用户 `@研究助手` 提指令
  2. Planner 生成 7 节点 DAG（fetch → parse → blast → 条件分支 → render → append → notify）
  3. Template Engine 把结果渲染成飞书块（heading_2/callout/table/image/text）
  4. L2 副作用（write_doc）需 IM 卡片确认
  5. 文档末尾出现结构化 BLAST 结果

- **工具插件机制**
  - 内置工具 vs 科研领域工具（BLAST/AlphaFold）
  - 热加载：watch config 目录 → sha256 变化 → reload
  - 工具风险分级：L0/L1/L2

- **多用户协作场景**
  - 文档共享给 N 个人，谁发的指令谁负责
  - L2 工具只能原作者触发

---

## 原 Phase 5: 工程化与部署 (Week 9-10)

**目标**：稳定、可扩展、可维护。

### 涉及模块（已在第一版设计中详细定义）

- **可观测性**
  | 信号 | 关键指标 |
  |---|---|
  | Trace | gateway → planner → executor → feishu_adapter |
  | Metric | task_latency_p99 / tool_success_rate / sandbox_deny_count / llm_token_per_session / kernel_pool_size |
  | Log | 结构化 JSON + task_id/session_id/actor |
  | Event | Base.audit + Webhook |

- **部署架构**
  - API Gateway（容器，≥2，含 WAF）
  - Orchestrator（容器，≥2，无状态）
  - Executor（容器，N 副本，每节点限 8 并发 Kernel）
  - PostgreSQL（主从，audit 表按月分区）
  - Redis（Kernel 状态 AOF）

- **SLO**
  | 指标 | 目标 |
  |---|---|
  | 任务完成率 | ≥95% |
  | IM→文档 P50 | ≤15s |
  | IM→文档 P95 | ≤90s |
  | 沙箱拒执行率 | ≤5% |
  | LLM 调用成本 | ≤$0.30/任务 |

- **灾备 & 安全合规**
  - PostgreSQL 每日全量 + 6h 增量备份
  - 审计日志归档 OSS ≥1 年
  - 配置仓库 PR + 双人 review
  - Vault 密钥管理
  - 网络白名单 + DLP + OAuth scope 最小化

- **扩展路线**
  - 多用户并发（Executor 按用户分池）
  - GPU 节点（AlphaFold/分子动力学）
  - 联邦部署（每团队独立 Orchestrator + 共享工具市场）
  - 离线模式（本地 LLM + 离线 Drive 缓存）

---

## 原第一版未进入主文档的关键机制（保留备查）

### "飞书文档即状态机" → 改为 "PostgreSQL 主存 + 飞书投影"

第一版的设计强调"把飞书文档本身当作系统的持久化状态和记忆载体"。GPT 重写后改为：
- PostgreSQL 是唯一主存（`sessions/tasks/artifacts/doc_writes/audit_logs`）
- 飞书 Base 仅做状态投影，投影失败不影响主事务
- 飞书 Doc 仍是叙事与结果载体
- 飞书 Drive 仍是文件存储与分享载体

**采纳原因**：Base 没有事务保证，并发写易冲突；从 PostgreSQL 恢复语义清晰。

### "三层安全沙箱" → 调整为 "容器隔离为主 + AST 为辅"

第一版的三层沙箱：
1. Docker 容器隔离（`--network=none`、`--read-only`、资源限额、cap-drop）
2. AST 静态分析（P0/P1/P2 分级）
3. 运行时 + 输出 DLP

GPT 重写后的调整：
- AST 不再是"可靠拦截边界"，而是"弱检测层"
- 真正边界是：容器隔离 + 只读根 FS + 网络白名单 + 资源限额 + 能力裁剪 + 内核销毁
- AST 只用于：提前发现危险调用、友好拒绝提示、审计附加证据

**采纳原因**：AST 静态分析有大量绕过方法（pickle/eval/compile/Jupyter间接调用），不能作为安全保证。

### "卡片回调二次确认" → 改为 "bind-doc 前置授权"

第一版对 L2 副作用工具用 IM 卡片按钮让用户确认。Phase 1 改为：
- 用户先发 `/bind-doc <doc_id>` 显式绑定
- 同一用户、同一会话、30 分钟内可对绑定文档执行"追加纯文本回复"
- 超时、跨用户、跨文档、跨群聊上下文一律重新确认

**采纳原因**：Phase 1 演示场景下 bind-doc 比每次卡片更顺滑；Phase 2+ 再恢复完整的卡片审批流。

---

## 待 Phase 2 开始时补做的设计

下面这些原 Phase 1 中讨论、但 GPT 重写时砍掉的内容，进入 Phase 2 设计稿时需重新讨论：

1. **Kernel 生命周期细节**（新建/空闲/重连/崩溃/清理）
2. **Docker 网络白名单具体名单**（BLAST/AlphaFold/UniProt）
3. **Template Engine 块类型选择决策表**（heading_2/callout/table/image/text/divider）
4. **卡片回调安全要点**（nonce/TTL/HMAC/event_token）
5. **权限模型映射公式**（用户 open_id ∩ 会话 scope ∩ 工具 risk_level）
6. **飞书侧失败降级表**（Doc权限被回收/Base字段缺失/Drive配额超限/IM卡片渲染失败/Webhook断连）
7. **执行层 gRPC proto 接口**（Submit/Stream/Cancel/Heartbeat）
8. **产物回收阈值表**（文本<1MB / 图像1~500MB / DataFrame→parquet / Notebook→Drive 链接）

---

## 参考

- 原始设计稿对话历史见 [`../../../paragraphs.txt`](../../paragraphs.txt)（如未删除）
- 第一版设计稿 43KB 完整版本在本次重写时被覆盖，已无法恢复，但核心要点在本归档文件中已保留