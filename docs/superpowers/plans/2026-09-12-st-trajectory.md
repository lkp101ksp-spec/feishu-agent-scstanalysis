# st_trajectory 空间拟时序实施计划（Phase 51）

> **For agentic workers:** 按 Task 顺序执行，TDD：红灯→绿灯→脚本层→冒烟→门禁。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** st_* 第 14 工具——表达邻居图 DPT + PAGA 推断 spot 进程序并映射回组织空间坐标，root_mode=marker|vicinity 双模式定根。

**Architecture:** 宿主 handler（l3_spatial.py）→ BioRunner → st 镜像容器脚本（st_tools/trajectory.py，sc_tools/pseudotime.py Phase 32 模式移植：UMAP 散点→组织空间散点、leiden→spatial_domain、增 vicinity 双模式定根与分层耦合产物）。**镜像零 pip 改动**（scanpy/squidpy 已在），仅 COPY 层快照需秒级重建。

**Tech Stack:** scanpy（diffmap/dpt/paga 内置离线）、scipy（spearmanr）、matplotlib、pytest、docker。

**Spec:** `docs/superpowers/specs/2026-09-12-st-trajectory-design.md`

**既有事实（已勘探）：**
- st_process 产物 DPT-ready：`uns['neighbors']`（表达图，process.py L42）+ `obsm['X_pca']/obsm['spatial']` + `obs['spatial_domain']` + `adata.raw`（L31 log-normalized 快照，root_marker 提取与 sc 同款）
- st_vicinity 写回 `obs['vicinity']` 有序 Categorical（tumor/L1..Ln/distal）
- 注册样式：`registry.register(ToolSpec(..., parameters={...}))`；l3 handler 插 st_misty 后（L252 起）；SECTION_TITLES 现 37 项（L58 st_misty 后插入）
- test_l3_spatial.py 两处 13 工具断言（L31/L111）→ 14

---

### Task 1: 红灯——st_trajectory 注册测试

**Files:**
- Create: `tests/unit/test_l3_st_trajectory.py`
- Modify: `tests/unit/test_l3_spatial.py`（L31-37、L111-117 两处 13→14）

- [ ] **Step 1: 写 `tests/unit/test_l3_st_trajectory.py`（镜像 test_l3_st_misty.py 结构）**

```python
"""st_trajectory 注册（Phase 51）：st 镜像 + /opt/st_tools + timeout 600。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orchestrator.sandbox.runner import BioRunError

ST_IMAGE = "feishu-research-agent/bio:st-cpu-latest"


@pytest.fixture
def runner():
    r = MagicMock()
    r.run.return_value = {"ok": True, "n_spots": 144}
    return r


@pytest.fixture
def reg(runner):
    from orchestrator.tools.builtin.l3_spatial import register
    registry = MagicMock()
    registry.register.side_effect = lambda t: t
    register(runner, registry, image=ST_IMAGE)
    tools = {c[0][0].name: c[0][0]
             for c in registry.register.call_args_list}
    m = MagicMock()
    m.get.side_effect = lambda n: tools[n]
    return m


def test_st_trajectory_registered(runner, reg):
    """注册 L1_compute + timeout 600 + root_mode 默认 marker。"""
    spec = reg.get("st_trajectory")
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 600
    props = spec.parameters["properties"]
    assert props["root_mode"]["default"] == "marker"
    assert "root_marker" in props
    assert "root_layer" in props


def test_st_trajectory_forwards_params(runner, reg):
    """全参数透传 + st 镜像 + /opt/st_tools + timeout 600。"""
    reg.get("st_trajectory").handler(
        dataset_ref="abc123", root_mode="vicinity",
        root_marker="MKI67", root_layer="distal")
    args, kw = runner.run.call_args
    assert args[0] == "trajectory"
    assert args[1] == {"dataset_id": "abc123",
                       "root_mode": "vicinity",
                       "root_marker": "MKI67",
                       "root_layer": "distal"}
    assert kw["image"] == ST_IMAGE
    assert kw["script_dir"] == "/opt/st_tools"
    assert kw["timeout_sec"] == 600


def test_st_trajectory_defaults(runner, reg):
    """缺省：root_mode=marker / root_marker="" / root_layer=tumor。"""
    reg.get("st_trajectory").handler(dataset_ref="abc123")
    args, _ = runner.run.call_args
    assert args[1]["root_mode"] == "marker"
    assert args[1]["root_marker"] == ""
    assert args[1]["root_layer"] == "tumor"


def test_st_trajectory_result_passthrough(runner, reg):
    """ok 键剥掉，其余原样返回。"""
    out = reg.get("st_trajectory").handler(dataset_ref="abc123")
    assert out == {"n_spots": 144}


def test_st_trajectory_error_passthrough(runner, reg):
    """BioRunError → _err 透传。"""
    runner.run.side_effect = BioRunError(
        "ST_TRAJ_NO_VICINITY", "obs['vicinity'] 不存在；先跑 st_vicinity")
    out = reg.get("st_trajectory").handler(
        dataset_ref="abc123", root_mode="vicinity")
    assert out == {"error_code": "ST_TRAJ_NO_VICINITY",
                   "error_message": "obs['vicinity'] 不存在；先跑 st_vicinity"}
```

