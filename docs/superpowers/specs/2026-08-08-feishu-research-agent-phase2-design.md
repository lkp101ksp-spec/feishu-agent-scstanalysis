# 飞书科研闭环 Agent — Phase 2 设计稿

> 日期：2026-08-08
> 状态：Phase 2 设计定稿（待实施）
> 范围：让 LLM 能"动手"做计算；继承 Phase 1 主存与审批模型
> 前置：[Phase 1 设计稿](./2026-08-08-feishu-research-agent-design.md)
> 后置：Phase 2 实施计划 [`../plans/2026-08-08-feishu-research-agent-phase2.md`](../plans/2026-08-08-feishu-research-agent-phase2.md)（待写）

---

## 1. 范围与目标### 1.1 Phase 2 主题

让 LLM 能"动手"做计算，而不只是"动嘴"说：

- 用户说"画箱线图" → 模型从 doc 提取数据 → Kernel 跑 Python → 生成图 → 上传 Drive → 模板引擎渲染飞书 image 块 → 追加到 doc
- 用户说"调用 BLAST 比对" → 调 L1 工具 `run_blast`（白名单网络放行）→ 结果落 artifacts → 上传 Drive → 写文档
- 用户说"上传文件" → 触发 L2 卡片审批 → 确认后分片上传 Drive → 关联 artifacts

### 1.2 范围（在 Phase 1 基础上新增）

| 子系统 | Phase 2 内容 | Phase 2 不做 |
|---|---|---|
| Executor | Docker 沙箱 + Jupyter Kernel 池 + 工具协议（OpenAI function calling） | GPU 节点、联邦部署、gRPC 拆分（Phase 5） |
| Planner | DAG 节点定义 + 静态调度器 + 上下文窗口管理 | 条件分支动态追加（Phase 3） |
| Tools | 工具注册中心 + L0/L1/L2 风险分级 + P0 AST 硬阻塞 | 热加载、BLAST/AlphaFold 具体工具（Phase 4） |
| Adapter | Drive Adapter（分片上传） + Template Engine（飞书块类型） | 富文本块、群聊多人协同写（Phase 4） |
| Approval | 卡片回调审批（explicit_card）+ nonce/TTL/HMAC | 群聊上下文级审批（Phase 4） |

### 1.3 关键约束（继承 Phase 1）

- PostgreSQL 仍为唯一主存；飞书 Base 仅做投影
- bind-doc 30 分钟前置授权**保留不变**，仅对**未被 bind-doc 覆盖**的 L2 操作走卡片审批
- AST 仅保留 P0 硬阻塞（os.system / subprocess / socket / ctypes / 外部 pickle.loads）
- 所有副作用操作在 audit_logs 留痕；artifacts 与 doc_writes 同源

---

## 2. 总体架构### 2.1 架构图

```text
FastAPI (Phase 1 不变)
  ├─ Gateway
  │     └─ /webhook/lark/card (Phase 2 新：卡片回调)
  └─ Orchestrator (Phase 1 升级)
        ├─ Planner (Phase 2 新)
        │     ├─ IntentParser     (LLM-call-A: 短 prompt → intent 字符串)
        │     ├─ DAGBuilder       (LLM-call-B: 长 prompt + tools schema → DAGPlan JSON)
        │     ├─ DAGValidator
        │     └─ Scheduler        (事件循环驱动)
        ├─ ExecutorPool (Phase 2 新)
        │     ├─ ExecutorClient        (抽象接口)
        │     │     └─ LocalExecutor   (Docker + Kernel 实现，Phase 5 可换 GRpcExecutor)
        │     └─ KernelManager
        │           └─ KernelPool      (key=session_id, value=KernelProcess)
        ├─ Tool Framework (Phase 2 新)
        │     ├─ ToolRegistry
        │     ├─ ToolHandler
        │     └─ ASTGuard             (P0 硬阻塞)
        ├─ ApprovalService (Phase 2 新)
        ├─ DriveAdapter (Phase 2 新)
        ├─ TemplateEngine (Phase 2 新)
        ├─ Session / BindDoc / Task / DocWrite 服务 (Phase 1 不变)
        ├─ LLM Router (Phase 1 升级，新增 role 参数)
        └─ Feishu Adapter
              ├─ IM Adapter (Phase 2 新增 send_card / 接收 callback)
              ├─ Doc Adapter (Phase 2 增强：append_blocks)
              ├─ Drive Adapter
              └─ Base Projection Adapter
```### 2.2 关键交互流

```
用户发 IM
  → Gateway 校验签名、限流、幂等
  → Orchestrator 创建 session / task
  → Planner.plan(message, session_context) → DAGPlan
  → Scheduler.run_until_done(plan)
       → ExecutorClient.submit(node)
            → ToolHandler.execute(tool, args)
                 ├─ AST check（L1）
                 ├─ Approval check（L2）：bind_doc 覆盖 / 卡片审批
                 ├─ Kernel.exec（L1） / main-process.exec（L0/L2）
                 ├─ DriveAdapter.upload（产物 >4MB）
                 ├─ 写 executions + audit_logs
                 └─ 收集 outputs（值 + artifacts）
       → 节点完成 → 解锁下游 → 继续
  → 节点全部完成（或终止）
       → TemplateEngine.render_plan_summary
       → DocAdapter.append_blocks（按 L2 走审批）
       → IMAdapter.send IM 通知用户
  → Base 投影（异步）
```

### 2.3 架构选型说明

