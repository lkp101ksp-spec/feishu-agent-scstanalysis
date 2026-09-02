# Phase 22 运维加固轮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 六项运维加固——ws_client pidfile 防双实例、bind-doc 存在性校验、lifespan+ConfigDict 弃用清理、qc 工具小数据参数指导、st 镜像 torch CPU 瘦身、ROADMAP 对齐 P21。

**Architecture:** 全部为小改动集合，无新组件。守卫/校验走 TDD；镜像瘦身走重建+冒烟回归；ROADMAP 纯文档。spec：`docs/superpowers/specs/2026-09-02-feishu-research-agent-phase22-ops-hardening-design.md`。

**Tech Stack:** ctypes（Windows 探活，零新依赖）、FastAPI lifespan、Pydantic ConfigDict、tuna pytorch-wheels。

## File Structure

- Modify: `gateway/ws_client.py`（守卫函数 + main 集成 + --force）
- Modify: `orchestrator/bind_doc_service.py`（bind 探活）
- Modify: `gateway/app.py`（on_event → lifespan）、`orchestrator/tools/tool_registry.py`（Config → ConfigDict）
- Modify: `orchestrator/tools/builtin/l3_singlecell.py`、`l3_spatial.py`（qc description）
- Modify: `sandbox/st.Dockerfile`（torch CPU 固定版本）、`.gitignore`（.ws_client.pid）
- Modify: `docs/ROADMAP.md`
- Test: `tests/unit/test_ws_client_pidfile.py`（新建）、`tests/unit/test_bind_doc_service.py`（追加）

---

### Task 1: ws_client pidfile 单实例守卫（TDD）

**Files:**
- Modify: `gateway/ws_client.py`、`.gitignore`
- Test: `tests/unit/test_ws_client_pidfile.py`（新建）

- [ ] **Step 1: 写失败测试（新建文件）**

```python
"""ws_client pidfile 单实例守卫单测（Phase 22 T1）。

_pid_alive/acquire 均可注入：monkeypatch gateway.ws_client 模块的
_PIDFILE（tmp_path）与 _pid_alive（免真进程）。
"""
from __future__ import annotations

import pytest

import gateway.ws_client as wsc


@pytest.fixture
def pidfile(tmp_path, monkeypatch):
    """隔离 pidfile 到 tmp_path，_pid_alive 默认报死（stale）。"""
    p = tmp_path / ".ws_client.pid"
    monkeypatch.setattr(wsc, "_PIDFILE", p)
    monkeypatch.setattr(wsc, "_pid_alive", lambda pid: False)
    return p


def test_first_start_writes_pid(pidfile):
    """无 pidfile 首启：写入当前 pid。"""
    wsc.acquire_single_instance()
    assert pidfile.read_text().strip() == str(wsc.os.getpid())


def test_alive_instance_rejects(pidfile, monkeypatch):
    """活实例：默认拒绝启动（SystemExit 1），pidfile 不被覆盖。"""
    pidfile.write_text("12345")
    monkeypatch.setattr(wsc, "_pid_alive", lambda pid: True)
    with pytest.raises(SystemExit) as ei:
        wsc.acquire_single_instance()
    assert ei.value.code == 1
    assert pidfile.read_text().strip() == "12345"


def test_stale_pidfile_takeover(pidfile):
    """stale（进程已死）：警告并接管覆写。"""
    pidfile.write_text("12345")
    wsc.acquire_single_instance()
    assert pidfile.read_text().strip() == str(wsc.os.getpid())


def test_force_terminates_and_takes_over(pidfile, monkeypatch):
    """--force：杀旧（mock _terminate）后接管。"""
    pidfile.write_text("12345")
    monkeypatch.setattr(wsc, "_pid_alive", lambda pid: True)
    killed = []
    monkeypatch.setattr(wsc, "_terminate", lambda pid: killed.append(pid))
    wsc.acquire_single_instance(force=True)
    assert killed == [12345]
    assert pidfile.read_text().strip() == str(wsc.os.getpid())


def test_release_only_removes_own_pidfile(pidfile):
    """atexit 清理：pidfile 已被接管者覆写时不误删。"""
    wsc.acquire_single_instance()
    pidfile.write_text("99999")  # 模拟被接管
    wsc._release_pidfile()
    assert pidfile.exists()
    pidfile.write_text(str(wsc.os.getpid()))
    wsc._release_pidfile()
    assert not pidfile.exists()
```