注意：fixture 以 reg.get 为准，若既有测试文件 fixture 样式不同（如直接
返回 dict），以 `tests/unit/test_l3_st_misty.py` 实际 fixture 为准对齐。

- [ ] **Step 2: test_l3_spatial.py 两处 13→14**

L31-37：

```python
def test_st_registers_fourteen_tools(reg):
    """st_* 14 工具全部注册为 L1_compute（Phase 51 +st_trajectory）。"""
    names = sorted(t.name for t in reg.list())
    assert names == ["st_cnv", "st_commot", "st_deconvolve", "st_domains",
                     "st_load", "st_markers", "st_misty", "st_niche",
                     "st_plot", "st_process", "st_qc", "st_stats",
                     "st_trajectory", "st_vicinity"]
```

L111-117：

```python
def test_register_fourteen_st_tools(reg):
    """Phase 51 后 st_* 共 14 工具（13 + st_trajectory）。"""
    names = sorted(t.name for t in reg.list() if t.name.startswith("st_"))
    assert names == [
        "st_cnv", "st_commot", "st_deconvolve", "st_domains", "st_load",
        "st_markers", "st_misty", "st_niche", "st_plot", "st_process",
        "st_qc", "st_stats", "st_trajectory", "st_vicinity"]
```

- [ ] **Step 3: 跑测试确认红灯**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_st_trajectory.py tests/unit/test_l3_spatial.py -q --basetemp=I:\飞书agent\.pytest_tmp`
Expected: 新文件 5 例全 FAIL（reg.get("st_trajectory") KeyError）+ test_l3_spatial 两处列表断言 FAIL

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_l3_st_trajectory.py tests/unit/test_l3_spatial.py
git commit -m "test(st): st_trajectory 红灯注册测试（Phase 51 Task1）"
```

### Task 2: 绿灯——l3_spatial 注册 + SECTION_TITLES

**Files:**
- Modify: `orchestrator/tools/builtin/l3_spatial.py`（handler 插 st_misty 后 L252 起；ToolSpec 插 L592 st_misty 块后）
- Modify: `orchestrator/report/section_digest.py`（L58 st_misty 行后）

- [ ] **Step 1: handler（st_misty handler 后插入）**

```python
    def st_trajectory(*, dataset_ref: str, root_mode: str = "marker",
                      root_marker: str = "",
                      root_layer: str = "tumor") -> dict[str, Any]:
        """空间拟时序：表达图 DPT + PAGA 映射回组织坐标。"""
        try:
            out = runner.run(
                "trajectory", {
                    "dataset_id": dataset_ref,
                    "root_mode": root_mode,
                    "root_marker": root_marker,
                    "root_layer": root_layer,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out
```

- [ ] **Step 2: ToolSpec（st_misty 注册块 `))` 后插入）**