**单进程 Executor 池 + ExecutorClient 抽象接口**：
- Phase 2 与 Orchestrator 同进程，部署形态不变
- 通过 `ExecutorClient` 抽象隔离实现
- Phase 5 切 gRPC 时仅替换实现（`GRpcExecutor`），Orchestrator 接口不动

---

## 3. Executor 池### 3.1 目标

1. 隔离：用户代码无法访问主机文件系统 / 进程 / 网络（除白名单）
2. 可恢复：Jupyter Kernel 30 分钟空闲后销毁；用户再次提问可基于现有 doc 上下文继续
3. 可取消：用户发 `/cancel <task_id>` 时，正在执行的代码 5s 内必须被中断
4. 可审计：每次 exec 都落 `executions` 表 + `audit_logs`
5. 可扩：Phase 5 切 gRPC 时仅替换实现

### 3.2 总体组件图

```
ExecutorPool
  ├─ ExecutorClient (抽象接口)
  │     ├─ submit(task) → TaskHandle
  │     ├─ cancel(handle) → None
  │     ├─ get_status(handle) → ExecutionState
  │     └─ list_active() → list[TaskHandle]
  │
  └─ LocalExecutor (生产实现)
        ├─ KernelManager
        │     ├─ KernelPool (key=session_id, value=KernelHandle)
        │     └─ 生命周期：new / idle(30min) / reconnect / crash_retry / cleanup
        ├─ DockerSandbox (每个 Kernel 一个容器)
        └─ ResourceLimiter (CPU/内存/PID 配额)
```

### 3.3 Docker 沙箱### 3.3.1 容器配置

- 镜像：`feishu-research-agent/kernel:latest`（Phase 2.0 计划任务里构建）
- 启动命令：`jupyter kernel --KernelManager.transport=tcp`
- 启动参数（统一来自 `sandbox.DockerSandboxConfig`）：

| 参数 | 值 | 说明 |
|---|---|---|
| `--network` | `none` 默认；白名单时 `bridge` + 自定义网桥 | 网络隔离 |
| `--read-only` | true | 根 FS 只读 |
| `--tmpfs` | `/tmp:size=64m` | 临时写入 |
| `--tmpfs` | `/workspace:size=512m` | 用户代码工作目录 |
| `--cpus` | `1.0` | CPU 限额 |
| `--memory` | `512m` | 内存限额 |
| `--pids-limit` | `64` | 进程数 |
| `--cap-drop` | `ALL` | 能力裁剪 |
| `--security-opt` | `no-new-privileges` | 禁止提权 |
| `-u` | `1000:1000` | 非 root 运行 |
| `--restart` | `no` | 崩溃不自动重启（让 KernelManager 决定） |

### 3.3.2 网络白名单

- 默认断网（`--network=none`）
- 白名单通过 `sandbox/network_allowlist.yaml` 配置
- Phase 2 内置白名单：
 - `blast.ncbi.nlm.nih.gov`
 - `rest.ensembl.org`
 - `www.uniprot.org`
 - `alphafold.ebi.ac.uk`
- 沙箱启动时把白名单域名解析为 IP，缓存 24h，写入 `/etc/hosts.allow` 并放行这些 IP 出网

### 3.3.3 文件 I/O

- 容器内：`/workspace/` 是用户代码工作目录
- 主机侧挂载：`/var/lib/feishu-agent/sessions/<session_id>/workspace/` ↔ 容器内 `/workspace/`
- 产物回收路径：
 - `<1MB` 文本 → inline 入 artifacts
 - `1MB ~ 500MB` 文件 → 上传 Drive，Drive token 入 artifacts.storage_ref
 - `>500MB` → 拒绝（Phase 2 不做分片大文件）### 3.4 Jupyter Kernel 管理### 3.4.1 KernelPool

```
class KernelPool:
    """key = session_id, value = KernelHandle"""
    def acquire(self, session_id) -> KernelHandle: ...
    def release(self, session_id) -> None: ...
    def idle_sweep() -> int: ...  # 清理超时空闲 Kernel
```

- `acquire(session_id)`：查找或新建 KernelHandle
 - 存在且 `last_used_at` < 30min → 重连（失败则新建）
 - 不存在 → 通过 `LocalExecutor.start_kernel()` 新建容器 + Kernel
- `release(session_id)`：仅在 session 关闭时调用
- `idle_sweep()`：每 60s 跑一次，清理 `last_used_at > 30min` 的 Kernel

### 3.4.2 生命周期状态机

```
new ──execute()──> running ──(success/error)──> idle
 │ │
 │                                          └─(30min 空闲)─> cleanup
 │                                          │
 │                                          └─execute()──> running
 │
 └─(crash)─> crash_retry(<=3)──> failed ──(>3)─> cleanup
```

### 3.4.3 通信协议

- 容器内 Jupyter Kernel 走 TCP（默认端口随机），不暴露公网
- 主机侧通过 `jupyter_client.KernelManager` 连接
- 协议：Jupyter wire protocol v5
- 代码提交：`km.execute(code)` → 异步等待 `iopub_channel` 的 `execute_result` / `stream` / `error`
- 超时：默认 60s，工具配置可覆盖（`ToolSpec.timeout_sec`）

### 3.4.4 中断 / 取消

- `cancel(handle)` 调用 `km.interrupt_kernel()`（发送 SIGINT）
- 5s 后仍未退出 → `km.shutdown_kernel(now=True)`
- 容器层：`docker kill <container_id>` 作为最后手段
- 任何取消必须写入 `audit_logs(action=cancel, detail={stage, reason})`

### 3.5 抽象接口