- [ ] **Step 2: 红**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_ws_client_pidfile.py -q --basetemp=.pytest_tmp`
Expected: FAIL（AttributeError: acquire_single_instance 不存在）

- [ ] **Step 3: 实现（gateway/ws_client.py，import 区与 main 附近）**

模块 import 区追加：

```python
import atexit
import ctypes
import os
import time
```

（os/time 若已 import 则跳过；`from pathlib import Path` 若无则补）

在 `def main()` 之前追加：

```python
_PIDFILE = Path(__file__).resolve().parent.parent / ".ws_client.pid"
_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ERROR_INVALID_PARAMETER = 87


def _pid_alive(pid: int) -> bool:
    """Windows 探活：OpenProcess 可打开即活着。

    仅探 pid 存活不校验 cmdline——pid 复用误判概率低（守卫目标是防
    人为双启，不是安全边界）；GetLastError==87（INVALID_PARAMETER）
    意味 pid 不存在判死，其余失败（权限等）一律按活着保守处理。
    """
    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = _KERNEL32.OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ctypes.get_last_error() != _ERROR_INVALID_PARAMETER
    _KERNEL32.CloseHandle(h)
    return True


def _terminate(pid: int) -> None:
    """--force 杀旧进程并等待退出（0.5s 轮询，上限 5s）。"""
    h = _KERNEL32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if h:
        _KERNEL32.TerminateProcess(h, 1)
        _KERNEL32.CloseHandle(h)
    for _ in range(10):
        if not _pid_alive(pid):
            return
        time.sleep(0.5)


def _release_pidfile() -> None:
    """退出清理：仅当 pidfile 内容仍是本进程 pid（不误删接管者）。"""
    try:
        if _PIDFILE.read_text().strip() == str(os.getpid()):
            _PIDFILE.unlink(missing_ok=True)
    except OSError:
        pass


def acquire_single_instance(force: bool = False) -> None:
    """单实例守卫（Phase 22）：活实例拒绝（--force 杀旧接管），stale 接管。"""
    if _PIDFILE.exists():
        try:
            old_pid = int(_PIDFILE.read_text().strip())
        except (ValueError, OSError):
            old_pid = 0
        if old_pid and old_pid != os.getpid() and _pid_alive(old_pid):
            if not force:
                logger.error(
                    "ws_client already running (pid=%s); "
                    "use --force to replace", old_pid)
                raise SystemExit(1)
            logger.warning("--force: terminating old ws_client (pid=%s)",
                           old_pid)
            _terminate(old_pid)
        elif old_pid != os.getpid():
            logger.warning("stale pidfile (pid=%s) -> take over", old_pid)
    _PIDFILE.write_text(str(os.getpid()))
    atexit.register(_release_pidfile)
```

`main()` 的 `logging.basicConfig(...)` 之后、`build_runtime()` 之前插入：

```python
    # Phase 22：单实例守卫（四轮真机双实例复发；--force 显式替换）
    acquire_single_instance(force="--force" in sys.argv)
```

（`import sys` 若无则补）

`.gitignore` 追加一行：`.ws_client.pid`

- [ ] **Step 4: 绿 + 既有 ws_client 测试回归**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_ws_client_pidfile.py tests/unit -k "ws_client" -q --basetemp=.pytest_tmp`
Expected: 5 passed + 既有 ws_client 相关用例无回归

- [ ] **Step 5: Commit**

```bash
git add gateway/ws_client.py tests/unit/test_ws_client_pidfile.py .gitignore
git commit -m "feat(phase22): ws_client pidfile 单实例守卫（保守+--force，TDD）"
```

---

### Task 2: bind-doc 存在性校验（TDD）

**Files:**
- Modify: `orchestrator/bind_doc_service.py:59-65`
- Test: `tests/unit/test_bind_doc_service.py`（追加 3 用例）

- [ ] **Step 1: 追加失败测试**

```python
def test_bind_doc_rejects_missing_document(fake_deps, doc_adapter_probe):
    """doc_adapter 探活失败（文档不存在）→ BindDocInvalidError 含 not found。

    nzb/nkb 一字之差真机踩坑（P15）：绑定时发现远早于写回时。
    """
    svc = make_service(fake_deps, doc_adapter=doc_adapter_probe)
    doc_adapter_probe.list_root_children.side_effect = \
        RuntimeError("1770002 not found")
    with pytest.raises(BindDocInvalidError, match="not found"):
        svc.bind(session_id="s1", owner_open_id="u1",
                 doc_id="L9AXd9xmdoZuSgx75mccKB82")


def test_bind_doc_passes_when_document_exists(fake_deps, doc_adapter_probe):
    """文档存在 → 绑定照常（探活调用过一次）。"""
    svc = make_service(fake_deps, doc_adapter=doc_adapter_probe)
    expires = svc.bind(session_id="s1", owner_open_id="u1",
                       doc_id="L9AXd9xmdoZuSgx75mccKB82")
    assert expires is not None
    assert doc_adapter_probe.list_root_children.call_count == 1


def test_bind_doc_skips_probe_without_adapter(fake_deps):
    """doc_adapter 未配置（纯单测环境）→ 跳过探活保持现状。"""
    svc = make_service(fake_deps, doc_adapter=None)
    expires = svc.bind(session_id="s1", owner_open_id="u1",
                       doc_id="L9AXd9xmdoZuSgx75mccKB82")
    assert expires is not None
```