```python
    registry.register(ToolSpec(
        name="st_trajectory",
        description=(
            "空间拟时序：表达邻居图扩散伪时序（scanpy diffmap + DPT）+ "
            "PAGA 域拓扑，映射回组织空间坐标，回答表达进程是否沿空间"
            "方向展开。root_mode=marker：root_marker 表达最高 spot 为根"
            "（空则 spot #0）；root_mode=vicinity：st_vicinity 层"
            "（root_layer，默认 tumor）内度中位 spot 为根。需先跑 "
            "st_process；vicinity 模式需先跑 st_vicinity。产物 "
            "trajectory/：pseudotime.csv + 空间着色图 + PAGA 空间质心图"
            "（obs 有 vicinity 时附分层 boxplot + Spearman ρ）；"
            "dpt_pseudotime 写回 obs 供 st_plot 叠加。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_load 输出的 dataset_ref"},
                "root_mode": {"type": "string", "default": "marker",
                              "enum": ["marker", "vicinity"],
                              "description": "定根模式：marker=基因表达最高"
                                             " spot；vicinity=st_vicinity 层"},
                "root_marker": {"type": "string", "default": "",
                                "description": "marker 模式根基因 symbol"},
                "root_layer": {"type": "string", "default": "tumor",
                               "description": "vicinity 模式根层"
                                              "（tumor/distal/L1..Ln）"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_trajectory,
        timeout_sec=600,
    ))
```

- [ ] **Step 3: SECTION_TITLES 插入（st_misty 行后）**

```python
    "st_misty": "多视图空间建模",
    "st_trajectory": "空间拟时序",
```