```
class ExecutorClient(ABC):
    @abstractmethod
    def submit(self, task: ExecutionTask) -> TaskHandle: ...
    @abstractmethod
    def cancel(self, handle: TaskHandle) -> None: ...
    @abstractmethod
    def get_status(self, handle: TaskHandle) -> ExecutionState: ...
    @abstractmethod
    def list_active(self) -> list[TaskHandle]: ...
```

实现：
- Phase 2 / Phase 3：`LocalExecutor`（Docker + Kernel）
- Phase 5：`GRpcExecutor`（远端）+ `LocalExecutor` 兜底

### 3.6 风险与边界

| 风险 | 缓解 |
|---|---|
| Docker daemon 在 Windows 上不可用 | `sandbox.runner` 检测 `docker info` 退出码；不可用时抛 `SandboxUnavailableError` |
| Kernel 内存泄漏 | 资源限额 + 30min 空闲清理 + 容器内 OOM killer |
| 代码死循环 | 执行超时 60s + Kernel 中断协议 |
| 网络出口被滥用 | 默认断网 + 白名单精确到域名 |
| 容器逃逸 | `--cap-drop=ALL` + `--read-only` + 非 root |
| 镜像供应链 | Phase 2 自构建；Phase 3 引入 cosign 签名 |

### 3.7 不做

- ❌ 多语言 Kernel（仅 Python 3.11）
- ❌ GPU 节点
- ❌ 实时屏幕回放
- ❌ 跨主机 Kernel 迁移
- ❌ Kernel 状态序列化（重启即丢变量；Phase 3 评估）

---

## 4. Planner 与 DAG### 4.1 目标

1. DAG 是数据流：节点 = 工具调用 / LLM 调用 / 控制流；边 = 数据依赖
2. 静态结构：Plan 在执行前一次生成，运行期不再追加节点
3. 可恢复：每个节点输入输出落 PostgreSQL `executions`，失败后可独立重试
4. 可取消：`/cancel <task_id>` 取消整个 Plan，正在跑的节点中断，pending 节点标 cancelled
5. 可审计：Plan 落 `tasks.plan_json`；每次节点执行落 `executions` + `audit_logs`

### 4.2 DAG Schema

```python
class DAGNode(BaseModel):
    node_id: str                        # ULID
    kind: Literal["tool", "llm", "branch", "join"]
    tool_name: Optional[str]            # kind=tool 时
    inputs: dict[str, str]              # {"param_name": "<upstream>.output_field"}
    depends_on: list[str]               # 上游节点 id 列表
    config: dict = {}                   # 超时/重试等
    # kind=branch 专用
    condition: Optional[str] = None
    true_branch: Optional[list["DAGNode"]] = None
    false_branch: Optional[list["DAGNode"]] = None
    # kind=join 专用
    join_strategy: Optional[Literal["all", "any", "first"]] = None

class DAGPlan(BaseModel):
    plan_id: str                        # ULID
    task_id: str
    session_id: str
    nodes: list[DAGNode]
    entry_node_ids: list[str]
```

约束：
- `entry_node_ids` 节点 `depends_on` 必须为空
- 每个非 entry 节点的 `depends_on` 必须在 `nodes` 列表里有对应 node_id
- 不允许循环依赖（Planner 校验时 DFS 检测）
- `branch` 节点的 `true_branch` / `false_branch` 是嵌套子树（递归）

### 4.3 Planner 组件图

```
Orchestrator.process(message)
  │
  ▼
Planner.plan(message, session_context)
  │
  ├─ IntentParser   (LLM-call-A: 短 prompt → intent 字符串)
  │
  ├─ DAGBuilder     (LLM-call-B: 长 prompt + tools schema → DAGPlan JSON)
  │     ├─ schema 校验 + 循环依赖检测
  │     ├─ AST 注入 P0 硬阻塞标记
  │     └─ plan_id / node_id 用 ULID 生成
  │
  └─ DAGValidator
        ├─ 节点引用的 tool_name 必须已注册
        ├─ 节点 inputs 引用的上游 output 字段存在
        └─ DAG 完整闭合（无悬空节点）
        │
        ▼（校验失败 → LLM 重试 1 次，仍失败 → task 标 failed + 写 audit）
```

### 4.4 Scheduler（事件循环）

```
class Scheduler:
    def __init__(self, executor: ExecutorClient, plan: DAGPlan): ...
    async def run_until_done(self) -> PlanResult:
        """驱动 Plan 执行到结束。"""
        while not self._all_terminal():
            ready = self._nodes_ready_to_run()
            for node in ready:
                await self._submit_node(node)  # 并发提交到 executor
            await self._wait_any()
        return self._collect_result()
```

要点：
- `ready` = 所有 `depends_on` 都已 `success`，且本节点 `pending`
- `branch` 节点：等 condition 上游完成后，用 LLM（轻量）求值
- `join` 节点：等所有上游完成，按 `join_strategy` 决定结果
- 失败处理：默认 `on_node_fail=continue`（推荐值）
- 并发上限：`max_concurrent_nodes=4` per Plan

### 4.5 LLM 路由策略

通过 `LLMRouter(role="...")` 区分角色：

| 任务 | 模型 | role |
|---|---|---|
| Intent 解析 | 轻量（Haiku/Mini） | `intent_parser` |
| DAG 生成 | 强推理（Sonnet/Opus） | `dag_builder` |
| 节点内文本生成 | 中等（Sonnet/4.1） | `text_gen` |
| Branch 条件求值 | 轻量 | `condition_eval` |

每个 role 有独立的 primary / fallback 与 token 预算。

