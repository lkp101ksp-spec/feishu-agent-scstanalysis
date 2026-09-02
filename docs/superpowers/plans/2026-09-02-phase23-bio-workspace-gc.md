# Phase 23：bio_workspace 磁盘治理 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 bio_workspace 加 TTL+LRU 混合的周期磁盘治理，防止数据集无限膨胀。

**Architecture:** 新模块 `orchestrator/bio_workspace_gc.py` 提供纯函数式 `sweep()`；`BioRunner.run()` 开头打点 `.last_access` 标记最近使用；ws_client 新增 `bio-workspace-gc` daemon 线程按 `settings` 周期调用 sweep。宽限期（2h）保护活动任务。

**Tech Stack:** 纯标准库（re/shutil/time/pathlib/threading）+ pytest（monkeypatch/tmp_path）。

**Spec:** `docs/superpowers/specs/2026-09-02-bio-workspace-gc-design.md`

**环境：** Windows + PowerShell，工作目录 `i:\飞书agent`，测试命令统一为
`.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp`（单文件跑法把 `tests` 换成具体文件路径）。

---

### Task 1: sweep 核心模块（TDD）

**Files:**
- Create: `orchestrator/bio_workspace_gc.py`
- Test: `tests/unit/test_bio_workspace_gc.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_bio_workspace_gc.py`：

```python
"""Phase 23：bio_workspace_gc.sweep 单测（tmp_path 造假数据集 + now 注入控时）。"""
import os
import time
from pathlib import Path

from orchestrator.bio_workspace_gc import sweep

DAY = 86400


def _mk_dataset(root: Path, name: str, *, size: int = 100,
                age_sec: float = 0) -> Path:
    """造假数据集目录：data.bin（size 字节），文件与目录 mtime 回拨 age_sec。"""
    d = root / name
    d.mkdir()
    f = d / "data.bin"
    f.write_bytes(b"x" * size)
    past = time.time() - age_sec
    os.utime(f, (past, past))
    os.utime(d, (past, past))
    return d


def _touch_marker(d: Path) -> None:
    """模拟 BioRunner 打点：.last_access 置为当前时间。"""
    (d / ".last_access").touch()


def test_ttl_deletes_expired(tmp_path):
    """last_used 超过 ttl 的数据集被整目录删除并计入 freed_bytes。"""
    d = _mk_dataset(tmp_path, "aaaaaaaabbbb", age_sec=8 * DAY)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=10**12, grace_sec=7200)
    assert not d.exists()
    assert r["ttl_deleted"] == ["aaaaaaaabbbb"]
    assert r["freed_bytes"] == 100


def test_ttl_keeps_fresh(tmp_path):
    """未到期数据集保留。"""
    d = _mk_dataset(tmp_path, "aaaaaaaabbbb", age_sec=1 * DAY)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=10**12, grace_sec=7200)
    assert d.exists()
    assert r["ttl_deleted"] == []


def test_grace_protects_from_ttl(tmp_path):
    """宽限期内（2h 动过）的目录即使超 TTL 也跳过。"""
    d = _mk_dataset(tmp_path, "aaaaaaaabbbb", age_sec=8 * DAY)
    _touch_marker(d)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=10**12, grace_sec=7200)
    assert d.exists()
    assert "aaaaaaaabbbb" in r["skipped"]


def test_last_used_prefers_marker(tmp_path):
    """.last_access 新于目录 mtime 时以打点为准（grace=0 排除宽限期干扰）。"""
    d = _mk_dataset(tmp_path, "aaaaaaaabbbb", age_sec=8 * DAY)
    _touch_marker(d)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=10**12, grace_sec=0)
    assert d.exists()  # last_used≈now < ttl，不删


def test_non_dataset_dirs_skipped(tmp_path):
    """非 12hex 目录（如 smoke_st）不在治理范围。"""
    d = _mk_dataset(tmp_path, "smoke_st", age_sec=30 * DAY)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=10**12, grace_sec=7200)
    assert d.exists()
    assert r["ttl_deleted"] == [] and r["skipped"] == []


def test_lru_evicts_oldest_first(tmp_path):
    """总量超 cap 时按 last_used 升序驱逐，达标即停。"""
    old = _mk_dataset(tmp_path, "aaaaaaaaaaaa", size=600, age_sec=3 * DAY)
    mid = _mk_dataset(tmp_path, "bbbbbbbbbbbb", size=600, age_sec=2 * DAY)
    new = _mk_dataset(tmp_path, "cccccccccccc", size=600, age_sec=1 * DAY)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=1300, grace_sec=7200)
    # 总量 1800 > 1300：驱逐最旧的 aaaa（600）后 1200 ≤ 1300 停
    assert not old.exists()
    assert mid.exists() and new.exists()
    assert r["lru_deleted"] == ["aaaaaaaaaaaa"]


def test_lru_respects_grace(tmp_path):
    """LRU 阶段同样遵守宽限期：最旧但宽限内的不驱逐，驱逐次旧。"""
    g = _mk_dataset(tmp_path, "dddddddddddd", size=600, age_sec=5 * DAY)
    _touch_marker(g)  # last_used=now → 宽限期内
    old = _mk_dataset(tmp_path, "eeeeeeeeeeee", size=600, age_sec=3 * DAY)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=600, grace_sec=7200)
    assert g.exists()
    assert not old.exists()
    assert r["lru_deleted"] == ["eeeeeeeeeeee"]


def test_delete_failure_continues(tmp_path, monkeypatch):
    """单目录删除失败仅 warn，其余照常删除。"""
    import orchestrator.bio_workspace_gc as gc
    bad = _mk_dataset(tmp_path, "ffffffffffff", age_sec=8 * DAY)
    good = _mk_dataset(tmp_path, "111111111111", age_sec=8 * DAY)
    real_rmtree = gc.shutil.rmtree

    def flaky(path, *a, **k):
        if Path(path).name == "ffffffffffff":
            raise OSError("locked")
        return real_rmtree(path, *a, **k)

    monkeypatch.setattr(gc.shutil, "rmtree", flaky)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=10**12, grace_sec=7200)
    assert bad.exists()
    assert not good.exists()
    assert r["ttl_deleted"] == ["111111111111"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_bio_workspace_gc.py -q --basetemp=.pytest_tmp`
