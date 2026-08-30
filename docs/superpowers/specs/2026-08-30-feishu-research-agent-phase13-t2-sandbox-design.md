# Phase 13 · T2 沙箱执行（run_python 容器内真实执行）设计

日期：2026-08-30 ｜ 状态：已完成（自动化 + 真机验收均通过） ｜ 前置：Phase 12 T1 已全绿

## 1. 范围与目标

打通 run_python 的真实容器执行链路：

```
Planner 规划 run_python 节点
  → ToolHandler AST 预检（已有）
  → run_python handler（真实现，替换 stub）
  → KernelPool.exec_code（新增 exec 通道）
  → DockerSandbox（已有容器生命周期 + 扩 input 透传）
  → docker exec 容器内 python 跑用户代码
  → stdout/result 回传 → 下游节点引用
```

**决策**（已确认）：
- 执行协议：**docker exec 直执行**——偏离 Phase 2 设计的 Jupyter TCP kernel 方案（ADR 记档）。安全边界完全一致（network none / read-only / cap-drop / 资源限额），无 zmq/jupyter_client 依赖，实现量小一个量级
- 镜像：**python:3.11-slim + numpy/pandas/matplotlib**（pip 走清华源）
- 本轮只做 run_python；run_blast 维持网络版 blast_search（本地 BLAST 需数十 GB 数据库，性价比低）

**不做**：富输出（image/HTML mime）、kernel 状态持久化、idle_sweep 周期调度（方法已有，调度挂载后续）、workspace 主机挂载与产物回收（tmpfs 方案够用）、网络白名单（network none 即断网）。

## 2. 板块① kernel 镜像

新建 `sandbox/kernel.Dockerfile`：

```dockerfile
FROM python:3.11-slim
# 清华源装数据科学基础包（numpy/pandas/matplotlib）
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    numpy pandas matplotlib
# 非 root（docker run -u 1000:1000 配套）+ workspace 目录
RUN useradd -u 1000 -m kernel && mkdir -p /workspace /tmp/mpl \
    && chown -R kernel /workspace /tmp/mpl
ENV MPLCONFIGDIR=/tmp/mpl PYTHONUNBUFFERED=1
WORKDIR /workspace
# 常驻等待 exec（docker run 无 CMD override，靠镜像默认命令保活）
CMD ["sleep", "infinity"]
# 执行 harness（板块②）固化进镜像
COPY run_user.py /opt/run_user.py
```

构建命令（文档化 + 验证步骤）：`docker build -t feishu-research-agent/kernel:latest sandbox -f sandbox/kernel.Dockerfile`

## 3. 板块② exec 通道（核心）

### 3.1 harness `sandbox/run_user.py`（固化进镜像）

职责：执行用户代码文件，产出**可解析的 stdout + 末表达式值**：

1. `ast.parse` 用户代码；顶层最后一个 `Expr` 语句单独 `eval`，其余 `exec`（stdout 重定向捕获）
2. 先打印捕获的 stdout，再打 sentinel `\n###RESULT###\n` + `repr(末表达式值)`
3. 用户异常：先冲刷已捕获 stdout 再让异常自然抛出（退出码非 0，traceback 落 stderr）

代码经 stdin 写入容器（`docker exec -i <c> sh -c 'cat > /tmp/r_<ulid>.py'`），**零转义问题**；tmpfs 随容器销毁，无需清理。

### 3.2 `DockerSandbox.exec` 扩展

加 `input_bytes: bytes | None = None` 透传 `subprocess.run(..., input=)`（其余不变）。

### 3.3 `KernelPool.exec_code(session_id, code, timeout_sec=60)`

```python
handle = self.acquire(session_id)          # 复用或新起容器
path = f"/tmp/r_{new_ulid().lower()}.py"
self._sandbox.exec([... "cat > {path}" ...], input_bytes=code.encode(), timeout_sec=15)
proc = self._sandbox.exec([... "python /opt/run_user.py {path}" ...], timeout_sec=timeout_sec)
```