### 4.6 上下文窗口管理

- 单会话 token 预算默认 200k
- 超出时按以下顺序压缩：
 1. 折叠 tool_results：超过 4k token 的执行结果折叠为"摘要：<前 200 字> + artifact 引用"
 2. 截代码：超过 200 行的代码块保留签名 + 关键行
 3. LLM 总结旧消息（Phase 3）
 4. 冻结旧 session 并开新 session_id（Phase 3）

Phase 2 仅实现 1+2；3+4 留 Phase 3。

### 4.7 持久化与重试

- `tasks.plan_json`：完整 DAGPlan JSON（已存在 schema）
- `executions`（Phase 2 新增）：每个节点一次执行一行

**重试规则**：
- 节点失败 → 自动重试 `max_retries` 次（默认 1）
- 重试用尽 → 按节点 `on_node_fail` 决定（默认 `continue`）
- Plan 整体重试：用户发 `/retry <task_id>` 重新驱动，所有失败节点重置 pending

### 4.8 风险与边界

| 风险 | 缓解 |
|---|---|
| DAG 生成 LLM 幻觉（节点引用不存在的 tool） | DAGValidator 拦截 + 失败重试 1 次 |
| DAG JSON 巨大 | Pydantic + `orjson` + gzip 落库 |
| 节点并发跑失控 | `max_concurrent_nodes=4` |
| 节点失败污染下游 | `join_strategy` + 下游 skip 传播 |
| Plan 重放成本高 | 每个 execution 已落库，重放仅重跑 failed 节点 |

---

## 5. 工具框架### 5.1 目标

1. 协议统一：所有工具按 OpenAI function calling JSON schema 注册
2. 风险分级：L0 只读 / L1 计算 / L2 副作用三级管理
3. P0 硬阻塞：AST 检测 `os.system / subprocess.* / socket.* / ctypes.* / 外部 pickle.loads`
4. 审批绑定：L2 工具调用前必须有审批记录
5. 可审计：每次工具调用落 `executions` + `audit_logs`

### 5.2 风险分级与审批

| 级别 | 示例 | 默认策略 |
|---|---|---|
| **L0 只读** | `read_doc`, `read_base`, `list_drive` | 自动允许 |
| **L1 纯计算** | `summarize_text`, `classify_intent`, `run_python`, `run_blast` | 会话内允许，受沙箱限制 |
| **L2 副作用** | `write_doc`, `write_base_projection`, `send_card`, `upload_drive` | 必须有审批记录 |

bind_doc 与审批的分工：
- bind_doc 仅豁免 `write_doc`（同 doc，30min TTL）
- 其他 L2 工具一律走卡片审批

### 5.3 工具注册中心

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict                      # OpenAI JSON schema
    risk_level: Literal["L0_read", "L1_compute", "L2_side_effect"]
    handler: Callable[..., Any]
    requires_approval: bool = False
    timeout_sec: int = 60
    max_retries: int = 1
    on_node_fail: Literal["stop_plan", "continue", "retry_n"] = "continue"
    tool_version: str = "1.0.0"
    approval_card_template: Optional[str] = None  # L2 专用

class ToolRegistry:
    def register(self, spec: ToolSpec) -> None: ...
    def get(self, name: str) -> ToolSpec: ...
    def list(self, risk_level: Optional[str] = None) -> list[ToolSpec]: ...
    def to_openai_functions(self, scope: ApprovalScope) -> list[dict]:
        """返回 OpenAI function calling 格式；L2 工具视 scope 决定是否暴露。"""
```

### 5.4 内置工具清单（Phase 2）

| name | risk | 说明 |
|---|---|---|
| `read_doc` | L0 | 读取飞书 doc 块树 |
| `read_base` | L0 | 读取飞书 base 记录 |
| `list_drive` | L0 | 列 Drive 文件 |
| `summarize_text` | L1 | 文本摘要（调 LLM） |
| `classify_intent` | L1 | 意图分类 |
| `run_python` | L1 | 在 Kernel 执行 Python |
| `run_blast` | L1 | BLAST 比对（白名单网络） |
| `write_doc` | L2 | 追加块到 doc |
| `write_base_projection` | L2 | 写 Base 投影 |
| `send_card` | L2 | 发送 IM 交互卡片 |
| `upload_drive` | L2 | 上传文件到 Drive |

### 5.5 工具调用执行流程

```
Scheduler.submit_node(node)
  │
  ▼
ExecutorClient.submit(task=ExecutionTask(
    handler=ToolHandler(tool_name, registry),
    args=resolved_inputs,
    risk_level=tool.risk_level,
    ...
))
  │
  ▼
ToolHandler.execute()
  │
  ├─ 1. AST 静态检查（仅 L1）→ 命中 P0 → 抛 ToolBlockedError
  ├─ 2. 审批检查（仅 L2）
  │     ├─ bind_doc 覆盖 → 通过
  │     └─ 否则 ApprovalService.request() → 等回调
  ├─ 3. 调用 tool.handler(**args)
  ├─ 4. 收集结果（函数返回值 / stdout / artifacts / approval_id）
  └─ 5. 落 executions + audit_logs