- [ ] **Step 4: 跑测试确认绿灯**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_st_trajectory.py tests/unit/test_l3_spatial.py tests/unit/test_report_digest.py -q --basetemp=I:\飞书agent\.pytest_tmp`
Expected: all passed（含 SECTION_TITLES 计数用例，若 report digest 测试有 37→38 断言需同步改）

- [ ] **Step 5: Commit**

```bash
git add orchestrator/tools/builtin/l3_spatial.py orchestrator/report/section_digest.py tests/
git commit -m "feat(st): st_trajectory 注册 + SECTION_TITLES（Phase 51 Task2）"
```

### Task 3: st_tools/trajectory.py 容器脚本

**Files:**
- Create: `sandbox/st_tools/trajectory.py`

- [ ] **Step 1: 写容器脚本（sc_tools/pseudotime.py 移植，镜像零 pip 改动）**

```python
"""st_trajectory：空间拟时序（Phase 51，sc_pseudotime 模式移植）。

stdin: {"dataset_id": ..., "root_mode": "marker", "root_marker": "",
        "root_layer": "tumor"}
需 processed.h5ad（uns['neighbors'] 表达图 + obsm['spatial']，
st_process 产物；raw=log-normalized 快照供 root_marker 提取）。
方法 scanpy diffmap + DPT（Haghverdi 2016 图扩散族）+ PAGA
（groups=spatial_domain 回退 leiden）；表达图 DPT——空间图 DPT≈BFS
距离场与 st_vicinity 语义重复，不做。
root 二选一：marker=root_marker raw 表达最高 spot（空/缺失→spot #0
+ root_note）；vicinity=obs['vicinity']==root_layer 内表达图度中位
spot（列缺失报 ST_TRAJ_NO_VICINITY）。dpt_pseudotime 写回 obs（供
st_plot 组织图叠加）并重存 processed.h5ad。
产物落 /ws/{ds}/trajectory/：pseudotime.csv + trajectory_spatial.png
（组织散点 viridis + 红圈 root）+ paga_spatial.png（域空间质心 +
连接度加权边）；obs 有 vicinity 时附 trajectory_vicinity.png
（分层 boxplot）+ emit Spearman ρ/pval（表达进程×空间分层一致性）。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from common import (WS_ROOT, emit, ensure_spatial, fail, load_adata,
                    read_args, run)


def _rf(x: float) -> float | None:
    """round + 非有限值转 None（防 JSON 输出 NaN/Infinity）。"""
    return round(float(x), 4) if np.isfinite(x) else None


def _pick_root(adata: Any, args: dict[str, Any]) -> tuple[int, str]:
    """定根：返回 (iroot, root_note)。marker/vicinity 双模式。"""
    root_mode = str(args.get("root_mode", "marker"))
    if root_mode == "marker":
        root_marker = str(args.get("root_marker", "")).strip()
        ref = adata.raw if adata.raw is not None else adata
        if root_marker and root_marker in ref.var_names:
            x = ref[:, root_marker].X
            expr = (np.asarray(x.todense()).ravel()
                    if hasattr(x, "todense") else np.asarray(x).ravel())
            iroot = int(np.argmax(expr))
            return iroot, f"{root_marker}-highest spot #{iroot}"
        note = (f"root_marker {root_marker!r} not in data; "
                "fell back to spot #0" if root_marker
                else "root_marker empty; using spot #0")
        return 0, note
    if "vicinity" not in adata.obs:
        fail("ST_TRAJ_NO_VICINITY",
             "obs['vicinity'] 不存在；先跑 st_vicinity")
        raise SystemExit(1)
    layer = str(args.get("root_layer", "tumor"))
    vic_str = adata.obs["vicinity"].astype(str)
    mask = (vic_str == layer).to_numpy()
    if not mask.any():
        fail("INVALID_INPUT",
             f"vicinity 层 {layer!r} 无 spot（现有层: "
             f"{sorted(vic_str.unique())}）")
        raise SystemExit(1)
    conn = adata.obsp["connectivities"]
    deg = np.asarray((conn > 0).sum(axis=1)).ravel()
    idx = np.flatnonzero(mask)
    med = float(np.median(deg[idx]))
    iroot = int(idx[int(np.argmin(np.abs(deg[idx] - med)))])
    return iroot, f"vicinity {layer!r} 层度中位 spot #{iroot}"


def main() -> None:
    """主流程：定根 → diffmap → DPT → 空间/PAGA 图 → vicinity 耦合。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    root_mode = str(args.get("root_mode", "marker"))
    if root_mode not in ("marker", "vicinity"):
        fail("INVALID_INPUT",
             f"root_mode={root_mode!r} 非法（需 marker|vicinity）")
        raise SystemExit(1)

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    ensure_spatial(adata)
    if "neighbors" not in adata.uns:
        fail("INVALID_INPUT",
             "processed.h5ad 缺 uns['neighbors']；先跑 st_process")
        raise SystemExit(1)

    iroot, root_note = _pick_root(adata, args)
    adata.uns["iroot"] = iroot
    sc.tl.diffmap(adata)
    sc.tl.dpt(adata)
    pt = adata.obs["dpt_pseudotime"].to_numpy(dtype=float)
    n_inf = int(np.sum(~np.isfinite(pt)))
    pt = np.where(np.isfinite(pt), pt, np.nan)  # inf（不连通）→ nan
    adata.obs["dpt_pseudotime"] = pt

    group_key = ("spatial_domain" if "spatial_domain" in adata.obs
                 else "leiden")
    groups = adata.obs[group_key].astype(str)
    stats = (pd.DataFrame({"g": groups.values, "pt": pt})
             .groupby("g")["pt"].agg(["mean", "median", "size"]))
    stats = stats.loc[sorted(stats.index, key=lambda c: (len(c), c))]

    coords = np.asarray(adata.obsm["spatial"])[:, :2]
    ds_dir = WS_ROOT / args["dataset_id"] / "trajectory"
    ds_dir.mkdir(parents=True, exist_ok=True)

    pt_csv = ds_dir / "pseudotime.csv"
    pd.DataFrame({group_key: groups.values, "dpt_pseudotime": pt},
                 index=adata.obs_names).to_csv(pt_csv)

    # 组织空间散点（viridis 着色 + 红圈 root）
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    s = ax.scatter(coords[:, 0], coords[:, 1], s=10, c=pt,
                   cmap="viridis", linewidths=0)
    ax.scatter(coords[iroot, 0], coords[iroot, 1], s=110,
               facecolors="none", edgecolors="red", linewidths=1.6,
               label="root")
    ax.legend(loc="upper right", fontsize=8)
    fig.colorbar(s, ax=ax, fraction=0.046, label="DPT pseudotime")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"Spatial DPT (root: {root_note})", fontsize=9)
    fig.tight_layout()
    traj_png = ds_dir / "trajectory_spatial.png"
    fig.savefig(traj_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # PAGA：域空间质心 + 连接度加权边（表达图连接度，坐标=组织空间）
    sc.tl.paga(adata, groups=group_key)
    conn = adata.uns["paga"]["connectivities"].toarray()
    cents = np.stack([coords[groups.values == c].mean(axis=0)
                      for c in stats.index])
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    ax.scatter(coords[:, 0], coords[:, 1], s=5, c="#d9d9d9", linewidths=0)
    w = conn[conn > 0]
    vmax = float(w.max()) if w.size else 1.0
    for i in range(len(cents)):
        for j in range(i + 1, len(cents)):
            if conn[i, j] > 0:
                ax.plot(*zip(cents[i], cents[j]), color="#3b7dd8",
                        lw=0.6 + 2.4 * conn[i, j] / vmax, alpha=0.75,
                        zorder=2)
    ax.scatter(cents[:, 0], cents[:, 1], s=60, c="#f2a636",
               edgecolors="black", linewidths=0.6, zorder=3)
    for (x, y), c in zip(cents, stats.index):
        ax.annotate(c, (x, y), fontsize=8, ha="center", va="center",
                    zorder=4)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"PAGA on tissue (node = {group_key} centroid)",
                 fontsize=9)
    fig.tight_layout()
    paga_png = ds_dir / "paga_spatial.png"
    fig.savefig(paga_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    out: dict[str, Any] = {
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": "diffmap_dpt",
        "n_spots": int(adata.n_obs),
        "root_mode": root_mode,
        "root_marker": str(args.get("root_marker", "")).strip(),
        "root_cell_index": iroot,
        "root_note": root_note,
        "group_key": group_key,
        "n_disconnected": n_inf,
        "per_domain": [
            {"domain": c, "mean": _rf(r["mean"]),
             "median": _rf(r["median"]), "n_spots": int(r["size"])}
            for c, r in stats.iterrows()],
        "pseudotime_csv": str(pt_csv),
        "trajectory_png": str(traj_png),
        "paga_png": str(paga_png),
        "note": "表达邻居图 DPT（Haghverdi 2016）；dpt_pseudotime 已写回"
                " obs（st_plot 可组织图叠加）；inf=不连通 spot",
    }

    # vicinity 耦合（条件产物）：分层 boxplot + Spearman ρ
    if "vicinity" in adata.obs:
        from scipy.stats import spearmanr
        vic = adata.obs["vicinity"]
        vic_str = vic.astype(str)
        order = ([str(c) for c in vic.cat.categories]
                 if hasattr(vic, "cat") else sorted(vic_str.unique()))
        code = {c: i for i, c in enumerate(order)}
        codes = vic_str.map(code).to_numpy(dtype=float)
        ok_mask = np.isfinite(pt)
        rho, pval = spearmanr(pt[ok_mask], codes[ok_mask])
        fig, ax = plt.subplots(figsize=(5.6, 4.0))
        data = [pt[(vic_str.to_numpy() == c) & ok_mask] for c in order]
        ax.boxplot(data)
        ax.set_xticks(range(1, len(order) + 1))
        ax.set_xticklabels(order, fontsize=8)
        ax.set_xlabel("vicinity layer")
        ax.set_ylabel("DPT pseudotime")
        ax.set_title(f"pseudotime ~ vicinity (Spearman rho={rho:.3f})",
                     fontsize=9)
        fig.tight_layout()
        vic_png = ds_dir / "trajectory_vicinity.png"
        fig.savefig(vic_png, dpi=150, bbox_inches="tight")
        plt.close(fig)
        out["spearman_rho"] = _rf(float(rho))
        out["spearman_pval"] = float(pval)
        out["vicinity_png"] = str(vic_png)

    # 写回 processed.h5ad（obs['dpt_pseudotime'] 供 st_plot 叠加）
    adata.write_h5ad(WS_ROOT / args["dataset_id"] / "processed.h5ad")
    emit(out)


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: mypy + 单测复验**

Run: `.venv\Scripts\python.exe -m mypy sandbox/st_tools/trajectory.py`
Expected: Success: no issues found（若 `ref[:, root_marker].X` 联合类型报错，`ref: Any = ...` 收窄——Phase 46/50 同款处理）
Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_st_trajectory.py -q --basetemp=I:\飞书agent\.pytest_tmp`
Expected: 5 passed（注册测试不受容器脚本影响，仍绿）

- [ ] **Step 3: Commit**

```bash
git add sandbox/st_tools/trajectory.py
git commit -m "feat(st): trajectory.py 空间拟时序容器脚本（Phase 51 Task3）"
```

### Task 4: 镜像重建（COPY 层秒级）+ 断网容器冒烟

**Files:**
- Create: `scripts/_smoke_st_trajectory.py`

- [ ] **Step 1: 写冒烟脚本**

```python
"""st_trajectory 容器冒烟：表达梯度合成数据真跑 DPT（断网）。

12×12 网格 144 spot，60 基因中 g0..g9 沿 x 线性渐变（g0 左高=root
端，g1..g9 右高），builder 容器内跑 mini st_process（normalize/log1p/
raw/scale/PCA/neighbors/leiden）造真 processed.h5ad。DPT root=g0 最高
spot（左端）→ pseudotime 应沿 x 单调恢复（Spearman>0.9）；obs 注入
合成 vicinity 层（右 4 列=tumor / 中 4 列=L1 / 左 4 列=distal）→
vicinity 模式 root=tumor 跑通 + ρ 符号=负（pt 左低右高、层序
tumor→distal 编码递减方向）。DS_NOVIC 无 vicinity 列 → NO_VICINITY。
"""
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path("I:/飞书agent/bio_workspace")
DS = "sttrajsmoke"
DS_NOVIC = "sttrajnovic"
IMG = "feishu-research-agent/bio:st-cpu-latest"
BASE = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG]