- 步骤失败/容器起不来 → `SandboxUnavailableError`（已有错误类型）
- `subprocess.TimeoutExpired` → 抛 `SandboxTimeoutError`（新增，code=`SANDBOX_TIMEOUT`）
- 解析 sentinel：`###RESULT###` 前=stdout、后=result（repr 字符串）；退出码非 0 → 结果带 stderr 尾部

### 3.4 超时语义（含已知局限）

`kernel_exec_timeout_sec`（默认 60s）作为 exec 调用的 subprocess 超时。客户端超时即断 docker exec 会话；容器内极端残留进程由容器级生命周期兜底（kill 容器即清），死循环不阻塞宿主。记档局限：单次超时后该 session 容器**直接 release 重建**，防止残留进程污染后续执行。

## 4. 板块③ run_python 真实现 + 接线

- `l1_compute.py`：`_make_run_python_handler(kernel_pool, default_timeout)` 工厂替换 stub；错误映射 `SANDBOX_UNAVAILABLE`/`SANDBOX_TIMEOUT`/`PY_RUNTIME_ERROR`（stderr 尾部 300 字），成功返回 `{"stdout", "result"}`（对齐 stub 契约 + 下游可引用）
- **session_id 缺省 `"research"`**：模型不会规划 session_id，研究任务内共享一个容器（池复用）
- `visible_to_planner=True` + description 声明输出：`在隔离沙箱执行 Python（numpy/pandas/matplotlib 可用）；输出 stdout 与 result（末表达式值），下游用 <node_id>.result 引用`
- `app.py`：`register_l1_compute(..., kernel_manager=self.kernel_pool)`（现为 None；注意装配顺序——kernel_pool 需先于工具注册创建）
- LocalExecutor 的 T1 降级探测逻辑保留不动（真实镜像存在后 acquire 即成功，路径自然切换）

## 5. 板块④ 配置外化

`load_settings()` 补读：`DOCKER_IMAGE` / `DOCKER_CPU_LIMIT` / `DOCKER_MEMORY_LIMIT` / `DOCKER_PIDS_LIMIT` / `DOCKER_NETWORK_MODE` / `KERNEL_IDLE_TIMEOUT_SEC` / `KERNEL_EXEC_TIMEOUT_SEC`；`.env.example` 与联调指南同步（含构建镜像命令与验证步骤）。

## 6. 测试策略

| 层 | 用例 |
|---|---|
| 单测（mock sandbox） | sentinel 解析（stdout/result 切分）；TimeoutExpired→SANDBOX_TIMEOUT；exec 失败→SANDBOX_UNAVAILABLE；退出码非 0→PY_RUNTIME_ERROR 带 stderr；run_python 对 Planner 可见；DockerSandbox input 透传 |
| 单测（harness 本体） | 本机直接跑 `run_user.py`：print+末表达式 / 无末表达式 / 异常路径 |
| 集成（skipif 无 docker 或无镜像） | 真容器：`print`+计算 roundtrip；sleep 超时映射 |
| 真机 | ① 构建 → ② `/research 用 python 计算 2 的 100 次方`（run_python success + result 展示）→ ③ 恶意代码 `os.system(...)` 被 AST 拒（单测已覆盖，真机可选） |

## 7. 验收标准

- 真机 run_python 节点 success，result 出现在关键输出并写入绑定文档
- 超时/异常/沙箱不可用三类错误路径单测全过（不裸抛）
- 全量测试通过 + ruff clean；镜像构建命令文档化

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Windows Docker Desktop 未启动/慢 | 构建前 `docker info` 预检；SandboxUnavailableError 优雅降级为节点失败（带失败详情） |
| 首次拉基础镜像慢（清华 pip 源已定） | 文档标注一次性成本；镜像 ~400MB |
| matplotlib 字体/缓存写入只读文件系统 | MPLCONFIGDIR=/tmp/mpl（tmpfs） |
| exec 客户端超时后容器内残留 | 超时即 release 重建该 session 容器（§3.4） |
| 模型规划危险代码 | ASTGuard P0 硬拦截已有 + 容器层兜底（network none/read-only/非 root） |

