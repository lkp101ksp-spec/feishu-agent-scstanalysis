# st_vicinity 肿瘤邻域分层 Implementation Plan（Phase 48）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** st_* 第 12 工具 st_vicinity——以 st_cnv 恶性 spot 为种子沿空间邻居图 BFS 分层（tumor/L1..Ln/distal），标签与统计产物写回。

**Architecture:** 宿主 handler（l3_spatial.py）→ BioRunner → st 镜像容器内 `sandbox/st_tools/vicinity.py`。种子=obs["is_malignant"]，邻居图复用 obsp["spatial_connectivities"]（缺失补建），BFS 用 `scipy.sparse.csgraph.shortest_path(unweighted=True)` 一次求距离场再分层。**镜像零改动**。

**Tech Stack:** scipy.sparse.csgraph / squidpy spatial_scatter / Docker st 镜像 / pytest + mock BioRunner。

**Spec:** `docs/superpowers/specs/2026-09-11-st-vicinity-design.md`

**已核实事实（Phase 45-47 探针沉淀，无需新探针）：**
- st `common.py` 契约、`spatial_scatter` 占位壳模式、`read_h5ad` stub 标 Any、`fail()+SystemExit(1)`
- `_build_neighbors`（st_stats.py L28）：obsp 已有 spatial_connectivities 直通；grid → `sq.gr.spatial_neighbors(coord_type="grid", n_neighs=n_neighs)`；generic → `delaunay=True`
- deconv.h5ad 组成矩阵键 `obsm["q05_cell_abundance_w_sf"]` + 前缀 strip（st_niche 同款加载器）
- l3_spatial.py 文件末尾现为 st_niche ToolSpec 块；section_digest.py L56 `"st_niche": "空间生态位重构",` 行后插入
- `test_l3_spatial.py` 两处 11 工具断言（test_st_registers_eleven_tools / test_register_eleven_st_tools）
- 合成网格坐标的邻居图：**冒烟用 coord_type="generic"（delaunay）**——grid/n_neighs=6 是 Visium 六边网格语义，整数 meshgrid 上行为不定
- 门禁口径：`pytest -q -m "not pg"` 基线 1255 passed, 6 deselected

---

### Task 1: 红灯——st_vicinity 注册测试

**Files:**
- Create: `tests/unit/test_l3_st_vicinity.py`

- [ ] **Step 1: 写失败测试**

```python
"""st_vicinity 注册测试（mock BioRunner，不发 docker）。"""
from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_spatial import register_l3_spatial
from orchestrator.tools.tool_registry import ToolRegistry

ST_IMAGE = "feishu-research-agent/bio:st-cpu-latest"


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.run.return_value = {"ok": True, "dataset_ref": "abc123",
                          "n_tumor": 72, "max_layers": 5,
                          "layer_sizes": {"tumor": 72, "L1": 12}}
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_vicinity_registered(reg):
    """st_* 第 12 工具注册为 L1_compute，timeout 600，max_layers 默认 5。"""
    spec = reg.get("st_vicinity")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 600
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert props["max_layers"]["default"] == 5
    assert "coord_type" in props


def test_st_vicinity_forwards_params(runner, reg):
    """全参数透传 + st 镜像 + /opt/st_tools + timeout 600。"""
    reg.get("st_vicinity").handler(
        dataset_ref="abc123", max_layers=3, coord_type="generic")
    args, kw = runner.run.call_args
    assert args[0] == "vicinity"
    assert args[1] == {"dataset_id": "abc123", "max_layers": 3,
                       "coord_type": "generic"}
    assert kw["image"] == ST_IMAGE
    assert kw["script_dir"] == "/opt/st_tools"
    assert kw["timeout_sec"] == 600


def test_st_vicinity_defaults(runner, reg):
    """默认值：max_layers=5 / coord_type='grid'。"""
    reg.get("st_vicinity").handler(dataset_ref="abc123")
    args, _ = runner.run.call_args
    assert args[1]["max_layers"] == 5
    assert args[1]["coord_type"] == "grid"


def test_st_vicinity_ok_stripped(runner, reg):
    """emit 的 ok 键被 handler 摘除（l3 全家同口径）。"""
    out = reg.get("st_vicinity").handler(dataset_ref="abc123")
    assert "ok" not in out
    assert out["n_tumor"] == 72


def test_st_vicinity_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出。"""
    runner.run.side_effect = BioRunError(
        "ST_VICINITY_NO_SEED", "obs 缺 is_malignant 列")
    out = reg.get("st_vicinity").handler(dataset_ref="abc123")
    assert out == {"error_code": "ST_VICINITY_NO_SEED",
                   "error_message": "obs 缺 is_malignant 列"}
```