```

### 5.6 AST 静态分析

定位：**弱检测层，给用户更友好的拒绝原因 + 审计附加证据。** 真正的安全边界是 Docker 沙箱。

```python
class ASTGuard:
    """P0 硬阻塞：命中即拒绝"""
    BLOCKED_CALLS = {
        ("os", "system"),
        ("os", "popen"),
        ("subprocess", "run"),
        ("subprocess", "Popen"),
        ("subprocess", "call"),
        ("socket", "socket"),
        ("socket", "create_connection"),
        ("ctypes", "CDLL"),
        ("ctypes", "windll"),
    }
    def check(self, code: str) -> None:
        """命中 P0 抛 ToolBlockedError"""
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name):
                        key = (node.func.value.id, node.func.attr)
                        if key in self.BLOCKED_CALLS:
                            raise ToolBlockedError(
                                f"P0 blocked call: {key[0]}.{key[1]} at line {node.lineno}"
                            )
```

Phase 2 范围：
- ✅ P0 硬阻塞（命中即拒绝）
- ❌ P1/P2 分级（requests / urllib / 写非 /workspace 的 open）—— Phase 3
- ❌ 输出 DLP 扫描 —— Phase 5

### 5.7 OpenAI function calling schema

`ToolSpec.parameters` 符合 OpenAI function calling 的 JSON schema 规范。Planner 的 LLM-call-B 传：

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "run_python",
        "description": "在 Jupyter Kernel 中执行 Python 代码。返回 stdout 与最终表达式的值。",
        "parameters": {
          "type": "object",
          "properties": {
            "code": {"type": "string"},
            "session_id": {"type": "string"}
          },
          "required": ["code", "session_id"]
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

### 5.8 工具签名稳定性

- `name` + `parameters` 是 LLM 看的接口
- `parameters` 加可选字段 → 兼容
- `parameters` 删字段 / 改必选 → break change，需新增工具 + 废弃老工具
- `ToolSpec.tool_version` 字段记录版本
- 审计日志记录 `tool_name + tool_version`

### 5.9 风险与边界

| 风险 | 缓解 |
|---|---|
| LLM 幻觉工具（不存在的 tool_name） | DAGValidator 拦截 |
| AST 检测漏报 | Docker 沙箱兜底 |
| L2 工具绕过审批 | ToolHandler.execute 必经审批检查 |
| 工具参数 JSON 不合法 | Pydantic 在 handler 入口校验 |
| 用户拒绝卡片后 retry | 同一 execution 内不 retry 拒绝 |

---

## 6. Drive Adapter + Template Engine### 6.1 Drive Adapter 目标

1. 分片上传：`>4MB` 自动分片
2. 上传前元数据校验：`task_id` / `artifact_id` / sha256 / MIME / 大小
3. 可恢复：上传失败重试 1 次（用 `artifact_id` 去重）
4. 降级：Drive 不可用时降级为 `inline` + 落 `audit_logs`

### 6.2 文件大小策略

```
<1MB inline 文本 → 直接写到 doc（inline 块）
1MB ~ 50MB       → Drive 分片上传 → doc 里放 file 块引用
50MB ~ 500MB     → Drive 分片上传 → doc 里放 file 块引用 + drive_url
>500MB           → 拒绝执行 → task 标 failed + 错误码 FILE_TOO_LARGE
```

### 6.3 Drive Adapter 接口

```python
@dataclass
class FileMeta:
    artifact_id: str
    task_id: str
    sha256: str
    mime: str
    size: int
    local_path: str

class DriveAdapter:
    def __init__(self, parent_node_token: str, lark_cli: LarkCLI): ...
    def upload(self, meta: FileMeta) -> UploadResult: ...
    def upload_inline(self, meta: FileMeta) -> UploadResult: ...
```

Phase 2 通过 `lark-cli drive file upload --chunked`；Phase 5 切 `drive/v1/files/upload_*` OpenAPI。

### 6.4 上传前校验

`upload()` 前必经：

1. `size <= 500MB` → 否则抛 `FileTooLargeError`
2. `sha256` 已计算 → 写入 `artifacts.sha256`
3. `task_id` 对应 task 存在
4. `artifact_id` 对应 artifact 存在
5. 同一 `artifact_id` 已上传过 → 跳过，返回之前的 `file_token`（幂等去重）

### 6.5 失败处理

| 失败 | 行为 |
|---|---|
| Drive 配额超限 | `audit_logs(action=upload_drive_failed, detail={reason: "quota_exceeded"})` + 任务降级 partial_failure |
| Drive API 401/403 | 提示用户重新授权 |
| 网络超时 | 重试 1 次，仍失败 → mark_failed |
| 分片上传一半 | `drive/v1/files/upload_abort` 清理 + 标记 orphan |

### 6.6 Template Engine 目标

1. 统一入口：`TemplateEngine.render(kind, payload) -> list[BlockSpec]`
2. 块类型决策表：根据 payload 自动选 heading_2 / callout / table / image / text / divider
3. 可扩展：Phase 4 加新块类型只加新 case
4. 可测试：纯函数，输入 payload 输出 blocks

### 6.7 块类型决策表

| payload.kind | payload.shape | 输出块 |
|---|---|---|
| `text` | `<2KB` | `[text(text)]` |
| `text` | `>=2KB` | `[callout(emoji="📄", text)]` |
| `table` | `<10 行` | `[table(headers, rows)]` |
| `table` | `>=10 行` | `[callout(text_summary), file(parquet → Drive)]` |
| `image` | PNG/JPEG | `[image(file_token, alt="...")]` |
| `code` | Python stdout | `[code_block(language="python", text)]` |
| `figure` | matplotlib 输出 | `[image(file_token)]` + `[text(caption)]` |
| `error` | — | `[callout(emoji="⚠️", text, color="red")]` |

### 6.8 TemplateEngine 接口

```python
@dataclass
class BlockSpec:
    block_type: str
    content: dict

