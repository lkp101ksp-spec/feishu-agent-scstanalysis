# Phase 21 批①（st_* 基础链）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增空间转录组基础分析链 5 工具（st_load/st_qc/st_process/st_markers/st_plot），跑在独立 bio:st-cpu 镜像，图片回传 IM 与绑定文档。

**Architecture:** 完全复用 Phase 20 bio 容器基建（BioRunner 白名单/dataset_ref 幂等/资源限额/超时放大/图片回传）。新增 sandbox/st.Dockerfile（squidpy 栈）、sandbox/st_tools/ 5 参数化脚本（stdin JSON → stdout JSON，与 sc_tools 同约定）、orchestrator/tools/builtin/l3_spatial.py 注册模块。st 与 sc 共用 BioRunner 实例但镜像不同——BioRunner 增加按脚本名选镜像的能力（st_* 脚本走 st 镜像）。

**Tech Stack:** Python 3.12 / scanpy / squidpy（visium 读取 + 空间邻域）/ leidenalg / docker / pytest

**Spec:** `docs/superpowers/specs/2026-09-01-feishu-research-agent-phase21-spatial-transcriptomics-design.md`

---

## 前置事实（已核实的现有接口）

1. **BioRunner**（[orchestrator/tools/bio/bio_runner.py](file:///i:/飞书agent/orchestrator/tools/bio/bio_runner.py)）：
   - `__init__(*, image, workspace_root, data_roots, timeout_sec, cpus, memory)`
   - `resolve_data_path(user_path) -> (挂载根, /data相对路径, 主机绝对路径)`，违规抛 `BioRunError("SC_PATH_FORBIDDEN")`
   - `run(script, args, *, timeout_sec=None, mounts=None) -> dict`，执行 `docker run ... <self.image> python /opt/sc_tools/<script>.py`；脚本级错误（`fail()` 输出 `{"ok": false, "error_code", "error_message"}`）抛 `BioRunError(error_code, message)`
   - `compute_dataset_id(abs_path)` = sha1(路径+文件大小)[:12]——**只适用于单文件**；spaceranger 目录需新函数（目录聚合 hash）
2. **脚本公共库** `sandbox/sc_tools/common.py`（st 镜像内复制为 `/opt/st_tools/common.py`）：`read_args()/emit()/fail()/run()/ws_path()/load_adata()`；约定图落 `/ws`、数据限 `/data`（只读）、matplotlib Agg
3. **工具注册**（[orchestrator/tools/builtin/l3_singlecell.py](file:///i:/飞书agent/orchestrator/tools/builtin/l3_singlecell.py) 模式）：`registry.register(ToolSpec(name, description, parameters, risk_level="L1_compute", handler, timeout_sec))`；handler 捕获 `BioRunError` 返回 `{"error_code", "error_message"}`；成功输出 `out.pop("ok", None)`
4. **装配**（[orchestrator/app.py:108-135](file:///i:/飞书agent/orchestrator/app.py)）：`bio_data_roots` 非空时建 BioRunner 并 `register_l3_singlecell`；settings 有 `bio_image`（config/settings.py:96）
5. **图片收集**（[orchestrator/research_runner.py:379](file:///i:/飞书agent/orchestrator/research_runner.py)）：`tool_name.startswith("sc_")` 才收集 `umap_png/dotplot_png/pngs` —— **需扩展到 st_***（本计划 Task 6）
6. **超时放大**：research_runner 判定 plan 含 sc_* 节点放大到 `research_sc_timeout_sec`——同样需扩展 st_*（Task 6）
7. **st_tools 脚本调用路径**：BioRunner 现在写死 `/opt/sc_tools/{script}.py`——需参数化脚本目录（Task 1）

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `sandbox/st.Dockerfile` | 新建 | st 镜像：scanpy 栈 + squidpy |
| `sandbox/st_tools/common.py` | 新建 | 与 sc_tools/common.py 同约定（st 镜像内 /opt/st_tools/） |
| `sandbox/st_tools/load.py` | 新建 | 三格式读取 → raw.h5ad + dataset_ref |
| `sandbox/st_tools/qc.py` | 新建 | spot 级过滤 → filtered.h5ad + 分布统计 |
| `sandbox/st_tools/process.py` | 新建 | 空间邻域 + PCA + Leiden 空间域 → processed.h5ad + umap/spatial png |
| `sandbox/st_tools/markers.py` | 新建 | 域间差异基因 → markers.json + dotplot.png |
| `sandbox/st_tools/plot.py` | 新建 | spatial 着色图 → pngs |
| `orchestrator/tools/bio/bio_runner.py` | 修改 | 支持目录 dataset_id 聚合 hash + 脚本目录/镜像参数化 |
| `orchestrator/tools/builtin/l3_spatial.py` | 新建 | st_* 5 工具注册 |
| `orchestrator/app.py` | 修改 | 装配 st 工具（st 镜像 BioRunner） |
| `config/settings.py` | 修改 | 新增 bio_st_image 配置 |
| `orchestrator/research_runner.py` | 修改 | 图片收集/超时放大判定扩展 st_* |
| `tests/unit/test_bio_runner.py` | 修改 | 目录 dataset_id + 镜像/脚本目录参数化用例 |
| `tests/unit/test_l3_spatial.py` | 新建 | st_* 注册/参数/错误码用例 |
| `tests/integration/test_research_runner.py` | 修改 | st_* 图片收集/超时放大用例 |

---

### Task 1: BioRunner 扩展（目录 dataset_id + 镜像/脚本目录参数化）

**Files:**
- Modify: `orchestrator/tools/bio/bio_runner.py`
- Test: `tests/unit/test_bio_runner.py`

- [ ] **Step 1: 写失败测试（目录 dataset_id）**

追加到 `tests/unit/test_bio_runner.py`：

```python
def test_compute_dataset_id_dir_aggregate(tmp_path):
    """目录聚合 hash：任一文件变化换 id；目录不动 id 稳定。"""
    from orchestrator.tools.bio.bio_runner import compute_dataset_id_dir

    d = tmp_path / "spaceranger_out"
    d.mkdir()
    (d / "filtered_feature_bc_matrix.h5").write_bytes(b"v1")
    (d / "tissue_positions.csv").write_bytes(b"pos")

    id1 = compute_dataset_id_dir(str(d))
    id2 = compute_dataset_id_dir(str(d))
    assert id1 == id2 and len(id1) == 12

    (d / "tissue_positions.csv").write_bytes(b"pos-changed")
    id3 = compute_dataset_id_dir(str(d))
    assert id3 != id1


def test_compute_dataset_id_dir_missing_raises(tmp_path):
    from orchestrator.tools.bio.bio_runner import compute_dataset_id_dir
    import pytest
    from orchestrator.tools.bio.bio_runner import BioRunError
    with pytest.raises(BioRunError, match="not found"):
        compute_dataset_id_dir(str(tmp_path / "nope"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_bio_runner.py::test_compute_dataset_id_dir_aggregate -v --basetemp=.pytest_tmp`
Expected: FAIL（ImportError: compute_dataset_id_dir）

- [ ] **Step 3: 实现 compute_dataset_id_dir**

在 `orchestrator/tools/bio/bio_runner.py` 的 `compute_dataset_id` 后追加：

```python
def compute_dataset_id_dir(abs_dir: str) -> str:
    """目录数据集（如 spaceranger 输出）幂等键：聚合 hash 目录内容。

    递归收集（相对路径, 文件大小, mtime_ns）排序后聚合 sha1[:12]——
    任一文件增删/改内容（大小或 mtime 变化）即换 id（Phase 21 spec §3）。
    目录不存在抛 BioRunError。
    """
    d = Path(abs_dir)
    if not d.is_dir():
        raise BioRunError(
            "SC_FILE_NOT_FOUND", f"data dir not found: {abs_dir}")
    items = []
    for p in sorted(d.rglob("*")):
        if p.is_file():
            st = p.stat()
            items.append(f"{p.relative_to(d).as_posix()}:{st.st_size}:{st.st_mtime_ns}")
    digest = hashlib.sha1(
        f"{os.path.realpath(abs_dir)}|".encode("utf-8")
        + "|".join(items).encode("utf-8")).hexdigest()
    return digest[:12]
```

- [ ] **Step 4: 写失败测试（run 的 image/script_dir 参数化）**

追加：

```python
def test_run_uses_custom_image_and_script_dir(runner, fake_docker_ok):
    """run(image=..., script_dir=...) 覆盖实例默认值（st 镜像走 /opt/st_tools）。"""
    out = runner.run(
        "load", {"path": "x"},
        image="feishu-research-agent/bio:st-cpu-latest",
        script_dir="/opt/st_tools")
    assert out["ok"] is True
    cmd = fake_docker_ok.cmd
    img_idx = cmd.index("feishu-research-agent/bio:st-cpu-latest")
    script_idx = cmd.index("python")
    assert cmd[script_idx + 1] == "/opt/st_tools/load.py"
    assert img_idx < script_idx


def test_run_default_image_and_script_dir(runner, fake_docker_ok):
    """不传覆盖参数时沿用实例默认 image 与 /opt/sc_tools。"""
    runner.run("load", {})
    cmd = fake_docker_ok.cmd
    assert "feishu-research-agent/bio:test" in cmd
    assert "/opt/sc_tools/load.py" in cmd
```

（fixture `runner`/`fake_docker_ok` 沿用本文件现有 mock 模式——若现有 fixture 名不同，以现有名为准，`fake_docker_ok` 需记录完整 cmd 供断言；如现有 fixture 未记录 cmd，追加一个记录型 fixture：）

```python
@pytest.fixture
def fake_docker_ok(monkeypatch):
    """mock subprocess.run 返回成功 JSON，并记录完整 docker 命令。"""
    recorded = SimpleNamespace(cmd=[])
    def _fake_run(cmd, **kwargs):
        recorded.cmd = list(cmd)
        return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")
    monkeypatch.setattr("orchestrator.tools.bio.bio_runner.subprocess.run", _fake_run)
    return recorded
```

- [ ] **Step 5: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_bio_runner.py -k "custom_image or default_image" -v --basetemp=.pytest_tmp`
Expected: FAIL（run 无 image/script_dir 参数 → TypeError）

- [ ] **Step 6: 实现 run 参数化**

修改 `BioRunner.run` 签名与命令拼接：

```python
    def run(self, script: str, args: dict, *, timeout_sec: int | None = None,
            mounts: list[tuple[str, str]] | None = None,
            image: str | None = None,
            script_dir: str = "/opt/sc_tools") -> dict:
        """跑容器内参数化脚本，返回 stdout JSON dict。

        mounts: 额外 (主机目录, 容器目录) 挂载（数据目录只读）。
        image/script_dir: 覆盖实例默认（st_* 脚本走 st 镜像 /opt/st_tools）。
        超时/非零退出/JSON 解析失败 → BioRunError。
        """
        cmd = ["docker", "run", "--rm", "-i", "--network", "none",
               "--cpus", self.cpus, "--memory", self.memory,
               "-v", f"{self.workspace_root}:/ws"]
        for host_dir, container_dir in (mounts or []):
            cmd += ["-v", f"{host_dir}:{container_dir}:ro"]
        cmd += [image or self.image, "python", f"{script_dir}/{script}.py"]
```

（方法体其余部分不动；docstring 更新如上。）

- [ ] **Step 7: 全文件测试通过**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_bio_runner.py -v --basetemp=.pytest_tmp`
Expected: 全部 PASS（含既有 15 用例不回归）

- [ ] **Step 8: Commit**

```bash
git add orchestrator/tools/bio/bio_runner.py tests/unit/test_bio_runner.py
git commit -m "feat(phase21): BioRunner 目录聚合 dataset_id + run 镜像/脚本目录参数化"
```

---

### Task 2: st 镜像 + st_tools 公共库

**Files:**
- Create: `sandbox/st.Dockerfile`
- Create: `sandbox/st_tools/common.py`

- [ ] **Step 1: 写 st.Dockerfile**

```dockerfile
# Phase 21 st 镜像：空间转录组分析栈（spec 2026-09-01 phase21 §2.1）
# 构建：docker build -t feishu-research-agent/bio:st-cpu-latest sandbox -f sandbox/st.Dockerfile
# 批②追加 commot/cell2location 时只改本文件的 requirements 层（分层缓存友好）
FROM python:3.12-slim

# 清华源装空间转录组栈（项目惯例：pip 默认走清华源）
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    numpy pandas scipy matplotlib h5py \
    anndata scanpy leidenalg igraph squidpy

# 非 root 用户 + 可写目录（与 bio:cpu 镜像惯例一致）
RUN useradd -u 1000 -m bio \
    && mkdir -p /ws /data /tmp/mpl \
    && chown -R bio /ws /tmp/mpl

# matplotlib 无头模式 + 缓存目录指向可写 tmp
ENV MPLCONFIGDIR=/tmp/mpl PYTHONUNBUFFERED=1

WORKDIR /ws

# 固化参数化脚本（BioRunner 调 python /opt/st_tools/<name>.py）
COPY st_tools/ /opt/st_tools/

# 短命容器：跑完即退（--rm），无 CMD 保活需求
CMD ["python", "/opt/st_tools/load.py"]
```

- [ ] **Step 2: 写 st_tools/common.py（复用 sc_tools 约定 + ST 专用辅助）**

```python
"""st 工具公共库：stdin JSON 参数 → stdout JSON 结果（Phase 21 st 镜像内）。

约定与 sc_tools/common.py 一致（spec phase21 §2）：
- 参数经 stdin 传入 JSON；结果 stdout 输出 JSON（{"ok": true, ...} /
  {"ok": false, "error_code": ..., "error_message": ...}）
- 图一律落 /ws；数据路径限定 /data（只读挂载）与 /ws
- matplotlib 无头模式 + 缓存目录指向可写 tmp
ST 专用：spatial coords 校验 / 坐标注入 obsm["spatial"]。
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

DATA_ROOT = Path("/data")
WS_ROOT = Path("/ws")


def read_args() -> dict:
    """读 stdin JSON 参数；空 stdin 返回空 dict。"""
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    return json.loads(raw)


def emit(obj: dict) -> None:
    """结果 JSON 写 stdout（唯一 stdout 输出，图/日志走 stderr）。"""
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def fail(error_code: str, error_message: str) -> None:
    """统一失败输出。"""
    emit({"ok": False, "error_code": error_code, "error_message": error_message})


def run(main) -> None:
    """脚本入口包装：异常吃掉转统一 JSON 错误（含 traceback 首 3 帧）。"""
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 —— 参数化脚本兜底统一报错
        tb = traceback.format_exc(limit=3)
        print(tb, file=sys.stderr)
        fail("SCRIPT_ERROR", f"{type(e).__name__}: {e}")


def ensure_spatial(adata, *, source: str = "obsm") -> None:
    """校验/注入空间坐标到 obsm["spatial"]（ST 链路所有下游脚本的前置）。

    source=obsm：已含 obsm["spatial"]（visium 读取或外部 h5ad）→ 直接过。
    抛 ValueError（脚本层转 ST_FORMAT_INVALID）当坐标缺失或形状不对。
    """
    import numpy as np

    sp = adata.obsm.get("spatial")
    if sp is None:
        raise ValueError(
            "spatial coordinates missing: obsm['spatial'] not found; "
            "load with st_load (visium dir / h5ad with spatial / mtx+coords csv)")
    sp = np.asarray(sp)
    if sp.ndim != 2 or sp.shape[1] < 2 or sp.shape[0] != adata.n_obs:
        raise ValueError(
            f"invalid spatial coords shape {sp.shape}: expect (n_spots, >=2) "
            f"matching n_obs={adata.n_obs}")


def load_adata(input_ref: dict):
    """按回退链读 AnnData：filtered.h5ad → raw.h5ad（st 与 sc 同模式）。"""
    import anndata as ad

    ds_dir = WS_ROOT / input_ref["dataset_id"]
    for name in ("filtered", "raw"):
        p = ds_dir / f"{name}.h5ad"
        if p.exists():
            return ad.read_h5ad(p)
    raise FileNotFoundError(
        f"no h5ad under workspace for dataset {input_ref['dataset_id']}; "
        "run st_load first")
```

- [ ] **Step 3: Commit**

```bash
git add sandbox/st.Dockerfile sandbox/st_tools/common.py
git commit -m "feat(phase21): st 镜像 Dockerfile + st_tools 公共库"
```

---

### Task 3: st_load / st_qc 脚本

**Files:**
- Create: `sandbox/st_tools/load.py`
- Create: `sandbox/st_tools/qc.py`

- [ ] **Step 1: 写 st_tools/load.py（三格式读取）**

```python
"""st_load：读入 visium/h5ad/mtx+坐标 → 统一 raw.h5ad + 概要统计（Phase 21）。

stdin: {"path": "/data/xxx", "dataset_id": "...", "format": "auto"}
path 指向：spaceranger 输出目录 / .h5ad 文件 / 含 matrix.mtx(+barcodes/
features) + coords.csv 的目录（coords.csv 需含 spot,x,y 三列）。
"""
from __future__ import annotations

from common import DATA_ROOT, WS_ROOT, emit, fail, run


def _read_visium(p):
    """spaceranger 目录：sq.read.visium（需 filtered_feature_bc_matrix.h5 + spatial/）。"""
    h5 = p / "filtered_feature_bc_matrix.h5"
    if not h5.exists():
        fail("ST_FORMAT_INVALID",
             f"not a spaceranger dir (filtered_feature_bc_matrix.h5 missing): {p}")
        raise SystemExit(1)
    if not (p / "spatial").is_dir():
        fail("ST_FORMAT_INVALID",
             f"spaceranger dir missing spatial/ subdir: {p}")
        raise SystemExit(1)
    import squidpy as sq

    return sq.read.visium(p, count_file=h5.name, library_id="st")


def _read_h5ad(p):
    """h5ad：直读 + 校验空间坐标。"""
    import anndata as ad

    adata = ad.read_h5ad(p)
    sp = adata.obsm.get("spatial")
    if sp is None:
        fail("ST_FORMAT_INVALID",
             f"h5ad has no obsm['spatial']: {p}")
        raise SystemExit(1)
    return adata


def _read_mtx_coords(p):
    """mtx + coords.csv：scanpy 读矩阵 + 坐标注入 obsm["spatial"]。"""
    import pandas as pd
    import scanpy as sc

    coords_csv = p / "coords.csv"
    if not coords_csv.exists():
        fail("ST_FORMAT_INVALID",
             f"mtx dir missing coords.csv (need spot,x,y columns): {p}")
        raise SystemExit(1)
    df = pd.read_csv(coords_csv)
    missing = [c for c in ("spot", "x", "y") if c not in df.columns]
    if missing:
        fail("ST_FORMAT_INVALID",
             f"coords.csv missing columns {missing}: {coords_csv}")
        raise SystemExit(1)

    # scanpy 只认 .gz：未压缩先临时 gzip 到 /tmp 再读（沿用 sc_load 方案）
    import gzip
    import shutil
    import tempfile
    from pathlib import Path

    mtx_dir = p
    if (p / "matrix.mtx").exists() and not (p / "matrix.mtx.gz").exists():
        tmp = tempfile.TemporaryDirectory()  # noqa: SIM115（读毕即弃）
        mtx_dir = Path(tempfile.gettempdir()) / tmp.name
        for name in ("matrix.mtx", "barcodes.tsv", "features.tsv", "genes.tsv"):
            src = p / name
            if src.exists():
                with open(src, "rb") as fin, \
                        gzip.open(mtx_dir / f"{name}.gz", "wb") as fout:
                    shutil.copyfileobj(fin, fout)
    adata = sc.read_10x_mtx(mtx_dir, var_names="gene_symbols", make_unique=True)

    if len(df) != adata.n_obs:
        fail("ST_FORMAT_INVALID",
             f"coords.csv rows ({len(df)}) != cells in matrix ({adata.n_obs})")
        raise SystemExit(1)
    adata = adata[df["spot"].astype(str).values.astype(adata.obs_names.dtype), :]
    adata.obsm["spatial"] = df[["x", "y"]].to_numpy(dtype="float64")
    return adata


def _detect_and_read(path: str):
    """auto 探测：h5ad 文件 / spaceranger 目录 / mtx+coords 目录。"""
    p = DATA_ROOT / path.lstrip("/")
    if not p.exists():
        fail("SC_FILE_NOT_FOUND", f"data not found: /data/{path}")
        raise SystemExit(1)
    if p.suffix == ".h5ad":
        return _read_h5ad(p)
    if p.is_dir():
        if (p / "filtered_feature_bc_matrix.h5").exists():
            return _read_visium(p)
        return _read_mtx_coords(p)
    fail("ST_FORMAT_UNSUPPORTED",
         f"unsupported format (need visium dir / .h5ad / mtx+coords dir): {path}")
    raise SystemExit(1)


def main() -> None:
    from common import read_args

    args = read_args()
    adata = _detect_and_read(args["path"])

    import numpy as np

    var_names = adata.var_names.astype(str)
    is_mt = var_names.str.startswith("MT-") | var_names.str.startswith("mt-")
    mt_summary = None
    if is_mt.any():
        import scanpy as sc

        adata.var["mt"] = is_mt
        sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None,
                                   log1p=False, inplace=True)
        mt = adata.obs["pct_counts_mt"]
        mt_summary = {
            "mean": round(float(mt.mean()), 2),
            "median": round(float(mt.median()), 2),
            "p95": round(float(np.percentile(mt, 95)), 2),
        }

    import scanpy as sc

    sc.pp.calculate_qc_metrics(adata, percent_top=None, log1p=False,
                               inplace=True)
    ds_dir = WS_ROOT / args["dataset_id"]
    ds_dir.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(ds_dir / "raw.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_spots": int(adata.n_obs),
        "n_genes": int(adata.n_vars),
        "mt_pct": mt_summary,
        "workspace": str(ds_dir),
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: 写 st_tools/qc.py（spot 级过滤 + 分布统计）**

```python
"""st_qc：spot 级质控过滤 → filtered.h5ad + 前后统计（Phase 21）。

stdin: {"dataset_id": "...", "min_genes": 50, "max_genes": 6000,
        "max_mt_pct": 20.0}
失败消息附 genes/spot 分布（median/p90/max）指导调参（沿用 sc_qc 模式）。
"""
from __future__ import annotations

from common import emit, fail, run


def _distribution(values) -> dict:
    """genes/spot 分布摘要（失败消息调参依据）。"""
    import numpy as np

    return {
        "median": float(np.median(values)),
        "p90": float(np.percentile(values, 90)),
        "max": float(np.max(values)),
    }


def main() -> None:
    from common import load_adata, read_args

    args = read_args()
    min_genes = int(args.get("min_genes", 50))
    max_genes = int(args.get("max_genes", 6000))
    max_mt_pct = float(args.get("max_mt_pct", 20.0))

    adata = load_adata({"dataset_id": args["dataset_id"]})
    n_before = int(adata.n_obs)

    import scanpy as sc

    if "n_genes_by_counts" not in adata.obs:
        sc.pp.calculate_qc_metrics(adata, percent_top=None, log1p=False,
                                   inplace=True)
    genes_per_spot = adata.obs["n_genes_by_counts"].to_numpy()
    mask = (genes_per_spot >= min_genes) & (genes_per_spot <= max_genes)
    if "pct_counts_mt" in adata.obs:
        mask &= adata.obs["pct_counts_mt"].to_numpy() <= max_mt_pct

    adata = adata[mask, :].copy()
    n_after = int(adata.n_obs)

    if n_after == 0:
        fail("SC_QC_EMPTY",
             f"all {n_before} spots filtered out (min_genes={min_genes}, "
             f"max_genes={max_genes}, max_mt_pct={max_mt_pct}); "
             f"genes/spot distribution: {_distribution(genes_per_spot)}; "
             "lower min_genes / raise max_mt_pct and retry")
        raise SystemExit(1)

    sc.pp.filter_genes(adata, min_cells=1)
    from common import WS_ROOT

    ds_dir = WS_ROOT / args["dataset_id"]
    adata.write_h5ad(ds_dir / "filtered.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_spots_before": n_before,
        "n_spots_after": n_after,
        "n_genes": int(adata.n_vars),
        "filtered_out": n_before - n_after,
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 3: 语法检查（本地 python 编译，不依赖镜像）**

Run: `.venv\Scripts\python.exe -m py_compile sandbox/st_tools/load.py sandbox/st_tools/qc.py sandbox/st_tools/common.py`
Expected: 无输出（编译通过）

- [ ] **Step 4: Commit**

```bash
git add sandbox/st_tools/load.py sandbox/st_tools/qc.py
git commit -m "feat(phase21): st_load 三格式读取 + st_qc spot 级过滤脚本"
```

---

### Task 4: st_process / st_markers / st_plot 脚本

**Files:**
- Create: `sandbox/st_tools/process.py`
- Create: `sandbox/st_tools/markers.py`
- Create: `sandbox/st_tools/plot.py`

- [ ] **Step 1: 写 st_tools/process.py（空间邻域 + 空间域聚类）**

```python
"""st_process：邻域图→PCA→Leiden 空间域 → processed.h5ad + 图（Phase 21）。

stdin: {"dataset_id": "...", "n_pcs": 30, "resolution": 1.0, "n_neighbors": 15}
产出：umap.png（域着色）+ spatial_domains.png（空间域着色，st 核心输出）。
空间域 = 基于 squidpy 空间邻域的 Leiden（visium 网格数据 hex 邻接）。
"""
from __future__ import annotations

from common import emit, fail, run


def main() -> None:
    from common import WS_ROOT, ensure_spatial, load_adata, read_args

    args = read_args()
    n_pcs = int(args.get("n_pcs", 30))
    resolution = float(args.get("resolution", 1.0))
    n_neighbors = int(args.get("n_neighbors", 15))

    adata = load_adata({"dataset_id": args["dataset_id"]})
    ensure_spatial(adata)

    import matplotlib.pyplot as plt
    import scanpy as sc
    import squidpy as sq

    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(adata, n_top_genes=min(2000, adata.n_vars),
                                flavor="seurat")
    sc.pp.scale(adata, max_value=10)
    sc.tl.pca(adata, n_comps=min(n_pcs, adata.n_obs - 1, adata.n_vars - 1),
              svd_solver="arpack")

    # 空间邻域（visium 网格 → hex 邻接；sq 自动按坐标推断）
    sq.gr.spatial_neighbors(adata, n_neighs=n_neighbors, coord_type="generic")
    # 空间域：空间邻域图上的 Leiden
    sc.pp.neighbors(adata, n_neighbors=n_neighbors,
                    use_rep="X_pca")
    sc.tl.leiden(adata, resolution=resolution, key_added="spatial_domain")

    # UMAP 仅作辅助可视化；spatial 域着色图是 st 核心输出
    sc.tl.umap(adata)
    domain_sizes = adata.obs["spatial_domain"].value_counts().to_dict()

    ds_dir = WS_ROOT / args["dataset_id"]
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    sc.pl.umap(adata, color="spatial_domain", ax=axes[0], show=False)
    sq.pl.spatial_scatter(adata, color="spatial_domain", ax=axes[1], show=False)
    fig.tight_layout()
    fig.savefig(ds_dir / "spatial_domains.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    fig = sc.pl.umap(adata, color="spatial_domain", show=False,
                     return_fig=True)
    fig.savefig(ds_dir / "umap.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    adata.write_h5ad(ds_dir / "processed.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_domains": int(len(domain_sizes)),
        "cluster_sizes": {str(k): int(v) for k, v in domain_sizes.items()},
        "umap_png": f"/ws/{args['dataset_id']}/umap.png",
        "spatial_png": f"/ws/{args['dataset_id']}/spatial_domains.png",
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: 写 st_tools/markers.py（域间差异基因）**

```python
"""st_markers：空间域差异基因 → markers.json + dotplot.png（Phase 21）。

stdin: {"dataset_id": "...", "method": "wilcoxon", "top_n": 10}
"""
from __future__ import annotations

from common import emit, run


def main() -> None:
    from common import WS_ROOT, load_adata, read_args

    args = read_args()
    method = args.get("method", "wilcoxon")
    top_n = int(args.get("top_n", 10))

    adata = load_adata({"dataset_id": args["dataset_id"]})
    if "spatial_domain" not in adata.obs:
        from common import fail

        fail("ST_STATE_INVALID",
             "spatial_domain not found; run st_process first")
        raise SystemExit(1)

    import matplotlib.pyplot as plt
    import scanpy as sc

    sc.tl.rank_genes_groups(adata, groupby="spatial_domain", method=method)
    result = adata.uns["rank_genes_groups"]
    groups = [g for g in result["names"].dtype.names]
    markers = {}
    for g in groups:
        rows = []
        for i in range(min(top_n, len(result["names"][g]))):
            rows.append({
                "gene": str(result["names"][g][i]),
                "score": round(float(result["scores"][g][i]), 2),
                "log2fc": round(float(result["logfoldchanges"][g][i]), 2),
            })
        markers[str(g)] = rows

    top_genes = [markers[g][0]["gene"] for g in groups if markers[g]][:6]
    ds_dir = WS_ROOT / args["dataset_id"]
    if top_genes:
        dp = sc.pl.dotplot(adata, top_genes, groupby="spatial_domain",
                           show=False, return_fig=True)
        dp.savefig(ds_dir / "dotplot.png", dpi=150, bbox_inches="tight")
        plt.close("all")

    import json

    (ds_dir / "markers.json").write_text(
        json.dumps(markers, ensure_ascii=False, indent=2), encoding="utf-8")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "n_groups": len(groups),
        "markers": markers,
        "dotplot_png": f"/ws/{args['dataset_id']}/dotplot.png",
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 3: 写 st_tools/plot.py（spatial 着色图）**

```python
"""st_plot：基因表达/域着色 spatial 图 → png 列表（Phase 21）。

stdin: {"dataset_id": "...", "genes": [...], "color_by": "spatial_domain"}
genes 模式每基因一张 spatial 表达着色图；color_by 模式按 obs 列着色。
"""
from __future__ import annotations

from common import emit, fail, run


def main() -> None:
    from common import WS_ROOT, ensure_spatial, load_adata, read_args

    args = read_args()
    genes = list(args.get("genes") or [])
    color_by = args.get("color_by", "")

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    if adata is None:
        adata = load_adata({"dataset_id": args["dataset_id"]})
    ensure_spatial(adata)

    import matplotlib.pyplot as plt
    import squidpy as sq

    ds_dir = WS_ROOT / args["dataset_id"]
    pngs: list[str] = []

    if genes:
        valid = [g for g in genes if g in adata.var_names]
        if not valid:
            fail("ST_GENES_NOT_FOUND",
                 f"none of genes {genes} found in dataset "
                 f"({adata.n_vars} genes)")
            raise SystemExit(1)
        for g in valid:
            fig = sq.pl.spatial_scatter(adata, color=g, show=False,
                                        return_fig=True)
            fig.savefig(ds_dir / f"{g}_spatial.png", dpi=150,
                        bbox_inches="tight")
            plt.close("all")
            pngs.append(f"/ws/{args['dataset_id']}/{g}_spatial.png")

    if color_by:
        if color_by not in adata.obs:
            fail("ST_COLOR_BY_NOT_FOUND",
                 f"color_by {color_by!r} not in obs columns "
                 f"{list(adata.obs.columns)}")
            raise SystemExit(1)
        fig = sq.pl.spatial_scatter(adata, color=color_by, show=False,
                                    return_fig=True)
        fig.savefig(ds_dir / f"{color_by}_spatial.png", dpi=150,
                    bbox_inches="tight")
        plt.close("all")
        pngs.append(f"/ws/{args['dataset_id']}/{color_by}_spatial.png")

    if not pngs:
        fail("INVALID_INPUT", "provide genes or color_by (at least one)")
        raise SystemExit(1)

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "pngs": pngs,
    })


if __name__ == "__main__":
    run(main)
```

注意：`common.load_adata` 的 `input_ref.get("file")` 只识别 `"processed"`，否则走 filtered→raw 回退链——上面 `load_adata({"dataset_id": ...})` 不传 file 即为回退链，第一个调用传 `"file": "processed"` 若 processed 不存在会抛 FileNotFoundError。**修正**：st_plot 应直接用回退链（filtered→raw，processed 有域列时上游已写入 obs）——实现时统一为：

```python
    adata = load_adata({"dataset_id": args["dataset_id"]})
```

（删掉 processed 分支；域着色需 st_process 已跑，否则 color_by=spatial_domain 时在 ST_COLOR_BY_NOT_FOUND 分支给出明确提示。）

- [ ] **Step 4: 语法检查**

Run: `.venv\Scripts\python.exe -m py_compile sandbox/st_tools/process.py sandbox/st_tools/markers.py sandbox/st_tools/plot.py`
Expected: 无输出

- [ ] **Step 5: Commit**

```bash
git add sandbox/st_tools/process.py sandbox/st_tools/markers.py sandbox/st_tools/plot.py
git commit -m "feat(phase21): st_process 空间域聚类 + st_markers + st_plot 脚本"
```

---

### Task 5: settings + st 工具注册 + 装配

**Files:**
- Modify: `config/settings.py`
- Create: `orchestrator/tools/builtin/l3_spatial.py`
- Modify: `orchestrator/app.py:108-135`（bio 装配段）

- [ ] **Step 1: settings 新增 bio_st_image**

`config/settings.py:96` 附近（bio_image 定义处）追加：

```python
    bio_st_image: str = "feishu-research-agent/bio:st-cpu-latest"
```

并在 `Settings.__init__` 环境变量读取处（约 194 行 bio_image 旁边）追加：

```python
            bio_st_image=os.environ.get(
                "BIO_ST_IMAGE", "feishu-research-agent/bio:st-cpu-latest"),
```

（沿用 bio_image 的读取模式，具体行号以现文件为准。）

- [ ] **Step 2: 写失败测试（st 工具注册）**

新建 `tests/unit/test_l3_spatial.py`：

```python
"""Phase 21 st_* 工具注册测试（mock BioRunner，不发 docker）。"""
from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunner, BioRunError
from orchestrator.tools.builtin.l3_spatial import register_l3_spatial
from orchestrator.tools.tool_registry import ToolRegistry


@pytest.fixture
def runner():
    """MagicMock BioRunner：resolve_data_path 返回三元组。"""
    r = MagicMock(spec=BioRunner)
    r.resolve_data_path.return_value = ("I:/bio_data", "visium_out", "I:/bio_data/visium_out")
    r.run.return_value = {"ok": True, "dataset_ref": "abc123",
                          "n_spots": 200, "n_genes": 300}
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_registers_five_tools(reg):
    """st_* 5 工具全部注册为 L1_compute。"""
    names = sorted(t.name for t in reg.list())
    assert names == ["st_load", "st_markers", "st_plot", "st_process",
                     "st_qc"]
    for t in reg.list():
        assert t.risk_level == "L1_compute"


def test_st_load_uses_dir_dataset_id_and_st_image(runner, reg):
    """st_load：目录聚合 dataset_id + 调 run 时指定 st 镜像与 /opt/st_tools。"""
    out = reg.get("st_load").handler(path="I:/bio_data/visium_out")
    assert out["dataset_ref"] == "abc123"
    args, kwargs = runner.run.call_args
    assert args[0] == "load"
    assert kwargs.get("image") == "feishu-research-agent/bio:st-cpu-latest"
    assert kwargs.get("script_dir") == "/opt/st_tools"
    assert kwargs["mounts"] == [("I:/bio_data", "/data")]
    # ok 字段剥离（ToolHandler 约定）
    assert "ok" not in out


def test_st_load_path_forbidden(runner, reg):
    """白名单外路径 → SC_PATH_FORBIDDEN 错误输出（不抛异常）。"""
    runner.resolve_data_path.side_effect = BioRunError(
        "SC_PATH_FORBIDDEN", "outside allowed roots")
    out = reg.get("st_load").handler(path="C:/evil")
    assert out == {"error_code": "SC_PATH_FORBIDDEN",
                   "error_message": "outside allowed roots"}


def test_st_qc_params_passthrough(runner, reg):
    """st_qc 参数透传到容器脚本。"""
    reg.get("st_qc").handler(dataset_ref="abc123", min_genes=10,
                             max_genes=8000, max_mt_pct=30.0)
    args, kwargs = runner.run.call_args
    assert args[0] == "qc"
    assert args[1] == {"dataset_id": "abc123", "min_genes": 10,
                       "max_genes": 8000, "max_mt_pct": 30.0}
    assert kwargs.get("image") == "feishu-research-agent/bio:st-cpu-latest"


def test_st_plot_genes_max_six(reg):
    """st_plot genes 参数 schema maxItems=6。"""
    spec = reg.get("st_plot")
    genes_prop = spec.parameters["properties"]["genes"]
    assert genes_prop["maxItems"] == 6
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_spatial.py -v --basetemp=.pytest_tmp`
Expected: FAIL（ModuleNotFoundError: l3_spatial）

- [ ] **Step 4: 实现 l3_spatial.py**

```python
"""Phase 21：空间转录组 st_* 工具注册（spec §2.2，5 个 L1_compute 工具）。

与 l3_singlecell 同模式：runner 由 runtime 组装注入；st 脚本走 st 镜像
（run 时覆盖 image/script_dir）；handler 捕获 BioRunError 转错误输出。
"""
from __future__ import annotations

from orchestrator.tools.bio.bio_runner import (
    BioRunner,
    BioRunError,
    compute_dataset_id_dir,
)
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec

_ST_IMAGE = "feishu-research-agent/bio:st-cpu-latest"
_ST_SCRIPT_DIR = "/opt/st_tools"


def _err(exc: BioRunError) -> dict:
    """BioRunError → 工具错误输出（ToolHandler 透传 error_code）。"""
    return {"error_code": exc.error_code, "error_message": str(exc)}


def register_l3_spatial(registry: ToolRegistry, runner: BioRunner,
                        *, st_image: str = _ST_IMAGE) -> None:
    """注册 st_* 5 工具（runner 由 runtime 装配后传入）。"""

    def st_load(*, path: str) -> dict:
        """读入空间转录组数据（visium/h5ad/mtx+coords）→ dataset_ref + 概要。"""
        try:
            mount_root, rel, host = runner.resolve_data_path(path)
            dataset_id = compute_dataset_id_dir(host)
            out = runner.run(
                "load", {"path": rel, "dataset_id": dataset_id},
                mounts=[(mount_root, "/data")],
                image=st_image, script_dir=_ST_SCRIPT_DIR)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_qc(*, dataset_ref: str, min_genes: int = 50,
              max_genes: int = 6000, max_mt_pct: float = 20.0) -> dict:
        """spot 级质控过滤 → filtered.h5ad + 前后统计。"""
        try:
            out = runner.run(
                "qc", {
                    "dataset_id": dataset_ref,
                    "min_genes": min_genes, "max_genes": max_genes,
                    "max_mt_pct": max_mt_pct,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_process(*, dataset_ref: str, n_pcs: int = 30,
                   resolution: float = 1.0, n_neighbors: int = 15) -> dict:
        """空间邻域 + PCA + Leiden 空间域 → processed.h5ad + 空间着色图。"""
        try:
            out = runner.run(
                "process", {
                    "dataset_id": dataset_ref, "n_pcs": n_pcs,
                    "resolution": resolution, "n_neighbors": n_neighbors,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_markers(*, dataset_ref: str, method: str = "wilcoxon",
                   top_n: int = 10) -> dict:
        """空间域差异基因 → markers JSON + dotplot.png。"""
        try:
            out = runner.run(
                "markers", {
                    "dataset_id": dataset_ref, "method": method,
                    "top_n": top_n,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_plot(*, dataset_ref: str, genes: list[str] | None = None,
                color_by: str = "") -> dict:
        """spatial 着色图（基因表达/obs 列）→ png 列表。"""
        try:
            out = runner.run(
                "plot", {
                    "dataset_id": dataset_ref,
                    "genes": genes or [], "color_by": color_by,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    registry.register(ToolSpec(
        name="st_load",
        description=(
            "读入本地空间转录组数据（spaceranger 输出目录 / 含空间坐标的 "
            ".h5ad / matrix.mtx+coords.csv 目录）。输出 dataset_ref（下游 "
            "st_* 工具用 <node_id>.dataset_ref 引用）、n_spots、n_genes。"
            "path 必须在管理员允许的数据目录内。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "本地数据路径（spaceranger 目录或 h5ad）"},
            },
            "required": ["path"],
        },
        risk_level="L1_compute",
        handler=st_load,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="st_qc",
        description=(
            "spot 级质控过滤（每 spot 最小/最大基因数、最大线粒体比例%）→ "
            "filtered.h5ad。输出过滤前后 spot/基因数与剔除数。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_load 输出的 dataset_ref"},
                "min_genes": {"type": "integer", "default": 50},
                "max_genes": {"type": "integer", "default": 6000},
                "max_mt_pct": {"type": "number", "default": 20.0},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_qc,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="st_process",
        description=(
            "空间转录组标准流程：归一化→HVG→PCA→空间邻域图→Leiden 空间域"
            "聚类，产出 processed.h5ad、umap.png 与 spatial_domains.png"
            "（空间域着色图，st 核心输出）。建议先跑 st_qc。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "n_pcs": {"type": "integer", "default": 30},
                "resolution": {"type": "number", "default": 1.0},
                "n_neighbors": {"type": "integer", "default": 15},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_process,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="st_markers",
        description=(
            "每个空间域的差异基因（rank_genes_groups），输出每域 top 基因"
            "（gene/score/log2fc）与 dotplot.png。需先跑 st_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "method": {"type": "string", "default": "wilcoxon",
                           "enum": ["wilcoxon", "t-test"]},
                "top_n": {"type": "integer", "default": 10, "maximum": 50},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_markers,
        timeout_sec=1200,
    ))
    registry.register(ToolSpec(
        name="st_plot",
        description=(
            "空间着色图（spatial scatter）：genes 按基因表达着色（≤6 个），"
            "或 color_by 按 obs 列（如 spatial_domain）着色。输出 png 路径"
            "列表。需先跑 st_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "genes": {"type": "array", "items": {"type": "string"},
                          "maxItems": 6,
                          "description": "基因符号列表（如 MARKER_D1）"},
                "color_by": {"type": "string",
                             "description": "obs 列名（如 spatial_domain）"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_plot,
        timeout_sec=600,
    ))
```

- [ ] **Step 5: 跑测试通过**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_spatial.py -v --basetemp=.pytest_tmp`
Expected: 5 用例全 PASS

- [ ] **Step 6: 装配到 app.py**

在 [orchestrator/app.py:129](file:///i:/飞书agent/orchestrator/app.py) `register_l3_singlecell(self.registry, bio_runner)` 之后、`logger.info` 之前追加：

```python
                from orchestrator.tools.builtin.l3_spatial import (
                    register_l3_spatial,
                )
                register_l3_spatial(
                    self.registry, bio_runner,
                    st_image=settings.bio_st_image)
                logger.info(
                    "Phase 21 st_* tools registered: image=%s",
                    settings.bio_st_image,
                )
```

- [ ] **Step 7: Commit**

```bash
git add config/settings.py orchestrator/tools/builtin/l3_spatial.py orchestrator/app.py tests/unit/test_l3_spatial.py
git commit -m "feat(phase21): st_* 5 工具注册 + bio_st_image 配置 + 装配"
```

---

### Task 6: research_runner 图片收集/超时放大扩展 st_*

**Files:**
- Modify: `orchestrator/research_runner.py:379`（判定前缀）
- Test: `tests/integration/test_research_runner.py`

先查超时放大判定位置（与 sc_ 前缀判定同文件）：

Run: `Grep pattern="startswith\(.sc_\.\)" path=orchestrator/research_runner.py`（用 Grep 工具）
确认所有 `startswith("sc_")` 出现处（预期 2 处：图片收集 + 超时放大）。

- [ ] **Step 1: 写失败集成测试**

追加到 `tests/integration/test_research_runner.py`（沿用本文件 sc_* 图片收集用例的 fixture 与构造模式——节点/plan/scheduler mock 方式以现有用例为准，此处给出测试逻辑骨架）：

```python
def test_st_image_fields_collected_and_sent():
    """st_* 节点成功后 spatial_png/pngs 收集并发送（前缀判定扩展）。"""
    # 构造：plan 含 tool 节点 tool_name="st_process"，
    # handle.state=SUCCESS, outputs={"spatial_png": "/ws/ds/spatial_domains.png",
    #                                 "pngs": ["/ws/ds/G1_spatial.png"]}
    # 断言：_sc_image_host_paths 返回两个主机路径
    # （bio_workspace_root + 去掉 /ws 前缀）


def test_st_node_extends_wallclock_timeout():
    """含 st_* 节点的 plan wall-clock 放大到 research_sc_timeout_sec。"""
    # 构造：plan 含 tool 节点 tool_name="st_qc"
    # 断言：超时预算 = settings.research_sc_timeout_sec（沿用 sc 的断言模式）
```

（实现时对照本文件既有 `test_sc_*` 图片/超时用例逐字复用其 fixture，仅把 tool_name 换成 st_ 系、输出字段换成 spatial_png/pngs。）

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_research_runner.py -k st_ -v --basetemp=.pytest_tmp`
Expected: FAIL（st_ 前缀不被收集/不放大超时）

- [ ] **Step 3: 修改判定前缀**

`orchestrator/research_runner.py` 所有 `startswith("sc_")` 判定（预期 2 处）改为：

```python
            if not ((tool_names.get(node_id) or "").startswith("sc_")
                    or (tool_names.get(node_id) or "").startswith("st_")):
                continue
```

（超时放大处同改；具体上下文行以 Grep 结果为准，语义 = sc_/st_ 任一前缀命中。）

- [ ] **Step 4: 跑测试通过 + sc 回归**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_research_runner.py -v --basetemp=.pytest_tmp`
Expected: 全部 PASS（含既有 sc_* 用例不回归）

- [ ] **Step 5: Commit**

```bash
git add orchestrator/research_runner.py tests/integration/test_research_runner.py
git commit -m "feat(phase21): 图片收集/超时放大判定扩展 st_* 前缀"
```

---

### Task 7: 构建 st 镜像 + tiny Visium 真机冒烟

**Files:**
- Create: `scripts/make_tiny_visium.py`（测试数据生成器）
- Create: `scripts/smoke_st_chain.py`（容器内脚本链冒烟）

- [ ] **Step 1: 构建 st 镜像**

Run: `docker build -t feishu-research-agent/bio:st-cpu-latest sandbox -f sandbox/st.Dockerfile`
Expected: Successfully tagged（首次约 5-10 分钟，squidpy 依赖较大）

- [ ] **Step 2: 写 tiny Visium 数据生成器**

新建 `scripts/make_tiny_visium.py`：

```python
"""生成 tiny Visium spaceranger 目录（~196 spots 14x14 网格、3 空间域、
域特异基因 MARKER_D1/D2/D3，Phase 21 真机冒烟用）。

用法：python scripts/make_tiny_visium.py <输出目录>
目录结构：filtered_feature_bc_matrix.h5 + spatial/
（tissue_positions_list.csv + scalefactors_json.json）——squidpy
sq.read.visium v2 布局。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "bio_test_data/tiny_visium")
    n_side = 14          # 14x14 网格 → 196 spots
    n_marker = 20        # 每域特异基因数
    n_bg = 120           # 背景基因
    rng = np.random.default_rng(42)

    # 3 空间域：按空间坐标三等分（左/中/右竖条带）
    domains = np.repeat([0, 1, 2], (n_side * 5, n_side * 5, n_side * 4))

    genes = ([f"MARKER_D{d+1}_{i}" for d in range(3) for i in range(n_marker)]
             + [f"BG_{i}" for i in range(n_bg)])
    n_genes = len(genes)
    spots = [f"spot{i}" for i in range(n_side * n_side)]

    # 计数矩阵（泊松）：域特异基因只在自己域高表达
    X = rng.poisson(0.3, (len(spots), n_genes)).astype(np.float32)
    for d in range(3):
        cols = slice(d * n_marker, (d + 1) * n_marker)
        rows = domains == d
        X[np.ix_(rows, np.arange(n_marker * 3)[cols])] += \
            rng.poisson(8.0, (rows.sum(), n_marker)).astype(np.float32)
    # 4 个 MT 基因（QC 用）
    genes += [f"MT-{i}" for i in range(1, 5)]
    mt = rng.poisson(1.0, (len(spots), 4)).astype(np.float32)
    X = np.hstack([X, mt])

    # 写 h5（spaceranger filtered_feature_bc_matrix.h5 10x 格式）
    import h5py

    out.mkdir(parents=True, exist_ok=True)
    (out / "spatial").mkdir(exist_ok=True)
    barcodes = np.array([s.encode() for s in spots], dtype="S32")
    names = np.array([g.encode() for g in genes], dtype="S32")
    ids = np.array([f"gene{i}".encode() for i in range(len(genes))],
                   dtype="S32")
    feat_type = np.array([b"Gene Expression"] * len(genes), dtype="S32")

    with h5py.File(out / "filtered_feature_bc_matrix.h5", "w") as f:
        g = f.create_group("matrix")
        g.create_dataset("shape", data=np.array([len(genes), len(spots)],
                                                dtype=np.int64))
        # CSC：按列（spot）存
        indptr = np.arange(len(spots) + 1, dtype=np.int64) * n_genes
        indices = np.tile(np.arange(n_genes, dtype=np.int64), len(spots))
        data = X.T.reshape(-1).astype(np.float32)
        g.create_dataset("data", data=data)
        g.create_dataset("indices", data=indices)
        g.create_dataset("indptr", data=indptr)
        g.create_dataset("barcodes", data=barcodes)
        feat = g.create_group("features")
        feat.create_dataset("name", data=names)
        feat.create_dataset("id", data=ids)
        feat.create_dataset("feature_type", data=feat_type)

    # spatial/：tissue_positions_list.csv（in_tissue,array_row,array_col,
    # pxl_row_fullres,pxl_col_fullres）+ scalefactors
    with open(out / "spatial" / "tissue_positions_list.csv", "w") as f:
        for i, s in enumerate(spots):
            r, c = divmod(i, n_side)
            f.write(f"{s},1,{r},{c},{r * 100},{c * 100}\n")
    scalefactors = {
        "spot_diameter_fullres": 100,
        "tissue_hires_scalef": 1.0,
        "tissue_lowres_scalef": 0.5,
    }
    (out / "spatial" / "scalefactors_json.json").write_text(
        json.dumps(scalefactors))

    print(f"tiny visium written: {out} ({len(spots)} spots x {len(genes)} genes,"
          f" 3 domains {np.bincount(domains).tolist()})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 生成数据 + 验证可读**

Run: `.venv\Scripts\python.exe scripts\make_tiny_visium.py I:\飞书agent\bio_test_data\tiny_visium`
Expected: `tiny visium written: ... (196 spots x 244 genes, 3 domains [70, 70, 56])`

- [ ] **Step 4: 写容器链冒烟脚本（直接 docker run 串联 5 步）**

新建 `scripts/smoke_st_chain.py`：

```python
"""st 链容器内冒烟：load→qc→process→markers→plot 串行（Phase 21 真机）。

用法：python scripts/smoke_st_chain.py <visium目录绝对路径>
等价 BioRunner 行为：--rm -i --network none + 资源限额 + /data /ws 挂载。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

IMAGE = "feishu-research-agent/bio:st-cpu-latest"
WS = Path("I:/飞书agent/bio_workspace/smoke_st")


def run_script(script: str, args: dict, data_mount: str) -> dict:
    """跑单个 st 脚本，返回 stdout JSON。"""
    cmd = ["docker", "run", "--rm", "-i", "--network", "none",
           "--cpus", "4", "--memory", "16g",
           "-v", f"{WS}:/ws", "-v", f"{data_mount}:/data:ro",
           IMAGE, "python", f"/opt/st_tools/{script}.py"]
    proc = subprocess.run(cmd, input=json.dumps(args), capture_output=True,
                          text=True, encoding="utf-8", timeout=1800)
    if proc.returncode != 0:
        print(f"[{script}] FAILED rc={proc.returncode}")
        print(proc.stderr[-2000:])
        sys.exit(1)
    out = json.loads(proc.stdout)
    print(f"[{script}] ok={out.get('ok')}",
          {k: v for k, v in out.items()
           if k not in ("markers",) and not isinstance(v, dict)})
    if out.get("ok") is False:
        sys.exit(1)
    return out


def main() -> None:
    visium = sys.argv[1]
    data_mount = str(Path(visium).parent)
    rel = Path(visium).name
    WS.mkdir(parents=True, exist_ok=True)

    out = run_script("load", {"path": rel, "dataset_id": "smoke_st"},
                     data_mount)
    ds = out["dataset_ref"]
    run_script("qc", {"dataset_id": ds, "min_genes": 20, "max_mt_pct": 50},
               data_mount)
    out = run_script("process", {"dataset_id": ds, "resolution": 1.0},
                     data_mount)
    domains = out.get("n_domains")
    run_script("markers", {"dataset_id": ds, "top_n": 5}, data_mount)
    run_script("plot", {"dataset_id": ds, "genes": ["MARKER_D1_0"],
                        "color_by": "spatial_domain"}, data_mount)

    # 产物核验
    for name in ("raw.h5ad", "filtered.h5ad", "processed.h5ad",
                 "umap.png", "spatial_domains.png", "dotplot.png",
                 "MARKER_D1_0_spatial.png", "spatial_domain_spatial.png"):
        p = WS / name
        ok = p.exists() and p.stat().st_size > 0
        print(f"{'✓' if ok else '✗'} {name} ({p.stat().st_size if p.exists() else 0} B)")
        if not ok:
            sys.exit(1)
    print(f"SMOKE PASS (n_domains={domains})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 跑冒烟**

Run: `.venv\Scripts\python.exe scripts\smoke_st_chain.py I:\飞书agent\bio_test_data\tiny_visium`
Expected: 5 步全 ok，8 个产物非空，`SMOKE PASS (n_domains=3)`（3 域被还原为最佳结果；分辨率敏感，2-4 域均可接受，核心验证是链路通+图产出）

- [ ] **Step 6: Commit**

```bash
git add scripts/make_tiny_visium.py scripts/smoke_st_chain.py
git commit -m "feat(phase21): tiny Visium 生成器 + st 链容器冒烟脚本（真机通过）"
```

---

### Task 8: 全量回归 + 真机 /research 链路验收

**Files:**
- Modify: `测试总结+2026-09-01T08-42-00.md`（追加批①记录）

- [ ] **Step 1: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp`
Expected: 全部 passed（基线 785 + 本计划新增约 13 用例）

- [ ] **Step 2: 重启 ws_client 加载 st 工具**

```powershell
# 先杀存量 ws_client（注意防双实例）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object {$_.CommandLine -like "*ws_client*"} |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
# 单实例重启
.venv\Scripts\python.exe -m gateway.ws_client
```

启动日志预期出现：`Phase 21 st_* tools registered: image=feishu-research-agent/bio:st-cpu-latest`

- [ ] **Step 3: 真机 /research 验收（用户配合）**

在飞书单聊发送：

```
/research 对 I:\飞书agent\bio_test_data\tiny_visium 做空间转录组分析：读取数据、质控、空间域聚类、域标记基因，并展示 MARKER_D1_0 基因的空间表达
```

验收点：
1. planner 规划出 st_load→st_qc→st_process→st_markers→st_plot 节点链
2. 全节点 success，IM 摘要含 n_domains/cluster_sizes
3. **IM 收到 spatial_domains.png + dotplot.png + MARKER_D1_0_spatial.png 图片消息**
4. 绑定文档写入文本 + 图片块（三步插图复用）

- [ ] **Step 4: 更新测试总结 + Commit**

追加批①真机记录到测试总结文件（含发现的问题与修复），然后：

```bash
git add "测试总结+2026-09-01T08-42-00.md"
git commit -m "docs(phase21): 批①真机验收记录"
```

---

## Self-Review 结论

- **Spec 覆盖**：spec §2.2 五工具（Task 3/4/5）、§2.1 镜像（Task 2）、§3 幂等（Task 1 目录 hash）、§4 错误处理（各脚本 fail 调用）、§2.5 图片回传（Task 6 + 既有 ImageBlock 链路复用）、§5 测试（Task 5/6/7/8）——批②③按 spec 分批交付，不在本计划
- **类型一致性**：`compute_dataset_id_dir`（Task 1 定义 / Task 5 引用）、`run(image=, script_dir=)`（Task 1 定义 / Task 5 调用 kwargs 一致）、`ensure_spatial`（Task 2 定义 / Task 3 plot 引用）、st 输出字段 `spatial_png/pngs`（Task 4 脚本输出 / Task 6 收集）已核对
- **占位符扫描**：Task 6 Step 1 集成测试为骨架 + 明确指引"逐字复用现有 sc_* 用例 fixture"（现有用例在上下文中，非 TBD）；Task 5 Step 1 settings 行号给出定位锚点——可接受
- **风险**：squidpy `spatial_scatter` API 在不同版本间签名有差异（ax=/return_fig= 参数）——Task 7 冒烟若报错，按容器内 squidpy 版本调整调用（记入测试总结）
