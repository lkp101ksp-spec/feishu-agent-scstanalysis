# Phase 16 设计：安全加固轮（沙箱生命周期 + 工具 ACL）

- 状态：设计中
- 日期：2026-09-01
- 前置：Phase 15 已实施；Phase 13 T2 沙箱已上线（network=none / tmpfs / 容器即删）

## 1. 调研结论：原范围两项已被现状覆盖（无改动）

ROADMAP 初版四项中两项经代码核实**已经安全**，本轮明确不做并记录依据：

| 原计划项 | 现状核实 | 结论 |
|---|---|---|
| 沙箱网络白名单 | `docker_network_mode="none"` 默认断网（[sandbox.py](../../../orchestrator/executor/sandbox.py) L95/L54-99）；ast_guard P0 硬拦 socket/subprocess/ctypes，P1 标记 requests/urllib/httpx | **不做**。none 比白名单更严格；需要网络的研究走宿主侧 blast_search 工具而非沙箱内直连。引入白名单 = 引入放行面，无需求不动 |
| workspace 产物回收 | workspace 为容器内 tmpfs（512MB，sandbox.py L95），容器 stop 即 `rm -f`（L101-122），无主机挂载 | **不做**。容器删除即产物消失，无残留路径 |

## 2. 真实缺口

| # | 缺口 | 现状 | 风险 |
|---|---|---|---|
| 1 | idle_sweep 从未被周期调度 | `KernelPool.idle_sweep()` 已实现（kernel_manager.py L95，1800s 空闲超时）且测试覆盖，但**生产代码零调用**（仅 tests 引用） | 会话结束/任务超时后容器静置，最长永不过期：宿主容器与 512MB tmpfs 无限累积 |
| 2 | 工具无 ACL | ToolRegistry 有 risk_level 分级，planner schema 过滤 L2（app.py L315 / research_runner.py L157），但**无禁用机制**：任何已注册工具对 planner 全可见、handler 执行侧无二次校验 | 无法按环境关停单个工具（如临时下线 blast_search 排障）；schema 过滤是唯一防线，直调/热加载可绕过 |

## 3. 设计

### 3.1 T1：kernel 空闲清扫守护线程

镜像续期扫描线程模式（ws_client.py `start_renew_scanner`）：

```python
# gateway/ws_client.py
def start_kernel_idle_sweeper(kernel_pool, interval_sec: int = 300):
    """启动沙箱容器空闲清扫守护线程：每 interval 秒调 idle_sweep 一次。"""
    # pool None（引擎未装配）→ 返回 None；daemon 线程 + 异常吃掉保线程
```

- `ws_client.main()` 启动处加一行（与 renew/auto_sync 扫描线程并列）
- Settings 新增 `kernel_sweep_interval_sec: int = 300`（env `KERNEL_SWEEP_INTERVAL_SEC`）；0 关闭线程
- `idle_sweep()` 本体不改（已有实现与测试）

### 3.2 T2：工具 ACL（禁用名单，双层执行）

Settings 新增：

```python
disabled_tools: str = ""   # env DISABLED_TOOLS，逗号分隔，如 "blast_search,run_python"
```

双层执行（defense in depth）：

1. **Planner 可见层**：`orchestrator/app.py` 与 `research_runner.py` 构造 planner schema 处，过滤 `disabled_tools`（LLM 看不到禁用工具，不会规划调用）
2. **执行层**：`ToolHandler.execute()` 开头查禁用名单，命中返回 `TOOL_DISABLED`（防直调/未来热加载绕过 schema 层）

解析工具函数 `_parse_disabled_tools(s: str) -> set[str]`（strip + 去空项）放 shared 或 ToolHandler 内；两处共用。

> 不做 per-user 粒度：单用户产品，环境级名单已满足「临时下线某工具」的真实场景；出现多用户再扩展。

### 3.3 非目标

- 网络白名单 / workspace 回收（见 §1）
- per-user RBAC、审批策略引擎
- `requires_approval` 字段激活（属 Phase 17 节点级审批范畴）

## 4. 测试计划

单测：
- `test_ws_client`：sweeper 线程启动/None pool 返回 None/异常不断线程
- `test_tool_handler`：禁用工具执行 → TOOL_DISABLED；未禁用正常执行
- `test_research_runner` / `test_app`：planner schema 不含禁用工具名
- settings 解析：逗号分隔串 → set

回归：全量用例；`DISABLED_TOOLS` 缺省空 → 行为与现在完全一致。

真机验收（延后批量）：设 `DISABLED_TOOLS=blast_search` 重启，/research 任务中 planner 不再出现 blast_search 引用。

## 5. 风险

- sweeper 线程与 exec_code 并发 release 同一容器：idle_sweep 内部已有锁语义（实现时确认 KernelPool 线程安全），必要时加锁
- 低：禁用名单仅影响新规划，进行中任务不受影响（可接受）