Expected: collection error（`orchestrator.bio_workspace_gc` 不存在）

- [ ] **Step 3: 实现 sweep 模块**

创建 `orchestrator/bio_workspace_gc.py`：

```python
"""Phase 23：bio_workspace 磁盘治理（spec §2）。

单次 sweep：筛 12hex 数据集目录 → last_used（打点文件/目录 mtime/直接子
文件 mtime 三者取大）→ 宽限期保护 → TTL 到期删除 → LRU 超 cap 驱逐。
纯函数式（now 可注入），不依赖 settings/ws_client，独立可测。
"""
from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 数据集目录名 = compute_dataset_id{,_dir} 的输出（sha1 hexdigest[:12]）；
# smoke_st 等命名目录天然排除，无需白名单
_DATASET_DIR_RE = re.compile(r"^[0-9a-f]{12}$")


def _last_used(d: Path) -> float:
    """数据集最近使用时间：目录 mtime 与直接子文件（含 .last_access 打点）
    mtime 取最大——打点前的历史数据集靠文件/目录 mtime 兜底。"""
    candidates = [d.stat().st_mtime]
    for p in d.iterdir():
        if p.is_file():
            candidates.append(p.stat().st_mtime)
    return max(candidates)


def _dir_size(d: Path) -> int:
    """目录总字节数（递归）。"""
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file())


def _delete(d: Path) -> int | None:
    """整目录删除并返回释放字节；失败 warning 返回 None（不中断 sweep）。"""
    try:
        size = _dir_size(d)
        shutil.rmtree(d)
        logger.info("bio workspace gc: deleted %s (freed %d bytes)",
                    d.name, size)
        return size
    except OSError as e:
        logger.warning("bio workspace gc: delete %s failed: %s", d.name, e)
        return None


def sweep(workspace_root, *, ttl_sec: int, cap_bytes: int, grace_sec: int,
          now: float | None = None) -> dict:
    """单轮清理：TTL 阶段删到期目录，LRU 阶段超 cap 按 last_used 升序驱逐。

    两阶段均跳过宽限期（grace_sec）内动过的目录（活动任务保护）。
    返回 {"ttl_deleted", "lru_deleted", "freed_bytes", "skipped"}。
    """
    root = Path(workspace_root)
    result = {"ttl_deleted": [], "lru_deleted": [],
              "freed_bytes": 0, "skipped": []}
    if not root.is_dir():
        return result
    now = time.time() if now is None else now

    survivors: list[tuple[Path, float]] = []
    for d in root.iterdir():
        if not (d.is_dir() and _DATASET_DIR_RE.match(d.name)):
            continue
        try:
            used = _last_used(d)
        except OSError as e:
            logger.warning("bio workspace gc: stat %s failed: %s", d.name, e)
            continue
        if now - used < grace_sec:
            result["skipped"].append(d.name)
            survivors.append((d, used))
            continue
        if now - used >= ttl_sec:
            freed = _delete(d)
            if freed is not None:
                result["ttl_deleted"].append(d.name)
                result["freed_bytes"] += freed
            else:
                survivors.append((d, used))
        else:
            survivors.append((d, used))

    # LRU：宽限期内的计入总量但不驱逐；驱逐到 ≤ cap 即止
    total = sum(_dir_size(d) for d, _ in survivors)
    evictable = sorted((x for x in survivors if now - x[1] >= grace_sec),
                       key=lambda x: x[1])
    for d, _ in evictable:
        if total <= cap_bytes:
            break
        size = _dir_size(d)
        freed = _delete(d)
        if freed is not None:
            result["lru_deleted"].append(d.name)
            result["freed_bytes"] += freed
            total -= size
    return result
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_bio_workspace_gc.py -q --basetemp=.pytest_tmp`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add orchestrator/bio_workspace_gc.py tests/unit/test_bio_workspace_gc.py
git commit -m "feat(phase23): bio_workspace GC sweep 核心（TTL+LRU+宽限期，TDD）"
```

---

### Task 2: BioRunner 打点（TDD）

**Files:**
- Modify: `orchestrator/tools/bio/bio_runner.py`
- Modify: `tests/unit/test_bio_runner.py`

先读 `tests/unit/test_bio_runner.py` 了解既有风格，把测试追加到文件末尾。

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_bio_runner.py`（顶部 import 区补 `import os`、`import time`、`from pathlib import Path`、`from orchestrator.tools.bio.bio_runner import touch_last_access`——以文件已有 import 为准补齐缺失项）：