（fixture `doc_adapter_probe` 为 MagicMock(spec=["list_root_children",
"resolve_wiki_token"])；`make_service`/`fake_deps` 沿用文件内既有构造方式——以现有测试文件的 fixture 为准对齐命名）

- [ ] **Step 2: 红** → 3 failed（当前无探活，missing 用例不抛）

- [ ] **Step 3: 实现（bind_doc_service.py 的 bind()，正则校验后插入）**

```python
        if not doc_id or not _DOC_ID_RE.match(doc_id):
            raise BindDocInvalidError(f"invalid doc_id: {doc_id!r}")

        # 存在性探活（Phase 22）：doc_adapter 可用即校验——绑定时发现
        # 远早于写回时（P15 真机 nzb/nkb 一字之差 1770002 踩坑）
        if self.doc_adapter is not None:
            try:
                self.doc_adapter.list_root_children(doc_id)
            except Exception as e:
                raise BindDocInvalidError(
                    f"document not found: {doc_id}"
                    "（请检查 doc_id 或链接是否正确）") from e
```

- [ ] **Step 4: 绿** + 既有 bind 用例回归
- [ ] **Step 5: Commit**

```bash
git add orchestrator/bind_doc_service.py tests/unit/test_bind_doc_service.py
git commit -m "feat(phase22): bind-doc 存在性探活（list_root_children，TDD）"
```

---

### Task 3: lifespan 迁移 + ConfigDict + qc description（小改集合）

**Files:**
- Modify: `gateway/app.py:278,313-317`、`orchestrator/tools/tool_registry.py`
- Modify: `orchestrator/tools/builtin/l3_singlecell.py`、`l3_spatial.py`

- [ ] **Step 1: gateway/app.py on_event → lifespan**

读 `create_app`（FastAPI 构造在 278 行附近，on_event("startup") 在 315 附近）。改法：

```python
    from contextlib import asynccontextmanager

    # Phase 22：on_event 弃用 → lifespan（行为等价迁移）
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        if auto_sync_worker is not None:
            auto_sync_worker.start_async()
        yield

    app = FastAPI(title="Feishu Research Agent — Phase 1",
                  lifespan=_lifespan)
```

删除原 `@app.on_event("startup")` 块（313-317）。`from contextlib import asynccontextmanager` 提到文件 import 区。验证：启动日志 auto_sync worker 正常 start、全量 gateway 测试回归。

- [ ] **Step 2: tool_registry class Config → ConfigDict**

Run 先定位：在 `orchestrator/tools/tool_registry.py` grep `class Config`（PydanticDeprecatedSince20 警告源，26 行附近 class ToolSpec 之后的内嵌 Config）。改法：

```python
from pydantic import BaseModel, ConfigDict

class ToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)  # 原内嵌 Config 的字段等价搬移
```

（原 Config 内的字段逐项搬到 ConfigDict——以实际读到的内嵌内容为准，禁止丢字段）

- [ ] **Step 3: sc_qc/st_qc description 追加小数据指导**

`l3_singlecell.py` 的 sc_qc 与 `l3_spatial.py` 的 st_qc ToolSpec description 各追加：

```
注意：小规模/测试数据每细胞（spot）基因数可能仅几十，min_genes 过大会
全滤光——若失败，错误消息含 genes/cell 分布（median/p90/max），请按
median 以下调低 min_genes 重试。
```

- [ ] **Step 4: 回归验证**

Run: `.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp --tb=short -p no:cacheprovider`
Expected: 全 passed 且 warnings 从 8 降（FastAPI on_event 与 Pydantic Config 警告消失）

- [ ] **Step 5: Commit**

```bash
git add gateway/app.py orchestrator/tools/tool_registry.py orchestrator/tools/builtin/l3_singlecell.py orchestrator/tools/builtin/l3_spatial.py
git commit -m "refactor(phase22): on_event→lifespan + ConfigDict + qc 小数据参数指导"
```

---

### Task 4: st 镜像 torch CPU 瘦身 + 冒烟回归

**Files:**
- Modify: `sandbox/st.Dockerfile`