- [ ] **Step 2: 跑测试确认红**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_st_vicinity.py -q --basetemp=I:\飞书agent\.pytest_tmp`
Expected: FAIL——ToolNotFoundError（5 例全红）

- [ ] **Step 3: Commit（红灯落库）**

```bash
git add tests/unit/test_l3_st_vicinity.py
git commit -m "test(st): st_vicinity 注册红灯测试 5 例（Phase 48 Task1）"
```

---

### Task 2: 绿灯——l3_spatial 注册 + SECTION_TITLES + 计数断言

**Files:**
- Modify: `orchestrator/tools/builtin/l3_spatial.py`（handler 插 st_niche handler 后；ToolSpec 插文件末尾 st_niche 注册块后）
- Modify: `orchestrator/report/section_digest.py`（st_niche 行后加一行）
- Modify: `tests/unit/test_l3_spatial.py`（两处 11→12）

- [ ] **Step 1: l3_spatial.py 加 handler（st_niche handler 之后）**

```python
    def st_vicinity(*, dataset_ref: str, max_layers: int = 5,
                    coord_type: str = "grid") -> dict[str, Any]:
        """肿瘤邻域分层：恶性种子沿空间邻居图 BFS 分层写回。"""
        try:
            out = runner.run(
                "vicinity", {
                    "dataset_id": dataset_ref,
                    "max_layers": max_layers,
                    "coord_type": coord_type,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out
```

- [ ] **Step 2: 文件末尾加 ToolSpec 注册**

```python
    registry.register(ToolSpec(
        name="st_vicinity",
        description=(
            "肿瘤邻域分层：以 st_cnv 判定的恶性 spot（obs['is_malignant']）"
            "为种子，沿空间邻居图 BFS 向外分层（tumor / L1..Ln / distal），"
            "刻画肿瘤核心→侵袭前沿→远端梯度；输出分层空间着色图、层尺寸"
            "csv，deconv.h5ad 存在时追加层×细胞型组成热图（免疫/基质随"
            "距离梯度）；obs['vicinity'] 写回 processed.h5ad（st_plot 可"
            "着色、st_stats 可作 cluster_key）。需先跑 st_process 与 "
            "st_cnv。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_load 输出的 dataset_ref"},
                "max_layers": {"type": "integer", "default": 5,
                               "description": "BFS 最大层数（1..10）"},
                "coord_type": {"type": "string", "default": "grid",
                               "enum": ["grid", "generic"],
                               "description": "补建邻居图坐标类型"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_vicinity,
        timeout_sec=600,
    ))
```

- [ ] **Step 3: section_digest.py 加映射**

```python
    "st_niche": "空间生态位重构",
    "st_vicinity": "肿瘤邻域分层",
```

- [ ] **Step 4: test_l3_spatial.py 两处 11→12**

test_st_registers_eleven_tools → 改名 test_st_registers_twelve_tools，
docstring 改「st_* 12 工具全部注册为 L1_compute（Phase 48 +st_vicinity）」，
断言列表改：

```python
    assert names == ["st_cnv", "st_commot", "st_deconvolve", "st_domains",
                     "st_load", "st_markers", "st_niche", "st_plot",
                     "st_process", "st_qc", "st_stats", "st_vicinity"]
```

test_register_eleven_st_tools → 改名 test_register_twelve_st_tools，
docstring 改「Phase 48 后 st_* 共 12 工具（11 + st_vicinity）」，断言列表改：

```python
    assert names == [
        "st_cnv", "st_commot", "st_deconvolve", "st_domains", "st_load",
        "st_markers", "st_niche", "st_plot", "st_process", "st_qc",
        "st_stats", "st_vicinity"]
```

- [ ] **Step 5: 跑测试确认绿**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_st_vicinity.py tests/unit/test_l3_spatial.py tests/unit/test_report_digest.py -q --basetemp=I:\飞书agent\.pytest_tmp`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add orchestrator/tools/builtin/l3_spatial.py orchestrator/report/section_digest.py tests/unit/test_l3_spatial.py
git commit -m "feat(st): 注册 st_vicinity 工具 + SECTION_TITLES 映射（Phase 48 Task2）"
```

---

### Task 3: st_tools/vicinity.py 容器脚本

**Files:**
- Create: `sandbox/st_tools/vicinity.py`

- [ ] **Step 1: 写脚本**

```python
"""st_vicinity：肿瘤邻域分层（Phase 48）——恶性种子沿空间邻居图 BFS。

stdin: {"dataset_id": ..., "max_layers": 5, "coord_type": "grid"}
种子=obs["is_malignant"]==True（st_cnv 写回；缺列/零恶性报
ST_VICINITY_NO_SEED）。邻居图复用 obsp["spatial_connectivities"]
（st_process 已建；缺失按 coord_type 补建，st_stats 同款）。BFS 用
scipy.sparse.csgraph.shortest_path(unweighted=True) 一次求全图距离场：
dist=0 → tumor，1..max_layers → L1..Ln，其余（含不可达 inf）→ distal。
obs["vicinity"] 写回 processed.h5ad。产物落 /ws/{ds}/vicinity/：
vicinity_spatial.png + vicinity_layer_sizes.csv；deconv.h5ad 存在时
追加 vicinity_composition.csv + vicinity_composition_heatmap.png
（层×细胞型均值组成，行按肿瘤 proximity 排序）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from common import (
    WS_ROOT,
    emit,
    ensure_spatial,
    fail,
    load_adata,
    read_args,
    run,
)

ABUND_KEY = "q05_cell_abundance_w_sf"
ABUND_PREFIX = "q05cell_abundance_w_sf_"
COORD_TYPES = ("grid", "generic")


def _build_neighbors(adata: Any, coord_type: str, n_neighs: int = 6) -> None:
    """复用 st_process 已建邻居图；缺失才按 coord_type 补建（st_stats 同款）。"""
    if "spatial_connectivities" in adata.obsp:
        return
    import squidpy as sq
    if coord_type == "generic":
        sq.gr.spatial_neighbors(adata, coord_type="generic", delaunay=True)
    else:
        sq.gr.spatial_neighbors(adata, coord_type="grid", n_neighs=n_neighs)


def _layer_labels(dist: np.ndarray, seed: np.ndarray,
                  max_layers: int) -> np.ndarray:
    """距离场 → 层标签：0=tumor，1..max=L1..Ln，其余=distal。"""
    labels = np.full(len(dist), "distal", dtype=object)
    labels[seed] = "tumor"
    for layer in range(1, max_layers + 1):
        labels[(dist == layer) & ~seed] = f"L{layer}"
    return labels


def _vicinity_spatial_png(adata: Any, png_path: Path) -> None:
    """分层着色 spatial_scatter（占位壳模式；类目按 tumor→Ln→distal 排序）。"""
    import matplotlib.pyplot as plt
    import squidpy as sq
    sp = adata.uns.get("spatial")
    if not isinstance(sp, dict) or not sp:
        adata.uns["spatial"] = {"_placeholder": {
            "images": {"hires": np.zeros((8, 8, 3))},
            "scalefactors": {"tissue_hires_scalef": 1.0,
                             "spot_diameter_fullres": 1.0}}}
        img_kw: dict[str, Any] = {"img": False}
    else:
        has_img = any(isinstance(lib, dict) and lib.get("images")
                      for lib in sp.values())
        img_kw = {} if has_img else {"img": False}
    ax = sq.pl.spatial_scatter(adata, color=["vicinity"],
                               return_ax=True, **img_kw)
    ax.set_title("Tumor vicinity layers")
    ax.figure.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close("all")


def _composition_products(adata: Any, dataset_id: str, labels: pd.Series,
                          out_dir: Path) -> tuple[Path | None, Path | None]:
    """层×细胞型均值组成 csv+热图（deconv.h5ad 存在才做，缺失返回 (None, None)）。

    行按肿瘤 proximity 排序（tumor→L1..→distal）；组成矩阵行归一化
    （st_niche 同款加载逻辑）。
    """
    p = WS_ROOT / dataset_id / "deconv.h5ad"
    if not p.exists():
        return None, None
    import anndata as ad
    import matplotlib.pyplot as plt
    dec: Any = ad.read_h5ad(p)  # stub 返回 Any | Dataset2D，标 Any 收窄
    if ABUND_KEY not in dec.obsm:
        return None, None
    abund = dec.obsm[ABUND_KEY]
    if not isinstance(abund, pd.DataFrame):
        abund = pd.DataFrame(np.asarray(abund), index=dec.obs_names)
    abund = abund.copy()
    abund.columns = [str(c).replace(ABUND_PREFIX, "")
                     for c in abund.columns]
    common = labels.index.intersection(abund.index)
    if len(common) < 100:
        return None, None
    comp = abund.loc[common].astype(float)
    row_sum = comp.sum(axis=1)
    row_sum = row_sum.where(row_sum > 0, 1.0)
    comp = comp.div(row_sum, axis=0)
    mat = comp.groupby(labels.loc[common]).mean()
    order = ["tumor"] + sorted(
        [i for i in mat.index if str(i).startswith("L")],
        key=lambda s: int(str(s)[1:])) + ["distal"]
    mat = mat.loc[[i for i in order if i in mat.index]]
    csv_path = out_dir / "vicinity_composition.csv"
    mat.to_csv(csv_path, index_label="vicinity")
    png_path = out_dir / "vicinity_composition_heatmap.png"
    fig, ax = plt.subplots(
        figsize=(max(6.0, mat.shape[1] * 0.5),
                 max(3.5, mat.shape[0] * 0.4 + 1.5)))
    im = ax.imshow(mat.to_numpy(), aspect="auto", cmap="viridis")
    ax.set_xticks(range(mat.shape[1]))
    ax.set_xticklabels([str(c) for c in mat.columns], rotation=45,
                       ha="right", fontsize=7)
    ax.set_yticks(range(mat.shape[0]))
    ax.set_yticklabels([str(i) for i in mat.index], fontsize=8)
    fig.colorbar(im, ax=ax, shrink=0.7, label="mean composition")
    ax.set_title("Vicinity layer composition (tumor proximity order)",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return csv_path, png_path


def main() -> None:
    """主流程：种子/邻居图 → BFS 距离场分层 → 写回+产物 → emit。"""
    args = read_args()
    max_layers = int(args.get("max_layers", 5))
    coord_type = str(args.get("coord_type", "grid"))
    if max_layers < 1 or max_layers > 10:
        fail("INVALID_INPUT",
             f"max_layers={max_layers} 越界（需 1..10）")
        raise SystemExit(1)
    if coord_type not in COORD_TYPES:
        fail("INVALID_INPUT",
             f"coord_type={coord_type!r} 非白名单 {COORD_TYPES}")
        raise SystemExit(1)

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    ensure_spatial(adata)
    if "is_malignant" not in adata.obs:
        fail("ST_VICINITY_NO_SEED",
             "obs 缺 is_malignant 列；先跑 st_cnv")
        raise SystemExit(1)
    seed = adata.obs["is_malignant"].astype(bool).to_numpy()
    n_tumor = int(seed.sum())
    if n_tumor == 0:
        fail("ST_VICINITY_NO_SEED",
             "is_malignant 全 False（st_cnv 未检出恶性）；"
             "确认数据含肿瘤或检查 st_cnv 参考选择")
        raise SystemExit(1)

    _build_neighbors(adata, coord_type)
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import shortest_path
    graph = csr_matrix(adata.obsp["spatial_connectivities"])
    dist = shortest_path(graph, directed=False, unweighted=True,
                         indices=np.where(seed)[0]).min(axis=0)
    labels = _layer_labels(dist, seed, max_layers)
    vicinity = pd.Series(labels, index=adata.obs_names, name="vicinity")
    layer_order = ["tumor"] + [f"L{i}" for i in range(1, max_layers + 1)] \
        + ["distal"]
    adata.obs["vicinity"] = pd.Categorical(
        vicinity, categories=layer_order, ordered=True)

    out_dir = WS_ROOT / args["dataset_id"] / "vicinity"
    out_dir.mkdir(parents=True, exist_ok=True)
    sizes = vicinity.value_counts()
    sizes = sizes.reindex([i for i in layer_order if i in sizes.index])
    sizes_csv = out_dir / "vicinity_layer_sizes.csv"
    sizes.rename_axis("vicinity").to_csv(sizes_csv, header=["n_spots"])
    sp_png = out_dir / "vicinity_spatial.png"
    _vicinity_spatial_png(adata, sp_png)
    comp_csv, comp_png = _composition_products(
        adata, args["dataset_id"], vicinity, out_dir)

    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    adata.write(h5ad_path)

    pngs = [str(sp_png)] + ([str(comp_png)] if comp_png else [])
    n_reached = int((vicinity != "distal").sum())
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "max_layers": max_layers,
        "n_tumor": n_tumor,
        "n_reached": n_reached,
        "layer_sizes": {str(k): int(v) for k, v in sizes.items()},
        "has_composition": comp_png is not None,
        # pngs 聚合键：IM 发图与 D 报告共用宿主四键收集
        "pngs": pngs,
        "vicinity_spatial_png": str(sp_png),
        "layer_sizes_csv": str(sizes_csv),
        "composition_csv": str(comp_csv) if comp_csv else None,
        "composition_heatmap_png": str(comp_png) if comp_png else None,
        "saved": str(h5ad_path),
        "note": "BFS 距离场分层（unweighted shortest_path）；"
                "obs['vicinity'] 已写回 processed.h5ad"
                + ("" if comp_png else "；deconv.h5ad 缺失，层×细胞型"
                                        "组成统计跳过"),
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: 语法 + 门禁自检**

Run: `.venv\Scripts\python.exe -m py_compile sandbox/st_tools/vicinity.py; .venv\Scripts\ruff.exe check sandbox/st_tools/vicinity.py; .venv\Scripts\mypy.exe sandbox/st_tools/vicinity.py`
Expected: 全绿

- [ ] **Step 3: 重建镜像（COPY 层内容寻址失效）+ Commit**

```bash
docker build -t feishu-research-agent/bio:st-cpu-latest sandbox -f sandbox/st.Dockerfile
git add sandbox/st_tools/vicinity.py
git commit -m "feat(st): st_vicinity 容器脚本（Phase 48 Task3）"
```

---

### Task 4: 断网容器冒烟（恶性种子 BFS 分层）

**Files:**
- Create: `scripts/_smoke_st_vicinity.py`

- [ ] **Step 1: 写冒烟脚本**

```python
"""st_vicinity 容器冒烟：合成恶性种子 BFS 分层真跑（断网）。

12×12 网格左半（x<6）is_malignant=True 作种子（72 个），coord_type
走 generic（delaunay——grid/n_neighs=6 是 Visium 六边网格语义，整数
meshgrid 上行为不定）。配套 deconv.h5ad：Tumor 组成随 x 递减、
T cells 随 x 递增（层×细胞型组成梯度断言用）。调用走 BioRunner 约定。
"""
import json
import subprocess
from pathlib import Path

WS = Path("I:/飞书agent/bio_workspace")
DS = "stvicsmoke"
DS_NOSEED = "stvicnoseed"
IMG = "feishu-research-agent/bio:st-cpu-latest"
BASE = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG]

_BUILDER = r'''
import numpy as np
import pandas as pd
import anndata as ad

xs, ys = np.meshgrid(np.arange(12), np.arange(12))
coords = np.column_stack([xs.ravel(), ys.ravel()]).astype(float)
n = coords.shape[0]
x = coords[:, 0]
barcodes = [f"s{i}" for i in range(n)]
obs = pd.DataFrame({"is_malignant": x < 6}, index=barcodes)
proc = ad.AnnData(X=np.zeros((n, 10), dtype=np.float32), obs=obs,
                  var=pd.DataFrame(index=[f"g{i}" for i in range(10)]))
proc.obsm["spatial"] = coords
proc.write_h5ad("/ws/stvicsmoke/processed.h5ad")
# 无种子列对照
proc2 = ad.AnnData(X=np.zeros((n, 10), dtype=np.float32),
                   obs=pd.DataFrame(index=barcodes), var=proc.var)
proc2.obsm["spatial"] = coords
proc2.write_h5ad("/ws/stvicnoseed/processed.h5ad")
# 组成矩阵：Tumor 随 x 递减、T cells 随 x 递增
abund = np.column_stack([
    np.maximum(0.1, 8.0 - x),      # Tumor
    np.maximum(0.2, 0.6 * x),      # T cells
    np.full(n, 1.0),               # Fibroblast
]).astype(np.float32)
dec = ad.AnnData(X=np.zeros((n, 3), dtype=np.float32),
                 obs=pd.DataFrame(index=barcodes),
                 var=pd.DataFrame(index=["g0", "g1", "g2"]))
dec.obsm["q05_cell_abundance_w_sf"] = pd.DataFrame(
    abund, index=barcodes,
    columns=["q05cell_abundance_w_sf_Tumor",
             "q05cell_abundance_w_sf_T cells",
             "q05cell_abundance_w_sf_Fibroblast"])
dec.write_h5ad("/ws/stvicsmoke/deconv.h5ad")
print("built", n)
'''


def build_dataset() -> None:
    """容器内构造合成数据集（含无种子列对照）。"""
    bdir = WS / "_builder_stvic"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "build_data.py").write_text(_BUILDER, encoding="utf-8")
    (WS / DS).mkdir(parents=True, exist_ok=True)
    (WS / DS_NOSEED).mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", "none",
         "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
         IMG, "python", "/ws/_builder_stvic/build_data.py"],
        capture_output=True, timeout=600)
    assert out.returncode == 0, out.stderr.decode()[-2000:]


def run_vicinity(ds: str, **kw):
    """容器内跑 vicinity.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": ds, "coord_type": "generic", **kw})
    r = subprocess.run(BASE + ["python", "/opt/st_tools/vicinity.py"],
                       input=payload, capture_output=True, text=True,
                       timeout=900)
    # 脚本级 fail() 也是 exit 1 + stdout JSON（BioRunner 同款口径）；
    # 仅当 stdout 无法解析为 JSON 才算执行崩溃
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n"
                         f"{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


build_dataset()

o = run_vicinity(DS, max_layers=2)
assert o["ok"], o
assert o["n_tumor"] == 72, o
assert o["has_composition"] is True, o
assert "distal" in o["layer_sizes"], o["layer_sizes"]
assert len(o["pngs"]) == 2
ds_dir = WS / DS
for f in ("vicinity/vicinity_spatial.png", "vicinity/vicinity_layer_sizes.csv",
          "vicinity/vicinity_composition.csv",
          "vicinity/vicinity_composition_heatmap.png"):
    assert (ds_dir / f).exists(), f

# 分层空间结构 + 组成梯度（容器内回读）
check = subprocess.run(
    ["docker", "run", "--rm", "--network", "none",
     "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG,
     "python", "-c",
     "import anndata as ad, pandas as pd\n"
     "a = ad.read_h5ad('/ws/stvicsmoke/processed.h5ad')\n"
     "x = a.obsm['spatial'][:, 0]; v = a.obs['vicinity'].astype(str)\n"
     "assert (x[v == 'tumor'] < 6).all()\n"
     "assert (x[v == 'L1'] >= 6).all() and (x[v == 'L1'] <= 7).all()\n"
     "assert (x[v == 'distal'] >= 6).all()\n"
     "m = pd.read_csv('/ws/stvicsmoke/vicinity/vicinity_composition.csv',"
     " index_col=0)\n"
     "assert m.loc['tumor', 'Tumor'] > m.loc['distal', 'Tumor']\n"
     "assert m.loc['distal', 'T cells'] > m.loc['tumor', 'T cells']\n"
     "print('layer-ok')"],
    capture_output=True, timeout=300)
assert b"layer-ok" in check.stdout, check.stderr.decode()[-2000:]

# max_layers 越界 → INVALID_INPUT
bad = run_vicinity(DS, max_layers=99)
assert not bad["ok"] and bad["error_code"] == "INVALID_INPUT", bad

# 无种子列 → ST_VICINITY_NO_SEED
no_seed = run_vicinity(DS_NOSEED)
assert not no_seed["ok"] and no_seed["error_code"] == "ST_VICINITY_NO_SEED", no_seed

print("SMOKE OK | tumor:", o["n_tumor"],
      "| layers:", o["layer_sizes"],
      "| layer-ok + composition gradient | INVALID_INPUT/NO_SEED rejected")
```

- [ ] **Step 2: 跑冒烟**

Run: `.venv\Scripts\python.exe scripts\_smoke_st_vicinity.py`
Expected: `SMOKE OK | tumor: 72 | ...`

- [ ] **Step 3: 冒烟产物自删 + Commit**

```bash
Remove-Item -Recurse -Force bio_workspace/stvicsmoke, bio_workspace/stvicnoseed, bio_workspace/_builder_stvic
git add scripts/_smoke_st_vicinity.py
git commit -m "test(st): st_vicinity 断网容器冒烟（恶性种子 BFS 分层）（Phase 48 Task4）"
```

---

### Task 5: 全量门禁 + ROADMAP/测试总结 + 推送 CI

**Files:**
- Modify: `docs/ROADMAP.md`（尾部加 Phase 48 行）
- Modify: `测试总结+2026-09-09T01-55-00.md`（追加十五）

- [ ] **Step 1: 全量回归（门禁口径）**

Run: `.venv\Scripts\python.exe -m pytest -q -m "not pg" --basetemp=I:\飞书agent\.pytest_tmp > reg_out.txt 2>&1; (Select-String -Path reg_out.txt -Pattern " passed").Line; Remove-Item reg_out.txt`
Expected: `1260 passed, 6 deselected`（1255 基线 + 5）

- [ ] **Step 2: ROADMAP 尾部加 Phase 48 行**

- [ ] **Step 3: 测试总结追加十五**（背景/实施/验证/教训四节格式）

- [ ] **Step 4: Commit + 推送（pre-push 四道门）+ CI 轮询至 success**

---

## Self-Review 记录

1. **Spec coverage**：§1 形态→Task2；§2 种子/邻居图/BFS/写回→Task3；§3 参数三件→Task1 测试+Task2 handler+Task3 白名单校验；§4 产物（含条件组成）→Task3+Task4 断言（has_composition/pngs 2 件/梯度回读）；§5 错误码三件→Task3+Task4 两路径（ST_FORMAT_INVALID 由 ensure_spatial 既有覆盖）；§6 测试三件→Task1/4/5。无非目标越界。
2. **Placeholder 扫描**：无 TBD/TODO；代码块完整。
3. **类型一致性**：handler 参数 dataset_ref/max_layers/coord_type ↔ 脚本 args 同名；emit 键 layer_sizes/has_composition ↔ 冒烟断言一致；组成键 q05_cell_abundance_w_sf 与 st_niche 加载器一致。