```python
def test_touch_last_access_creates_marker(tmp_path):
    """目录存在时创建 .last_access 打点文件。"""
    d = tmp_path / "abcdef123456"
    d.mkdir()
    touch_last_access(str(tmp_path), "abcdef123456")
    assert (d / ".last_access").exists()


def test_touch_last_access_updates_mtime(tmp_path):
    """已有打点文件时刷新 mtime（LRU 的"最近使用"依据）。"""
    d = tmp_path / "abcdef123456"
    d.mkdir()
    marker = d / ".last_access"
    marker.touch()
    old = time.time() - 3600
    os.utime(marker, (old, old))
    touch_last_access(str(tmp_path), "abcdef123456")
    assert marker.stat().st_mtime > old


def test_touch_last_access_skips_invalid(tmp_path):
    """None / 非 12hex（路径穿越防护）/ 目录不存在 → 静默跳过。"""
    touch_last_access(str(tmp_path), None)
    touch_last_access(str(tmp_path), "../escape")
    touch_last_access(str(tmp_path), "0123456789ab")  # 合法 hex 但目录不存在
    assert list(tmp_path.iterdir()) == []


def test_touch_last_access_failure_non_blocking(tmp_path, monkeypatch):
    """打点 IO 异常不抛（best-effort，宁可漏打点不可挂任务）。"""
    d = tmp_path / "abcdef123456"
    d.mkdir()

    def boom(*a, **k):
        raise OSError("read-only fs")

    monkeypatch.setattr(Path, "touch", boom)
    touch_last_access(str(tmp_path), "abcdef123456")  # 不抛即通过


def test_run_touches_last_access(tmp_path, monkeypatch):
    """run() 开头对 args.dataset_id 对应目录打点（docker 调用 mock 掉）。"""
    import subprocess as sp

    ws = tmp_path / "ws"
    d = ws / "abcdef123456"
    d.mkdir(parents=True)
    runner = BioRunner(image="img", workspace_root=str(ws), data_roots=[])

    class _P:
        returncode = 0
        stdout = '{"ok": true}'
        stderr = ""

    monkeypatch.setattr(sp, "run", lambda *a, **k: _P())
    runner.run("qc", {"dataset_id": "abcdef123456"})
    assert (d / ".last_access").exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_bio_runner.py -q --basetemp=.pytest_tmp`