- [ ] **Step 1: 查 tuna pytorch-wheels/cpu 可用 cp312 版本**

Run: `Invoke-WebRequest -UseBasicParsing https://mirrors.tuna.tsinghua.edu.cn/pytorch-wheels/cpu/ -TimeoutSec 30`（或直接 `pip index` / 浏览器），找 `torch-2.x.y+cpu-cp312-...whl` 的最新 2.x 版本号 `<VER>`。若目录无 cp312 → 记录到 Dockerfile 注释并跳过 Step 2-3（保留 CUDA 现状，本任务提前完成）。

- [ ] **Step 2: 改 torch 层固定 CPU 版本**

```dockerfile
# torch CPU wheel（Phase 22 瘦身：固定 +cpu 版本，镜像 7.17GB→~2.5GB）
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -f https://mirrors.tuna.tsinghua.edu.cn/pytorch-wheels/cpu/ \
    "torch==<VER>+cpu"
```

（`<VER>` 换 Step 1 查到的版本；删掉原 `|| pip install ... torch` 回退——失败即构建失败，暴露问题优于静默 5GB 膨胀）

- [ ] **Step 3: 重建 + 验证**

Run:
```powershell
docker build -t feishu-research-agent/bio:st-cpu-latest sandbox -f sandbox/st.Dockerfile
docker images feishu-research-agent/bio:st-cpu-latest
docker run --rm feishu-research-agent/bio:st-cpu-latest python -c "import torch, cell2location; print(torch.__version__, torch.cuda.is_available(), cell2location.__version__)"
```
Expected: 镜像 ~2.5GB；torch `<VER>+cpu` False；cell2location import 正常。

- [ ] **Step 4: deconvolve 冒烟步回归（全链 8 步）**

Run:
```powershell
Remove-Item -Recurse -Force I:\飞书agent\bio_workspace\smoke_st -ErrorAction SilentlyContinue
.venv\Scripts\python.exe scripts\smoke_st_chain.py I:\飞书agent\bio_test_data\tiny_visium
```
Expected: SMOKE PASS；deconvolve 耗时 356s 量级（CPU wheel 数值性能同源，允许 ±50%）。

- [ ] **Step 5: Commit**

```bash
git add sandbox/st.Dockerfile
git commit -m "build(phase22): st 镜像 torch 固定 CPU wheel（7.17GB→~2.5GB）"
```

---

### Task 5: ROADMAP 更新 + 全量回归 + 真机双实例验证

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: ROADMAP 更新**

- Phase 20 后续扩展：勾选 st_*（注"Phase 21 完成"）
- 新增 Phase 21 收官节（三批 21 任务、13 个 bio 工具、真机三批验收）
- 持续项：勾掉 bind-doc 存在性校验、双 ws_client 防复发、lifespan 迁移（标注 Phase 22 完成）；补 st 镜像瘦身已完成
- 变更记录追加：`| 2026-09-02 | Phase 21 空间转录组三批收官 + Phase 22 运维加固轮（pidfile/bind 校验/lifespan/瘦身） |`

- [ ] **Step 2: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp`
Expected: 全 passed（803 + 本轮新增 ≥8）

- [ ] **Step 3: 真机双实例验证（守护进程行为）**

当前 ws_client 在跑（pidfile 已写）。验证：
1. 第二实例：`.venv\Scripts\python.exe -m gateway.ws_client` → 预期秒退 + 日志 `already running (pid=N)`（exit 1）
2. --force 接管：`.venv\Scripts\python.exe -m gateway.ws_client --force`（后台）→ 杀旧起新，Lark reconnect 正常
3. 最终留单实例运行

- [ ] **Step 4: 更新测试总结 + 收尾 Commit**

```bash
git add docs/ROADMAP.md 测试总结*.md
git commit -m "docs(phase22): ROADMAP 对齐 P21 + 运维加固轮收官"
```

---

## Self-Review 结论

- **Spec 覆盖**：§1 pidfile（T1）、§2 bind 探活（T2）、§3 lifespan（T3 Step1；ConfigDict 为顺手的同类弃用清理）、§4 description（T3 Step3）、§5 瘦身（T4，含无 cp312 降级路径）、§6 ROADMAP（T5）、§7 验收（T3 Step4/T5 全量+双实例真机）。
- **占位符**：`<VER>` 是运行时查得的版本号（Step 1 明确来源），非设计占位；fixture 命名注明"以现有文件为准对齐"。
- **类型一致性**：`acquire_single_instance(force=)`/`_pid_alive`/`_terminate`/`_release_pidfile` 测试与实现签名一致；`list_root_children` 与 DocAdapter 实际方法名一致（feishu_adapter/doc_adapter.py:189）。