class TemplateEngine:
    def render(self, artifact: Artifact) -> list[BlockSpec]: ...
    def render_plan_summary(self, plan: DAGPlan, result: PlanResult) -> list[BlockSpec]: ...
```

设计要点：
- 渲染是纯函数（无副作用），便于测试
- `BlockSpec` 是中性表示，调用方负责转换到 lark-cli 参数
- `render_plan_summary` 让"Plan 完成后整体写文档"成为可能

### 6.9 Doc 写入编排

```
ToolHandler.execute() returns {
    "kind": "image",
    "file_token": "boxcn_xxx",
    "drive_url": "https://...",
    "caption": "箱线图（图1）",
    "artifact_id": "art_xxx",
}
 │
  ▼
TemplateEngine.render(artifact)
  │
  ▼
[BlockSpec(block_type="image", content={file_token, alt=caption})]
  │
  ▼
DocAdapter.append_blocks(doc_id, blocks)
  │
  ▼
doc_writes 表新增一行（status=success, anchor_block_id=last_block）
```

### 6.10 风险与边界

| 风险 | 缓解 |
|---|---|
| 分片上传一半被中断 | upload_abort 清理 |
| Drive 配额耗尽 | 配额监控 + 降级 + 提示用户 |
| 块大小超出飞书限制 | 截断 + 加省略号 + 写"完整内容在 artifact_id" |
| 模板渲染错误 | render 抛 RenderError → tool 标 failed |
| Drive token 过期 | 401 时触发 token refresh + 重试 1 次 |

---

## 7. Approval 流程（explicit_card）### 7.1 目标

1. 卡片 nonce + TTL：每张卡片有 30 分钟 TTL，超时自动拒绝
2. HMAC 签名：`event_token` + `app_secret` HMAC 校验通过后才执行
3. 回调入口：Gateway 新增 `/webhook/lark/card` 路由
4. 审批记录：每次审批落 `audit_logs(action=approve | deny)` 与 `executions.approval_id`
5. 拒绝传播：用户拒绝 → 当前 tool 抛 `ToolDeniedError` → 下游节点 skip → Plan success_with_partial_failure

### 7.2 审批触发矩阵

| 工具 | bind_doc 覆盖？ | 触发审批？ |
|---|---|---|
| `write_doc`（同一绑定 doc） | 是 | ❌ 跳过（30min TTL 内免审批） |
| `write_doc`（其他 doc） | 否 | ✅ 卡片 |
| `write_base_projection` | 任意 | ✅ 卡片 |
| `send_card` | 任意 | ✅ 卡片 |
| `upload_drive` | 任意 | ✅ 卡片 |

关键不变量：bind_doc **只豁免 `write_doc`** 这一个工具，且必须同 doc。

### 7.3 审批流程图

```
ToolHandler.execute(tool=write_base_projection, args, session)
 │
  ▼
检测 L2 + 无 bind_doc 覆盖
  │
  ▼
ApprovalService.request(tool_name, args_preview, actor_open_id, session_id, task_id, ttl_sec=1800)
  │
  ▼
生成 approval_id (ULID) + nonce (32B random) + expires_at
  │
  ▼
IMAdapter.send_card(chat_id, card={
    "header": "需要确认",
    "elements": [
        {"tag": "div", "text": {"tag": "lark_md", "content": "..."}},
        {"tag": "action", "actions": [
            {"tag": "button", "text": {"tag": "plain_text", "content": "确认"},
             "value": {"approval_id": ..., "action": "approve", "nonce": ...}},
            {"tag": "button", "text": {"tag": "plain_text", "content": "拒绝"},
             "value": {"approval_id": ..., "action": "deny", "nonce": ...}},
        ]},
    ],
})
  │
  ▼
audit_logs(action=request_approval)
  │
  ▼
阻塞等回调（最长 ttl_sec=1800s）
```

### 7.4 回调入口（Gateway `/webhook/lark/card`）

```
飞书 → POST /webhook/lark/card
  Body: {"action": "approve" | "deny", "approval_id": ..., "nonce": ...,
         "open_id": ..., "timestamp": ..., "sign": "..."}
  │
  ▼
Gateway.lark_card_webhook
  ├─ 1. 验签：timestamp + body + secret HMAC → 失败 401
  ├─ 2. TTL 检查：expires_at > now → 否则 410 Gone
  ├─ 3. nonce 一次性 → 重复 409
  ├─ 4. approval_id 查 executions 找到 pending 审批
  ├─ 5. 校验：approval 请求人与回调 open_id 一致
  ├─ 6. 按 action 处理：
  │     ├─ approve → executions.approval_status = approved
  │     └─ deny → executions.approval_status = denied
  └─ 7. audit_logs(action=approve | deny)
  │
  ▼
Future（创建时设的）被 resolve，ToolHandler.execute 继续
```

### 7.5 关键安全点

1. HMAC 签名：飞书提供的 `event_token` + `app_secret` SHA256 HMAC，必须校验后才信任回调
2. nonce 一次性：同一 nonce 不能 reuse，存 Redis `approval:nonce:<nonce>` TTL=1800s
3. 请求人校验：回调 `open_id` 必须等于发起审批的 `actor_open_id`
4. TTL：默认 1800s，可配置
5. 过期降级：回调时已过期 → 状态记 `expired`，plan 不等，立即走 partial_failure

### 7.6 ApprovalService 接口

```python
@dataclass
class ApprovalRequest:
    approval_id: str
    tool_name: str
    args_preview: dict
    actor_open_id: str
    session_id: str
    task_id: str
    nonce: str
    expires_at: datetime
    status: Literal["pending", "approved", "denied", "expired"]