Expected: 5 个新用例 FAIL（`touch_last_access` 不存在）

- [ ] **Step 3: 实现打点**

`orchestrator/tools/bio/bio_runner.py` 两处改动。

改动 1——import 区（第 10-15 行 `import hashlib/json/logging/os/subprocess` 块）补 `import re`：

```python
import hashlib
import json
import logging
import os
import re
import subprocess
from pathlib import Path
```

改动 2——`parse_gene_list` 之后、`class BioRunner` 之前插入模块级函数：

```python
def touch_last_access(workspace_root: str, dataset_id) -> None:
    """Phase 23：GC 打点——更新 <workspace_root>/<dataset_id>/.last_access。

    best-effort：dataset_id 为空或非 12hex（dataset_ref 来自 LLM/planner，
    正则校验挡路径穿越）、目录不存在（load 首跑目录由容器内脚本新建）、
    IO 异常均仅 warning/静默，绝不阻断任务。
    """
    if not dataset_id or not re.fullmatch(r"[0-9a-f]{12}", str(dataset_id)):
        return
    try:
        d = Path(workspace_root) / str(dataset_id)
        if d.is_dir():
            (d / ".last_access").touch()
    except OSError as e:
        logger.warning("gc touch failed for %s: %s", dataset_id, e)
```

改动 3——`BioRunner.run()` 方法体开头（docstring 之后、`cmd = [...]` 之前）插一行：

```python
        # Phase 23：GC 打点（best-effort，读引用也算"最近使用"）
        touch_last_access(self.workspace_root, args.get("dataset_id"))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_bio_runner.py -q --basetemp=.pytest_tmp`
Expected: 全 passed（既有 + 新 5）

- [ ] **Step 5: Commit**

```bash
git add orchestrator/tools/bio/bio_runner.py tests/unit/test_bio_runner.py
git commit -m "feat(phase23): BioRunner .last_access 打点（12hex 校验防穿越，TDD）"
```

---

### Task 3: settings 配置项（TDD）

**Files:**
- Modify: `config/settings.py`
- Test: `tests/unit/test_bio_workspace_gc.py`（追加 settings 用例，Phase 23 测试集中一处）

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_bio_workspace_gc.py` 末尾：

```python
def test_gc_settings_fields_exist():
    """Phase 23 五个配置字段存在且默认值符合 spec §3。"""
    from config.settings import Settings
    fields = Settings.__dataclass_fields__
    assert fields["bio_workspace_ttl_sec"].default == 604800
    assert fields["bio_workspace_cap_gb"].default == 10
    assert fields["bio_workspace_grace_sec"].default == 7200
    assert fields["bio_workspace_gc_interval_sec"].default == 3600
    assert fields["bio_workspace_gc_enabled"].default is True