_BUILDER = r'''
import numpy as np
import pandas as pd
import anndata as ad
import scanpy as sc

rng = np.random.default_rng(42)
xs, ys = np.meshgrid(np.arange(12), np.arange(12))
coords = np.column_stack([ys.ravel(), xs.ravel()]).astype(float)  # (row, col)
n = coords.shape[0]
xfrac = coords[:, 1] / 11.0  # 沿 col 渐变 0→1
genes = [f"g{i}" for i in range(60)]
X = rng.poisson(1.5, (n, 60)).astype(np.float32)
X = np.log1p(X)
# 轨迹流形：g0 左端高（root 端），g1..g9 沿 x 渐强（log 空间加梯度——
# 教训十六：信号加在最终空间）
X[:, 0] += ((1 - xfrac) * 3).astype(np.float32)
for k in range(1, 10):
    X[:, k] += (xfrac * (1.0 + 0.2 * k)).astype(np.float32)
barcodes = [f"s{i}" for i in range(n)]
proc = ad.AnnData(X=X, obs=pd.DataFrame(index=barcodes),
                  var=pd.DataFrame(index=genes))
proc.obsm["spatial"] = coords
# mini st_process：raw 快照 + 表达邻居图 + spatial_domain
proc.raw = proc
sc.pp.highly_variable_genes(proc, n_top_genes=40, flavor="seurat")
sc.pp.scale(proc, max_value=10)
sc.tl.pca(proc, n_comps=15, svd_solver="arpack")
sc.pp.neighbors(proc, n_neighbors=10, use_rep="X_pca")
sc.tl.leiden(proc, resolution=0.8, key_added="spatial_domain",
             flavor="igraph", n_iterations=2, directed=False)
# 合成 vicinity 层（有序 Categorical，tumor 右端）
col = coords[:, 1]
vic = np.where(col >= 8, "tumor", np.where(col >= 4, "L1", "distal"))
proc.obs["vicinity"] = pd.Categorical(
    vic, categories=["tumor", "L1", "distal"], ordered=True)
proc.write_h5ad("/ws/sttrajsmoke/processed.h5ad")
# 对照：同数据无 vicinity 列
proc2 = proc.copy()
del proc2.obs["vicinity"]
proc2.write_h5ad("/ws/sttrajnovic/processed.h5ad")
print("built", n)
'''