class ApprovalService:
    def __init__(self, im_adapter: IMAdapter, audit_repo: AuditRepo, ...): ...
    async def request(self, tool_name, args_preview, actor_open_id,
                     session_id, task_id, ttl_sec=1800) -> bool:
        """发送卡片并阻塞等回调。返回 True=批准，False=拒绝/超时/异常。"""
    async def resolve_callback(self, approval_id: str, action: str,
                                open_id: str) -> None:
        """处理 Gateway 转发的回调。"""
```

Phase 2 用 `asyncio.Future` 做本地等待；Phase 5 换 Redis pub/sub。

### 7.7 卡片模板

| template_id | 用途 | 关键字段 |
|---|---|---|
| `l2_tool_confirm` | L2 工具通用审批 | tool_name, args_preview, risk_explanation |
| `execution_summary` | Plan 完成后总结 | task_id, status, artifacts_count |

Phase 2 实现 `l2_tool_confirm` 与 `execution_summary`。

### 7.8 bind_doc 与 ApprovalPolicy 协作

```
ApprovalPolicy.can_skip_approval(tool, args, session):
  ├─ tool == "write_doc" 且 args["doc_id"] == session.bound_doc_id
  │     且 session.bind_expires_at > now
  │     → True（跳过审批）
  └─ 否则 → False（走 ApprovalService.request）