def test_gc_settings_env_override(monkeypatch):
    """env 覆盖生效（BIO_WORKSPACE_GC_ENABLED=false 语义关闭）。"""
    from config.settings import load_settings
    monkeypatch.setenv("BIO_WORKSPACE_TTL_SEC", "3600")
    monkeypatch.setenv("BIO_WORKSPACE_CAP_GB", "5")
    monkeypatch.setenv("BIO_WORKSPACE_GC_ENABLED", "false")
    s = load_settings()
    assert s.bio_workspace_ttl_sec == 3600
    assert s.bio_workspace_cap_gb == 5
    assert s.bio_workspace_gc_enabled is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_bio_workspace_gc.py -q --basetemp=.pytest_tmp`
Expected: 2 个新用例 FAIL（KeyError / 字段不存在）

- [ ] **Step 3: 加配置字段**

`config/settings.py` 两处改动（同文件多编辑必须**串行**——Phase 22 T3 竞态教训）。

改动 1——`Settings` 类 bio 区末尾（第 110 行 `bio_memory: str = "16g"` 之后）追加：

```python
    # === Phase 23: bio_workspace 磁盘治理（TTL+LRU 周期清理，spec §3） ===
    # 数据集保留期（秒，默认 7d）：last_used 早于此即整目录删除
    bio_workspace_ttl_sec: int = 604800
    # workspace 总量上限（GB）：TTL 后仍超则按 last_used 升序 LRU 驱逐
    bio_workspace_cap_gb: int = 10
    # 宽限期（秒，默认 2h）：窗口内动过的目录一律不删（活动任务保护）
    bio_workspace_grace_sec: int = 7200
    # GC 扫描线程间隔（秒，0 不启动线程）
    bio_workspace_gc_interval_sec: int = 3600
    # GC 总开关（env "false"/"0" 关闭 sweeper 线程）
    bio_workspace_gc_enabled: bool = True
```

改动 2——`load_settings()` 返回构造末尾（第 213 行 `bio_memory=...` 之后）追加：

```python
        # Phase 23：bio_workspace 磁盘治理
        bio_workspace_ttl_sec=int(
            os.environ.get("BIO_WORKSPACE_TTL_SEC", "604800")),
        bio_workspace_cap_gb=int(
            os.environ.get("BIO_WORKSPACE_CAP_GB", "10")),
        bio_workspace_grace_sec=int(
            os.environ.get("BIO_WORKSPACE_GRACE_SEC", "7200")),
        bio_workspace_gc_interval_sec=int(
            os.environ.get("BIO_WORKSPACE_GC_INTERVAL_SEC", "3600")),
        bio_workspace_gc_enabled=os.environ.get(
            "BIO_WORKSPACE_GC_ENABLED", "true").lower() not in ("false", "0"),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_bio_workspace_gc.py -q --basetemp=.pytest_tmp`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add config/settings.py tests/unit/test_bio_workspace_gc.py
git commit -m "feat(phase23): bio_workspace GC 五个配置项（env 可覆盖，TDD）"
```

---

### Task 4: ws_client sweeper 线程（TDD）

**Files:**
- Modify: `gateway/ws_client.py`
- Test: `tests/unit/test_ws_client_gc_sweeper.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_ws_client_gc_sweeper.py`：

```python
"""Phase 23：ws_client bio workspace GC sweeper 单测（沿用 scanner 模式）。"""
from types import SimpleNamespace

import gateway.ws_client as wsc


def _settings(**over):
    """构造只含 GC 相关字段的 settings 桩。"""
    base = dict(
        bio_workspace_gc_enabled=True,
        bio_workspace_gc_interval_sec=3600,
        bio_workspace_root="./bio_workspace",
        bio_workspace_ttl_sec=604800,
        bio_workspace_cap_gb=10,
        bio_workspace_grace_sec=7200,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_sweeper_disabled_returns_none():
    """enabled=False 不启动线程。"""
    assert wsc.start_bio_workspace_gc_sweeper(
        _settings(bio_workspace_gc_enabled=False)) is None


def test_sweeper_interval_zero_returns_none():
    """interval=0 不启动线程。"""
    assert wsc.start_bio_workspace_gc_sweeper(
        _settings(bio_workspace_gc_interval_sec=0)) is None


def test_sweeper_starts_daemon_thread():
    """正常启动：返回 daemon 线程，命名 bio-workspace-gc。"""
    t = wsc.start_bio_workspace_gc_sweeper(_settings())
    try:
        assert t is not None and t.daemon and t.name == "bio-workspace-gc"
    finally:
        pass  # daemon 线程随 pytest 进程退出，无需 join


def test_sweep_once_passes_settings(monkeypatch):
    """单轮 sweep 把 settings 正确换算传给 gc.sweep（GB→bytes）。"""
    calls = []
    fake_result = {"ttl_deleted": [], "lru_deleted": [],
                   "freed_bytes": 0, "skipped": []}
    monkeypatch.setattr(
        wsc, "sweep", lambda *a, **k: calls.append((a, k)) or fake_result)
    wsc._bio_gc_sweep_once(_settings())
    (args, kwargs) = calls[0]
    assert args == ("./bio_workspace",)
    assert kwargs["ttl_sec"] == 604800
    assert kwargs["cap_bytes"] == 10 * (1024 ** 3)
    assert kwargs["grace_sec"] == 7200
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_ws_client_gc_sweeper.py -q --basetemp=.pytest_tmp`
Expected: FAIL（`wsc.start_bio_workspace_gc_sweeper` / `wsc._bio_gc_sweep_once` 不存在）