def build_dataset() -> None:
    """容器内构造合成数据集（含无 vicinity 对照）。"""
    bdir = WS / "_builder_sttraj"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "build_data.py").write_text(_BUILDER, encoding="utf-8")
    (WS / DS).mkdir(parents=True, exist_ok=True)
    (WS / DS_NOVIC).mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", "none",
         "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
         IMG, "python", "/ws/_builder_sttraj/build_data.py"],
        capture_output=True, timeout=600)
    assert out.returncode == 0, out.stderr.decode()[-2000:]


def run_traj(ds: str, **kw):
    """容器内跑 trajectory.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": ds, **kw})
    r = subprocess.run(BASE + ["python", "/opt/st_tools/trajectory.py"],
                       input=payload, capture_output=True, text=True,
                       timeout=600)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n"
                         f"{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


build_dataset()

# marker 主跑：root=g0 最高 spot（左端）→ pt 沿 x(col) 单调
o = run_traj(DS, root_marker="g0")
assert o["ok"], o
assert o["root_mode"] == "marker", o
assert o["n_spots"] == 144, o
assert o["group_key"] == "spatial_domain", o
ds_dir = WS / DS
for f in ("trajectory/pseudotime.csv", "trajectory/trajectory_spatial.png",
          "trajectory/paga_spatial.png", "trajectory/trajectory_vicinity.png"):
    assert (ds_dir / f).exists(), f
assert "spearman_rho" in o, o
df = pd.read_csv(ds_dir / "trajectory/pseudotime.csv", index_col=0)
xs, ys = np.meshgrid(np.arange(12), np.arange(12))
col = pd.DataFrame({"col": xs.ravel() * 0 + ys.ravel()},
                   index=[f"s{i}" for i in range(144)])
from scipy.stats import spearmanr
rho_x, _ = spearmanr(df["dpt_pseudotime"], col["col"])
assert rho_x > 0.9, f"pseudotime~x Spearman={rho_x:.3f} (<0.9)"
# 写回核验：processed.h5ad obs 有 dpt_pseudotime
import anndata as ad
back = ad.read_h5ad(ds_dir / "processed.h5ad")
assert "dpt_pseudotime" in back.obs, "write-back missing"

# vicinity 模式：root=tumor 层，层序 tumor→L1→distal 沿 x 递减 →
# ρ(pt, 层编码)<0（pt 左低右高，tumor 编码 0 在右）
v = run_traj(DS, root_mode="vicinity", root_layer="tumor")
assert v["ok"], v
assert v["root_mode"] == "vicinity", v
assert v["spearman_rho"] < -0.9, f"rho={v['spearman_rho']}"

# 无 vicinity 列 → ST_TRAJ_NO_VICINITY
nv = run_traj(DS_NOVIC, root_mode="vicinity")
assert not nv["ok"] and nv["error_code"] == "ST_TRAJ_NO_VICINITY", nv

# root_mode 非法 → INVALID_INPUT
bad = run_traj(DS, root_mode="bogus")
assert not bad["ok"] and bad["error_code"] == "INVALID_INPUT", bad

# root_layer 无 spot → INVALID_INPUT
bad_layer = run_traj(DS, root_mode="vicinity", root_layer="L9")
assert not bad_layer["ok"] and bad_layer["error_code"] == "INVALID_INPUT", \
    bad_layer

print("SMOKE OK | spots:", o["n_spots"],
      "| pt~x rho: %.3f" % rho_x,
      "| vicinity rho:", v["spearman_rho"],
      "| NO_VICINITY/INVALID_INPUT rejected")
```

- [ ] **Step 2: 重建 st 镜像（COPY 层快照，秒级）**

Run: `docker build -t feishu-research-agent/bio:st-cpu-latest sandbox -f sandbox/st.Dockerfile 2>&1 | Select-Object -Last 3`
Expected: 构建成功（仅 COPY st_tools/ 层重跑）

- [ ] **Step 3: 跑断网冒烟**

Run: `.venv\Scripts\python.exe scripts\_smoke_st_trajectory.py`
Expected: `SMOKE OK | spots: 144 | pt~x rho: >0.9 | vicinity rho: <-0.9 | NO_VICINITY/INVALID_INPUT rejected`
注意：若 ρ 未达阈值，先容器内回读 pseudotime.csv 查 DPT 排序质量
（梯度强度/leiden 参数），不要先改脚本逻辑。

- [ ] **Step 4: 清理冒烟产物**

```bash
Remove-Item -Recurse -Force I:\飞书agent\bio_workspace\sttrajsmoke, I:\飞书agent\bio_workspace\sttrajnovic, I:\飞书agent\bio_workspace\_builder_sttraj
```

- [ ] **Step 5: Commit**

```bash
git add scripts/_smoke_st_trajectory.py
git commit -m "test(st): st_trajectory 断网容器冒烟（表达梯度 DPT 恢复）（Phase 51 Task4）"
```

### Task 5: 全量门禁 + 文档 + 推送 CI

**Files:**
- Modify: `docs/ROADMAP.md`（追加 Phase 51 行）
- Modify: `测试总结+2026-09-09T01-55-00.md`（追加十八）

- [ ] **Step 1: 全量回归（统一 `-m "not pg"` 口径）**

Run: `.venv\Scripts\python.exe -m pytest -q -m "not pg" --basetemp=I:\飞书agent\.pytest_tmp > reg_out.txt 2>&1; Select-String -Path reg_out.txt -Pattern "deselected"`
Expected: `1272 passed, 6 deselected`（1267 + 新注册 5 例）；看完摘要后删 reg_out.txt

- [ ] **Step 2: ROADMAP 追加 Phase 51 行 + 测试总结追加十八**

ROADMAP 行要点：st_* 第 14 工具；sc_pseudotime Phase 32 模式移植
（UMAP→组织空间、leiden→spatial_domain、+vicinity 双模式定根/分层
耦合 Spearman ρ）；**表达图 DPT 不用空间图**（≈st_vicinity BFS 重复）；
镜像零 pip 改动（st_process 产物 DPT-ready：neighbors/raw/spatial_domain
齐备）；dpt_pseudotime 写回 obs 供 st_plot 叠加（实现期增补，与
st_cnv/st_vicinity 写回惯例一致）；断网冒烟表达梯度 ρ>0.9 恢复 +
NO_VICINITY/INVALID_INPUT 路径；SECTION_TITLES 38 项；TDD 注册 5
用例；全量 **1272 passed**（同口径恰 +5）。

测试总结十八要点：范围/实施/验证/教训四节；教训记"新功能先勘探
既有资产——st_process 产物天然 DPT-ready，零依赖落地"与"空间图
DPT≈BFS 重复语义鉴别（vicinity 已占坑，表达图才是增量）"。

- [ ] **Step 3: Commit + 推送**

```bash
git add docs/ROADMAP.md "测试总结+2026-09-09T01-55-00.md"
git commit -m "docs: Phase 51 ROADMAP + 测试总结追加十八"
git push
```

- [ ] **Step 4: CI 轮询至 success**

git credential 取 PAT → GitHub API 查最新 workflow run，轮询至
`conclusion=success`。

---

## Self-Review

**Spec coverage：**
- §1 形态/镜像零改动/注册 → Task 2/3 ✓
- §2 表达图 DPT/PAGA spatial_domain/vicinity 条件产物 → Task 3 ✓
- §3 数据流（raw 提取/NO_VICINITY/三 png+csv）→ Task 3 ✓
- §4 四参数（默认值/非法 root_mode INVALID_INPUT/层无 spot
  INVALID_INPUT）→ Task 2 ToolSpec + Task 3 校验 + Task 4 冒烟 ✓
- §5 emit 键（含 spearman_rho/pval 条件键）→ Task 3 + Task 4 断言 ✓
- §6 错误码四场景 → NO_VICINITY/bogus mode/L9 层 Task 4 冒烟；
  缺 neighbors INVALID_INPUT 为防御分支（st_process 产物必含，
  不单独冒烟——与 NO_PROGENY 同口径）
- §7 注册 5 例/两处 13→14/冒烟/1267+5=1272 → Task 1/4/5 ✓
- 实现期增补（spec 精神内）：dpt_pseudotime 写回 processed.h5ad obs
  供 st_plot 叠加——与 st_cnv/st_vicinity 写回惯例一致，已记入
  ROADMAP 要点

**Placeholder scan：** 无 TBD/TODO；所有代码步骤含完整代码；
Task1 Step1 注明 fixture 以既有 misty 测试文件实际样式为准（防止
凭记忆写错 fixture 形态）。

**Type consistency：** `root_mode/root_marker/root_layer` 全链统一
（handler 默认 → args payload → read_args 键 → ToolSpec properties
→ emit 键）；`_pick_root` 返回 `tuple[int, str]` 与 main() 解包一致；
`group_key` 贯穿 stats/csv/PAGA/emit；冒烟断言键（group_key/
spearman_rho/n_spots/root_mode）与 emit 一致。