## 9. 实施结果（2026-08-30）

四板块全部落地（commits `5120650`/`68ecaf8`/本轮收尾）：

| 板块 | 结果 |
|---|---|
| ① 镜像 | `sandbox/kernel.Dockerfile` + `sandbox/run_user.py` harness；构建成功（python:3.11-slim + numpy 2.4.6/pandas/matplotlib，清华 pip 源） |
| ② exec 通道 | `KernelPool.exec_code`（stdin 零转义写码 → `/opt/run_user.py` → sentinel 切分）；超时→release 重建容器 |
| ③ run_python | 真实现替换 stub，app 装配顺序调整（sandbox 先于工具注册），Planner 可见 + 描述声明输出 |
| ④ 配置外化 | `DOCKER_*`/`KERNEL_*` 环境变量入 settings；.env.example + 联调指南 6d 冒烟行 |

**验收**：全量 **635 passed + ruff clean**（含 4 个真容器集成测试真跑：roundtrip / numpy / PY_RUNTIME_ERROR / 超时重建复用，13.67s）。

### 真机验收 ✅（2026-08-30，两轮）

`/research 用 python 计算 2 的 100 次方并说明位数`：

| 轮 | 结果 | 说明 |
|---|---|---|
| 1 | ❌ result=None 假 success | 暴露 scheduler 引用误判 bug（见下），修复 `4858d12` |
| 2 | ✅ stdout 完整输出 `2^100 = 1267650600228229401496703205376`，位数 31（len/log10 双验证） | run_python 真实沙箱执行打通 |

注：轮 2 `[n1.result] None` 为协议正常语义——模型该轮代码全用 print（末行无表达式），result 承载于 stdout；轮 1 模型末行有 dict 表达式则 result 有值。两种风格均为 success。

### 关键发现②：含点字符串被误判为节点引用（真机 result=None 根因）

**链条**：code 含 `.`（如 `{float(v):.6e}`）→ Scheduler `_resolve_inputs` 把整段 code 拆成 `<node>.<field>` 引用 → `.` 前非已知 node_id → code 置 None → `exec_code` 的 `input_text=None` → docker exec 不带 `-i`、stdin 空 → 容器 0 字节文件执行 rc=0 → **假 success**（现场证据：容器 `/tmp/r_xxx.py` 大小 0）。

**修复**（`4858d12`）：① 引用判定要求 `.` 前是计划内 node_id，否则字面值透传；② `exec_code` 空/None code 直接返回 `INVALID_INPUT`（不再碰沙箱，同类问题显式失败）；③ `sandbox.stop` kill 后必 rm（容器不再残留）。诊断中证伪了 GBK 编码、`_run_subprocess` stdin、ASTGuard 改写三个假设。

### 关键发现①：Windows subprocess 超时管道挂死（集成测试卡死根因）

**现象**：真容器集成测试卡死 8 分钟（容器 Up 但 pytest 无进展）；手动 PowerShell 管道与 `subprocess.run(input=)` 写码通道均正常——唯独 `while True` 超时用例必挂。

**根因**：`subprocess.run(timeout=N)` 超时后仅 `kill()` docker.exe 本体；Windows 下 docker.exe 派生的子进程仍持有 stdout/stderr 管道句柄，`communicate()` 等管道 EOF **永不到达**。诊断脚本复现确认（timeout=3 后 15s 无返回）；容器仍 Up 也佐证 `release` 从未执行到。

**修复**（`sandbox.py`）：默认 subprocess 换成 `_run_subprocess`——`Popen` + 超时后 `taskkill /F /T /PID` 杀整棵进程树再回收输出、重抛 `TimeoutExpired`（POSIX 走 `proc.kill()`）。修复后超时用例 5s 准时返回，release→重建→复用链路正常。

**适配**：镜像内 numpy 2.x 标量 `repr` 为 `np.int64(10)`（1.x 为 `10`），集成测试断言兼容两种。