- [ ] **Step 3: 实现 sweeper**

`gateway/ws_client.py` 三处改动（**串行**编辑）。

改动 1——模块 import 区补：

```python
from orchestrator.bio_workspace_gc import sweep
```

改动 2——`start_kernel_idle_sweeper` 函数之后（约第 113 行后）插入：

```python
def _bio_gc_sweep_once(settings) -> dict:
    """单轮 bio_workspace GC（线程内同步直调；异常由线程 loop 吃掉）。"""
    return sweep(
        settings.bio_workspace_root,
        ttl_sec=settings.bio_workspace_ttl_sec,
        cap_bytes=settings.bio_workspace_cap_gb * (1024 ** 3),
        grace_sec=settings.bio_workspace_grace_sec,
    )


def start_bio_workspace_gc_sweeper(settings):
    """Phase 23：bio_workspace 磁盘治理守护线程（每 interval 秒 sweep 一轮）。

    enabled=False 或 interval=0 → 不启动返回 None；单轮异常吃掉保线程。
    """
    if not getattr(settings, "bio_workspace_gc_enabled", True):
        return None
    interval = getattr(settings, "bio_workspace_gc_interval_sec", 3600)
    if not interval:
        return None

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                result = _bio_gc_sweep_once(settings)
                if result["ttl_deleted"] or result["lru_deleted"]:
                    logger.info(
                        "bio workspace gc: ttl=%s lru=%s freed=%d bytes",
                        result["ttl_deleted"], result["lru_deleted"],
                        result["freed_bytes"])
            except Exception:
                logger.exception("bio workspace gc sweep failed")

    t = threading.Thread(target=loop, daemon=True, name="bio-workspace-gc")
    t.start()
    logger.info(
        "bio workspace gc sweeper started (interval=%ss, ttl=%ss, cap=%sGB)",
        interval, getattr(settings, "bio_workspace_ttl_sec", "?"),
        getattr(settings, "bio_workspace_cap_gb", "?"))
    return t
```

改动 3——`main()` 中 `start_kernel_idle_sweeper(...)` 调用块之后（约第 354 行后）插入：

```python
    # Phase 23：bio_workspace 磁盘治理（TTL+LRU 周期清理）
    start_bio_workspace_gc_sweeper(rt.settings)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_ws_client_gc_sweeper.py tests/unit/test_ws_client_pidfile.py -q --basetemp=.pytest_tmp`
Expected: 4 + 5 passed

- [ ] **Step 5: Commit**

```bash
git add gateway/ws_client.py tests/unit/test_ws_client_gc_sweeper.py
git commit -m "feat(phase23): ws_client bio-workspace-gc sweeper 线程（TDD）"
```

---

### Task 5: 全量回归 + 冒烟 + 真机验证 + 文档收尾

**Files:**
- Modify: `docs/ROADMAP.md`
- Modify: `测试总结+2026-09-02T12-26-38.md`