```

### 7.9 风险与边界

| 风险 | 缓解 |
|---|---|
| 用户不点按钮（永远 pending） | TTL=1800s 超时自动 deny |
| 用户点了别人发起的卡片 | open_id 不一致 → 拒绝 |
| 同一 nonce 被重放 | Redis TTL + DB 唯一约束 |
| 卡片渲染失败（飞书侧） | send_card 异常 → 工具标 failed |
| 回调到时 ToolHandler 已超时 | Future 取消 + audit 标注 stale |
| 飞书回调未到但 Plan 已结束 | Future 解析时检测 plan 状态 → 忽略 |

---

## 8. PostgreSQL schema 扩展### 8.1 新增 `executions`

| 字段 | 类型 | 说明 |
|---|---|---|
| execution_id | text PK | ULID |
| task_id | text FK | |
| plan_id | text | 关联 Plan |
| node_id | text | DAG 节点 ID |
| tool_name | text nullable | kind=tool |
| tool_version | text nullable | |
| risk_level | text | L0_read / L1_compute / L2_side_effect |
| state | text | pending / running / success / failed / skipped / cancelled / denied |
| inputs_json | jsonb | 节点输入快照 |
| outputs_json | jsonb | 节点输出快照（success） |
| artifacts_ids | text[] | 产生的 artifact_id 列表 |
| approval_id | text nullable | L2 工具填 |
| error_code | text nullable | |
| error_message | text nullable | |
| started_at | datetime | |
| finished_at | datetime nullable | |

索引：`task_id`、`plan_id`、`state`、`(task_id, node_id)` 唯一约束。

### 8.2 新增 `approvals`

| 字段 | 类型 | 说明 |
|---|---|---|
| approval_id | text PK | ULID |
| task_id | text FK | |
| tool_name | text | |
| args_preview | jsonb | 卡片展示用 |
| actor_open_id | text | |
| session_id | text | |
| nonce | text unique | 一次性 HMAC nonce |
| status | text | pending / approved / denied / expired |
| expires_at | datetime | |
| resolved_by | text nullable | 实际点击人 open_id |
| created_at | datetime | |
| resolved_at | datetime nullable | |

索引：`task_id`、`nonce` unique、`status`、`expires_at`。

### 8.3 扩展 `artifacts`

Phase 1 已定义 artifacts，Phase 2 接入：

- 新增 `file_token` (text)
- 新增 `drive_url` (text)
- 新增 `mime` (text)
- 新增 `size_bytes` (int)
- 新增 `caption` (text)

### 8.4 迁移脚本

`migrations/versions/0002_phase2_executions.py`：
- create_table("executions")
- create_table("approvals")
- add_column("artifacts", "file_token" / "drive_url" / "mime" / "size_bytes" / "caption")
- downgrade 反向

---

## 9. 测试策略### 9.1 测试分层

| 层 | 范围 | 环境 | 速度 |
|---|---|---|---|
| **单元** | 纯逻辑（AST、DAG 校验、Template、Policy、approval 解析） | 内存 / mock | < 100ms |
| **集成** | 模块间（Sandbox↔Kernel、Tool↔Approval、Planner↔Executor） | mock LLM + mock Docker / 真 Docker | 1-5s |
| **端到端** | Planner → Executor → Drive → Doc 全链路 | mock LLM + 真 Docker + mock lark-cli | 5-30s |
| **合约** | OpenAI function calling schema 兼容 | jsonschema | < 1s |

### 9.2 测试矩阵

| 子模块 | 单元 | 集成 | 端到端 |
|---|---|---|---|
| Sandbox | 配置解析 | docker run / kill / inspect | — |
| Kernel | 生命周期状态机 | 真 Kernel 启停 + 简单 print | — |
| ExecutorClient | 抽象接口契约 | LocalExecutor 真实执行 | "画箱线图" |
| Planner | DAG 校验 / Scheduler 状态机 | Planner.plan() 端到端（mock LLM） | — |
| Tool Framework | AST / Registry / 各 handler | ToolHandler.execute（mock 审批） | — |
| Drive Adapter | 校验 / 幂等 / 上传 | mock lark-cli | 上传 + 写文档 |
| Template Engine | 各 kind 渲染 | render_plan_summary | — |
| Approval | HMAC / nonce / TTL / Policy | request → callback | 卡片回调链路 |

### 9.3 覆盖率目标

| 模块 | 目标 |
|---|---|
| `orchestrator/executor/*` | ≥ 75% |
| `orchestrator/planner/*` | ≥ 85% |
| `orchestrator/tools/*` | ≥ 90%（AST 必须 100%） |
| `orchestrator/approval_service.py` | ≥ 90% |
| `feishu_adapter/drive_adapter.py` | ≥ 80% |
| `orchestrator/template_engine.py` | ≥ 90% |
| **整体** | **≥ 80%** |

### 9.4 Docker 集成测试约束

- 本机 Windows 开发环境无 Docker → CI 环境跑 docker 集成测试，本地用 mock
- 集成测试在 `tests/integration/` 单独标记 `pytest.mark.docker`
- 真 Docker 测试用最小镜像（`alpine + python3.11`）减少启动开销

---

## 10. Phase 2 不做（明确边界）

| 项 | 推迟到 |
|---|---|
| GPU 节点（AlphaFold 推理） | Phase 5 |
| 联邦部署 / 多租户 Orchestrator | Phase 5 |
| 跨节点 Kernel 迁移 | Phase 5 |
| 动态 DAG 节点追加 | Phase 3 |
| DAG 多分支 / 循环（仅支持 if-else 嵌套） | Phase 3 |
| 上下文压缩 3+4（LLM 总结 + 冻结 session） | Phase 3 |
| AST P1/P2 分级 | Phase 3 |
| 输出 DLP 扫描 | Phase 5 |
| 工具热加载 | Phase 4 |
| 用户自建工具 / 工具市场 | Phase 4 |
| 领域工具 BLAST / AlphaFold 具体实现 | Phase 4 |
| 文件夹批量上传 / 分享权限管理 | Phase 4 |
| 富文本块 / 条件块 / 用户自定义模板 | Phase 4 |
| 群聊上下文级审批 / 多用户合签 | Phase 4 |
| bind_doc 续期能力 | Phase 3 |
| Kernel 状态序列化 | Phase 3 评估 |
| 实时屏幕回放 | 永不 |
| `>500MB` 文件 | 永不（架构上限） |
| 多语言 Kernel | Phase 5 |

---

## 11. 风险与决策### 11.1 风险登记

| 风险 | 等级 | 缓解 | 触发升级条件 |
|---|---|---|---|
| Docker daemon 不可用 | 高 | Phase 2 mock + CI 真 Docker | Windows 团队普遍无法跑集成测试 |
| 飞书 lark-cli 升级 break change | 中 | Phase 2.5 评估切 SDK；Phase 5 切 OpenAPI | lark-cli 出现 break change |
| Kernel 内存泄漏 | 中 | 资源限额 + 30min 空闲清理 + OOM killer | 单用户连续执行 10 次后 OOM |
| LLM function calling 跨厂商差异 | 中 | ToolSpec.parameters 通用 JSON schema，转换层在 LLMRouter | 新厂商接入时 schema 不兼容 |
| 卡片回调延迟 | 中 | TTL=1800s；可配置 | 用户反馈审批太慢 |
| DAG JSON 巨大 | 低 | orjson + gzip + plan_json 压缩存储 | 单 Plan > 100KB |
| AST 误报 | 低 | 仅 P0 严格匹配；提供白名单豁免机制 | 真实场景误报率 > 5% |
| Drive 配额耗尽 | 中 | 配额监控 + 降级 partial_failure + 提示用户 | 单一租户 Drive < 1GB |

### 11.2 关键决策（ADR 候选）

| 决策 | 备选 | Phase 2 选择 | 理由 |
|---|---|---|---|
| Sandbox 实现 | InProcess / Docker / gRPC | Docker（LocalExecutor） | Phase 2 不做分布式；Phase 5 切 gRPC 时仅替换实现 |
| Kernel 通信 | TCP / stdio / ZMQ | TCP | Jupyter wire protocol 标准 |
| DAG 调度 | 全异步事件驱动 / 协程轮询 | 协程轮询 + asyncio.Event | Phase 2 单 Plan 节点数有限（< 20） |
| 审批等待 | Future / Redis pub/sub | asyncio.Future | Phase 2 单进程；Phase 5 切 Redis |
| 卡片渲染 | 飞书原生 / 自定义 | 飞书原生 + 内置模板 | 飞书卡片 schema 成熟 |
| 工具注册 | 装饰器 / YAML / JSON | 装饰器 + Registry | Pythonic；Phase 4 引入 YAML |
| ToolSpec 兼容性 | 单版本 / 多版本 | 多版本（tool_version 字段） | LLM 调用工具签名稳定是核心契约 |
| DAG 失败默认 | stop_plan / continue | continue | 用户能看到部分结果 |

---

## 12. 后续动作

1. 按本设计稿写实施计划 [`../plans/2026-08-08-feishu-research-agent-phase2.md`](../plans/2026-08-08-feishu-research-agent-phase2.md)（调用 writing-plans skill）
2. 实施前补 ADR：
   - 为什么 Sandbox 选 Docker 而不是 InProcess
   - 为什么 AST 仅保留 P0
   - 为什么 bind_doc 保留不废弃
   - 为什么 default_on_node_fail=continue
3. Phase 2 验收后启动 Phase 3（动态 DAG / 条件分支 / 上下文压缩 3+4）