- [ ] **Step 1: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp 2>&1 | Select-String "\d+ passed"`
Expected: ≥829 passed（811 + 本轮 18）

- [ ] **Step 2: 冒烟 8 步（确认打点未影响 bio 链路）**

Run: `python scripts/smoke_st_chain.py`（系统 Python，按此前批②③惯例）
Expected: SMOKE PASS，17 产物 OK

- [ ] **Step 3: 真机 GC 验证**

1. 造假超期数据集：
   ```powershell
   $d = "i:\飞书agent\bio_workspace\deadbeef0000"; New-Item -ItemType Directory -Force $d | Out-Null
   Set-Content "$d\data.bin" "x"; $past = (Get-Date).AddDays(-8)
   (Get-Item "$d\data.bin").LastWriteTime = $past; (Get-Item $d).LastWriteTime = $past
   ```
2. 临时短周期重启 ws_client 验证删除：
   ```powershell
   $env:BIO_WORKSPACE_GC_INTERVAL_SEC = "60"
   .venv\Scripts\python.exe -m gateway.ws_client --force   # 后台终端
   ```
   等 ~90s，确认：日志出现 `bio workspace gc sweeper started` + `deleted deadbeef0000`；目录已删；现有 5 个真实数据集（均非超期或在宽限期内）不受影响。
3. 去掉环境变量正常重启 ws_client（`--force`），留单实例运行：
   ```powershell
   Remove-Item Env:BIO_WORKSPACE_GC_INTERVAL_SEC
   .venv\Scripts\python.exe -m gateway.ws_client --force   # 后台终端
   ```
   确认 Lark connected + `bio workspace gc sweeper started (interval=3600s, ttl=604800s, cap=10GB)`。

- [ ] **Step 4: ROADMAP 更新**

`docs/ROADMAP.md` 三处：
1. Phase 20 后续扩展 `- [ ] bio_workspace 磁盘治理...` → `- [x] bio_workspace 磁盘治理（Phase 23 完成）`
2. Phase 22 节之后插入 Phase 23 节：
   ```markdown
   ## Phase 23：bio_workspace 磁盘治理 — 已实施（2026-09-02）

   spec：`docs/superpowers/specs/2026-09-02-bio-workspace-gc-design.md`

   - [x] sweep 核心（TTL 7d + LRU 10GB + 宽限期 2h，12hex 候选集）
   - [x] BioRunner .last_access 打点（12hex 校验防路径穿越）
   - [x] ws_client bio-workspace-gc sweeper 线程（interval 3600s，env 全可覆盖）
   - [x] 真机验证：造假超期目录 60s 周期删除通过，活动数据集无损
   ```
3. 变更记录追加：
   `| 2026-09-02 | Phase 23 bio_workspace 磁盘治理（TTL+LRU 周期清理 + .last_access 打点 + sweeper 线程，真机验证通过） |`

- [ ] **Step 5: 更新测试总结 + 收尾 Commit**

`测试总结+2026-09-02T12-26-38.md` 追加 Phase 23 大节（任务/commit/回归数/真机验证过程/后续建议）。

```bash
git add docs/ROADMAP.md "测试总结+2026-09-02T12-26-38.md" docs/superpowers/plans/2026-09-02-phase23-bio-workspace-gc.md
git commit -m "docs(phase23): ROADMAP + 测试总结 + 计划归档（磁盘治理收官）"
```

---

## Self-Review 结论

- **Spec 覆盖**：§1 三组件（T1 sweep / T2 打点 / T4 线程）、§2 算法（T1）、
  §3 配置（T3）、§4 错误处理（T1 _delete / T2 best-effort / T4 线程吃掉）、
  §5 测试（T1-T4 各步）、§6 验收（T5）。非目标未引入。
- **占位符**：无——所有代码完整，命令具体；唯一"以既有为准"的是
  test_bio_runner.py 的 import 补齐（文件现状需执行者读取，属合理指引）。
- **类型一致性**：`sweep(workspace_root, *, ttl_sec, cap_bytes, grace_sec, now)`
  在 T1 实现/测试、T4 `_bio_gc_sweep_once`、T4 测试中签名一致；
  `touch_last_access(workspace_root, dataset_id)` 在 T2 实现/测试一致；
  settings 五字段名三处（类定义/env 加载/测试）一致。
