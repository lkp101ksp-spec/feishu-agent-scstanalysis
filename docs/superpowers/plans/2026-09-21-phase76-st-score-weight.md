# Phase 76 实施计划：st_score_weight 反卷积加权打分（细胞型 × 通路矩阵）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 兑现挂账"反卷积权重（Cell2Location q05）加权打分"——新独立容器工具 `st_score_weight`，消费 st_deconvolve 的 `deconv.h5ad` 与 st_genescore/st_metabolism 的 scores csv，产出细胞型 × 通路丰度加权活性矩阵，完成 L3 注册、CI 冒烟与 oscc 真机闭环。

**Architecture:** 零镜像改动——工具放 `sandbox/sc_tools/` 跑 bio 镜像（Phase 75 先例），纯"两产物联接"：读 `obsm["q05_cell_abundance_w_sf"]`（spot×细胞型）与 scores csv（spot×通路，首列 groupby 丢弃），`W = (Aᵀ @ S) / A.colsum()` 丰度加权均值。前置 oscc deconvolve 走 st 镜像长任务（~2h，参数历史定型）。三守卫联动单 commit 原子性沿用 Phase 75 Task 3 纪律。

**Tech Stack:** numpy/pandas/anndata/matplotlib（均在 bio 镜像层）；宿主侧 pytest 契约测试（mock BioRunner）+ docker 断网冒烟。

**Spec:** `docs/superpowers/specs/2026-09-21-phase76-st-score-weight-design.md`（§2 算法 / §3 接口 / §5 测试 / §6 真机）

**质量门（每次提交前本地跑）：** `ruff check .` → `python -m mypy` → `python -m pytest -m "not pg" -q`（基线 1362 passed，Task 2 后 1366，零回归才算完）。

---

## 文件结构总览

| 文件 | 动作 | 职责 |
|---|---|---|
| `sandbox/sc_tools/st_score_weight.py` | Create | 容器工具：q05 丰度 × scores → 细胞型×通路矩阵 + 热图 |
| `scripts/_smoke_st_score_weight.py` | Create | 断网冒烟：合成 deconv+scores 真值回收 + 三拒收 |
| `tests/unit/test_l3_st_score_weight.py` | Create | 契约测试 4 用例（bare mock） |
| `orchestrator/tools/builtin/l3_spatial.py` | Modify 4 处 | 常量 + handler + ToolSpec |
| `tests/unit/test_l3_spatial.py` | Modify 2 处 | st 名单 20→21 |
| `orchestrator/report/section_digest.py` | Modify 1 处 | SECTION_TITLES 追加 |
| `docs/superpowers/specs/2026-09-17-execution-plane-unification-design.md` | Modify 1 行 | 附录 A 600 档追加 |
| `.github/workflows/ci.yml` | Modify | bio-image-smoke 加一步 |
| `bio_workspace/_eval/real_st_deconvolve_oscc.py` | Create | oscc deconvolve 前置驱动（git add -f） |
| `bio_workspace/_eval/real_st_score_weight_oscc.py` | Create | 真机验收脚本（git add -f） |
| `测试总结+2026-09-09T01-55-00.md` | Modify | #21 回填 |

**三守卫联动说明**：`test_l3_spatial.py` 两处名单、`test_l3_dispatch_contract.py` 附录 A 双向表、`test_report_digest.py` SECTION_TITLES 守卫——三者任一缺同步即红，故 Task 2 的全部接线必须一次提交（Phase 75 Task 3 已验证纪律）。

---

### Task 1: st_score_weight 容器工具 + 本地断网冒烟

**Files:**
- Create: `sandbox/sc_tools/st_score_weight.py`
- Create: `scripts/_smoke_st_score_weight.py`

- [x] **Step 1.1: 写容器工具 `sandbox/sc_tools/st_score_weight.py`**

```python
"""st_score_weight：反卷积加权打分（Phase 76，spec 2026-09-21 §2-§4）。

stdin: {"dataset_id": ..., "source": "st_genescore"|"st_metabolism"}
读两路产物做"细胞型 × 通路"丰度加权活性矩阵：
- {ds}/deconv.h5ad → obsm["q05_cell_abundance_w_sf"]（spot × 细胞型，
  deconvolve.py 已把列名净化为纯因子名，本脚本再做防御性前缀剥离）；
- {ds}/{source}/{scores csv}（spot × 通路，首列为 groupby 丢弃）。
口径 W = (Aᵀ @ S) / A.colsum()（丰度加权均值，与 spot 分同尺度、
细胞型间可比；裸矩阵乘会把"细胞多"与"活性高"混淆）；总丰度 <1e-6
的细胞型剔除并上报 dropped_celltypes；spot 索引重合率 <80% 硬拒收。
产物落 {ds}/st_score_weight/（csv 全量 + 列 z-score 热图方差 top30）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, read_args, run

SCORES_MAP = {
    "st_genescore": ("st_genescore", "st_progeny_scores.csv"),
    "st_metabolism": ("st_metabolism", "st_metabolism_scores.csv"),
}
MIN_OVERLAP_RATIO = 0.8  # spot 索引重合率下限，防错数据集张冠李戴
MIN_ABUND = 1e-6  # 细胞型总丰度下限，防 0/0 出 NaN 静默入产物
HEATMAP_TOP_N = 30  # 代谢 315 通路全画不可读，热图只取方差 top30 列


def main() -> None:
    """主流程：q05 丰度 × spot 通路分 → 细胞型×通路加权矩阵 + 热图。"""
    import anndata as ad
    import matplotlib.pyplot as plt

    args = read_args()
    source = str(args.get("source", "st_genescore")).strip()
    if source not in SCORES_MAP:
        fail("INVALID_INPUT",
             f"source 需为 {sorted(SCORES_MAP)} 之一，收到 {source!r}")
        raise SystemExit(1)
    sub_dir, scores_name = SCORES_MAP[source]

    ds_dir_in = WS_ROOT / args["dataset_id"]
    deconv_h5ad = ds_dir_in / "deconv.h5ad"
    if not deconv_h5ad.exists():
        fail("ST_WEIGHT_NO_DECONV",
             f"{deconv_h5ad} 缺失：请先运行 st_deconvolve 生成反卷积产物")
        raise SystemExit(1)
    scores_csv = ds_dir_in / sub_dir / scores_name
    if not scores_csv.exists():
        fail("INVALID_INPUT",
             f"{scores_csv} 缺失：请先运行 {source} 生成 spot 级打分产物")
        raise SystemExit(1)

    sp = ad.read_h5ad(deconv_h5ad)
    abund = sp.obsm.get("q05_cell_abundance_w_sf")
    if abund is None or abund.shape[1] == 0:
        fail("ST_WEIGHT_NO_DECONV",
             "deconv.h5ad 缺 obsm['q05_cell_abundance_w_sf']（结构异常，"
             "请重跑 st_deconvolve）")
        raise SystemExit(1)
    abund = abund.copy()
    abund.columns = [str(c).replace("q05cell_abundance_w_sf_", "")
                     for c in abund.columns]
    abund.index = abund.index.astype(str)

    sc_df = pd.read_csv(scores_csv, index_col=0)
    sc_df.index = sc_df.index.astype(str)
    scores = sc_df.iloc[:, 1:].astype(float)  # 首列 groupby 丢弃

    common = abund.index.intersection(scores.index)
    ratio = len(common) / max(len(scores.index), 1)
    if ratio < MIN_OVERLAP_RATIO:
        fail("INVALID_INPUT",
             f"spot 索引重合率 {ratio:.1%}（{len(common)}/"
             f"{len(scores.index)}）<{MIN_OVERLAP_RATIO:.0%}：scores 与 "
             "deconv 疑似不同数据集产物")
        raise SystemExit(1)
    a_mat = abund.loc[common].to_numpy(dtype=float)
    s_mat = scores.loc[common].to_numpy(dtype=float)

    colsum = a_mat.sum(axis=0)
    keep = colsum >= MIN_ABUND
    dropped = [str(c) for c, k in zip(abund.columns, keep) if not k]
    if not keep.any():
        fail("INVALID_INPUT",
             "全部细胞型总丰度近零（deconv 产物异常，请重跑 st_deconvolve）")
        raise SystemExit(1)
    cell_types = [str(c) for c, k in zip(abund.columns, keep) if k]
    w = (a_mat[:, keep].T @ s_mat) / colsum[keep][:, None]
    w_df = pd.DataFrame(w, index=cell_types, columns=scores.columns)

    out_dir = WS_ROOT / args["dataset_id"] / "st_score_weight"
    out_dir.mkdir(parents=True, exist_ok=True)
    w_csv = out_dir / f"st_weighted_{source}_scores.csv"
    w_df.to_csv(w_csv)

    top_cols = (w_df.var(axis=0).sort_values(ascending=False)
                .head(HEATMAP_TOP_N).index.tolist())
    hm = w_df[top_cols].to_numpy(dtype=float)
    mu = hm.mean(axis=0, keepdims=True)
    sd = hm.std(axis=0, keepdims=True)
    z = np.divide(hm - mu, sd, out=np.zeros_like(hm), where=sd > 0)
    fig, ax = plt.subplots(
        figsize=(max(6.0, 0.32 * len(top_cols)),
                 0.3 * len(cell_types) + 1.6))
    im = ax.imshow(z, aspect="auto", cmap="RdBu_r", vmin=-2, vmax=2)
    ax.set_xticks(range(len(top_cols)))
    ax.set_xticklabels([c[:18] for c in top_cols], rotation=60,
                       ha="right", fontsize=7)
    ax.set_yticks(range(len(cell_types)))
    ax.set_yticklabels(cell_types, fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.03, label="z-score (per pathway)")
    ax.set_title(f"Deconv-weighted activity ({source})", fontsize=10)
    fig.tight_layout()
    heatmap_png = out_dir / f"st_weighted_{source}_heatmap.png"
    fig.savefig(heatmap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    top_by_celltype = {
        ct: w_df.loc[ct].sort_values(ascending=False).head(3).index.tolist()
        for ct in cell_types
    }
    emit(
        {
            "ok": True,
            "dataset_ref": args["dataset_id"],
            "source": source,
            "n_celltypes": len(cell_types),
            "n_pathways": int(w_df.shape[1]),
            "n_spots_overlap": int(len(common)),
            "dropped_celltypes": dropped,
            "top_by_celltype": top_by_celltype,
            "products": {
                "scores_csv": str(w_csv),
                "heatmap_png": str(heatmap_png),
            },
        }
    )


if __name__ == "__main__":
    run(main)
```

- [x] **Step 1.2: 写冒烟脚本 `scripts/_smoke_st_score_weight.py`**

无需 PROGENy 模型（纯矩阵乘），合成 deconv.h5ad + scores csv 即可；真值注入：CellA 丰度集中左半、左半 TGFb 高分 → W 中 CellA×TGFb 全局最高；CellC 全零 → dropped：

```python
"""st_score_weight 冒烟（Phase 76，spec 2026-09-21 §5.3）：
合成 deconv.h5ad（q05 丰度，CellA 集中左半）+ 合成 scores csv（左半
TGFb 高分注入）→ 容器断网跑 → 真值回收 + 全零型剔除 + 三拒收。

场景：
①主场景：CellA×TGFb 为 W 全局最高且 > CellB×TGFb、CellC（全零）入
  dropped_celltypes、n_spots_overlap=40、两产物落盘、
  top_by_celltype["CellA"][0]=="TGFb"；
②缺 deconv.h5ad → ST_WEIGHT_NO_DECONV + 引导 st_deconvolve；
③source=st_metabolism 无产物 → INVALID_INPUT + 引导 st_metabolism；
④spot 索引全错位（X 前缀）→ INVALID_INPUT + 重合率报数。

合成数据：40 spot（8×5 网格左半/右半），3 细胞型（CellA 左半 3.0/
右半 0.05、CellB 均匀 1.0、CellC 全零），14 通路（TGFb 左半 5.0/
右半 0.1，其余 N(0,0.05)，seed=7）。
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", Path(__file__).resolve().parents[1] / "bio_workspace"))
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")

REPO = Path(__file__).resolve().parents[1]
MOUNTS = [
    "-v",
    f"{str(WS).replace(chr(92), '/')}:/ws",
    # sc_tools 整目录挂载对齐 handler 的 script_dir 行为（Phase 65 先例）
    "-v",
    f"{str(REPO / 'sandbox' / 'sc_tools').replace(chr(92), '/')}:/opt/sc_tools",
]
BASE = ["docker", "run", "--rm", "-i", "--network", "none", *MOUNTS, IMG]

PW = ["TGFb", "EGFR", "JAK-STAT"] + [f"PW{i:02d}" for i in range(11)]


def build_ds(ds: str, misalign: bool = False, with_deconv: bool = True) -> str:
    """合成 {ds}/st_genescore/st_progeny_scores.csv + deconv.h5ad；
    misalign 时 scores 索引用 X 前缀（与 deconv 零重合）。"""
    import anndata as ad

    rng = np.random.default_rng(7)
    n = 40
    names = [f"S{i:02d}" for i in range(n)]
    left = (np.arange(n) % 8) < 4
    s = rng.normal(0.0, 0.05, (n, len(PW)))
    s[:, 0] = np.where(left, 5.0, 0.1)  # TGFb 左半高分注入
    sc_df = pd.DataFrame(s, columns=PW)
    sc_df.insert(0, "spatial_domain", np.where(left, "D1", "D2"))
    sc_df.index = [f"X{i:02d}" for i in range(n)] if misalign else names
    d = WS / ds
    (d / "st_genescore").mkdir(parents=True, exist_ok=True)
    sc_df.to_csv(d / "st_genescore" / "st_progeny_scores.csv")
    if with_deconv:
        abund = pd.DataFrame(
            {
                "CellA": np.where(left, 3.0, 0.05),
                "CellB": np.full(n, 1.0),
                "CellC": np.zeros(n),  # 全零 → dropped_celltypes
            },
            index=names,
        )
        a = ad.AnnData(X=np.ones((n, 3), dtype=np.float32))
        a.obs_names = names
        a.var_names = ["G1", "G2", "G3"]
        a.obsm["q05_cell_abundance_w_sf"] = abund
        a.write_h5ad(d / "deconv.h5ad")
    return ds


DS_MAIN = build_ds("_smoke_st_weight_main")
DS_NODECONV = build_ds("_smoke_st_weight_nodeconv", with_deconv=False)
DS_MISALIGN = build_ds("_smoke_st_weight_misalign", misalign=True)


def run_sw(**kw):
    """容器内跑 st_score_weight.py（stdin JSON），整体解析 stdout。"""
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/st_score_weight.py"],
        input=json.dumps(kw),
        capture_output=True,
        text=True,
        timeout=600,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 主场景：真值回收 + 全零型剔除 + 产物落盘
o1 = run_sw(dataset_id=DS_MAIN)
assert o1["ok"], o1
assert o1["source"] == "st_genescore" and o1["n_spots_overlap"] == 40, o1
assert o1["n_celltypes"] == 2 and o1["n_pathways"] == 14, o1
assert o1["dropped_celltypes"] == ["CellC"], o1
for key in ("scores_csv", "heatmap_png"):
    assert (WS / Path(o1["products"][key]).relative_to("/ws")).exists(), o1["products"][key]
assert o1["top_by_celltype"]["CellA"][0] == "TGFb", o1["top_by_celltype"]
w = pd.read_csv(WS / Path(o1["products"]["scores_csv"]).relative_to("/ws"), index_col=0)
assert float(w.loc["CellA", "TGFb"]) == float(w.to_numpy().max()), w
assert float(w.loc["CellA", "TGFb"]) > float(w.loc["CellB", "TGFb"]), w
print(f"① 主场景：CellA×TGFb={w.loc['CellA', 'TGFb']:.3f} 全局最高 "
      f"+ CellC 剔除 + 两产物落盘 OK")

# ② 缺 deconv.h5ad 拒收
o2 = run_sw(dataset_id=DS_NODECONV)
assert not o2["ok"] and o2["error_code"] == "ST_WEIGHT_NO_DECONV", o2
assert "st_deconvolve" in o2["error_message"], o2
print("② 缺 deconv ST_WEIGHT_NO_DECONV + 引导 OK")

# ③ 缺 source 产物拒收（st_metabolism 未跑）
o3 = run_sw(dataset_id=DS_MAIN, source="st_metabolism")
assert not o3["ok"] and o3["error_code"] == "INVALID_INPUT", o3
assert "st_metabolism" in o3["error_message"], o3
print("③ 缺 scores INVALID_INPUT + 引导 st_metabolism OK")

# ④ spot 索引错位拒收（0% 重合）
o4 = run_sw(dataset_id=DS_MISALIGN)
assert not o4["ok"] and o4["error_code"] == "INVALID_INPUT", o4
assert "重合率" in o4["error_message"], o4
print("④ 索引错位 INVALID_INPUT + 重合率报数 OK")

print("\nSMOKE OK: st_score_weight 4 场景全绿")
```

- [x] **Step 1.3: 跑冒烟**

Run: `python scripts/_smoke_st_score_weight.py`
Expected: 末尾输出 `SMOKE OK: st_score_weight 4 场景全绿`（①中 CellA×TGFb≈4.9 为全局最高）

- [x] **Step 1.4: ruff**

Run: `ruff check sandbox/sc_tools/st_score_weight.py scripts/_smoke_st_score_weight.py`
Expected: `All checks passed!`

- [x] **Step 1.5: Commit**

```bash
git add sandbox/sc_tools/st_score_weight.py scripts/_smoke_st_score_weight.py
git commit -m "feat: st_score_weight 容器工具+断网冒烟（Phase 76 q05 丰度×通路分，细胞型×通路矩阵真值回收）"
```

---

### Task 2: L3 接线（契约测试 + 三守卫联动，单 commit 原子性）

**Files:**
- Create: `tests/unit/test_l3_st_score_weight.py`
- Modify: `orchestrator/tools/builtin/l3_spatial.py`（3 处：L33 后常量、st_metabolism handler 后、文件尾 ToolSpec）
- Modify: `tests/unit/test_l3_spatial.py`（两处名单 + docstring）
- Modify: `orchestrator/report/section_digest.py`（L60 后）
- Modify: `docs/superpowers/specs/2026-09-17-execution-plane-unification-design.md`（附录 A L110 整行替换）

- [x] **Step 2.1: 先写契约测试（TDD 红）——`tests/unit/test_l3_st_score_weight.py`**

```python
"""st_score_weight 注册测试（mock BioRunner，不发 docker）。"""

from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_spatial import (
    _ST_SCORE_WEIGHT_TIMEOUT,
    register_l3_spatial,
)
from orchestrator.tools.tool_registry import ToolRegistry


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.run.return_value = {
        "ok": True,
        "dataset_ref": "oscc",
        "source": "st_genescore",
        "n_celltypes": 13,
        "n_pathways": 14,
        "n_spots_overlap": 1749,
        "dropped_celltypes": [],
        "top_by_celltype": {"Epithelial cells": ["JAK-STAT", "EGFR", "TGFb"]},
        "products": {
            "scores_csv": "/ws/oscc/st_score_weight/st_weighted_st_genescore_scores.csv",
            "heatmap_png": "/ws/oscc/st_score_weight/st_weighted_st_genescore_heatmap.png",
        },
    }
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_score_weight_registered(reg):
    """第 21 个 st_* 工具：L1_compute、timeout 600（附录 A 档）、
    浅层 schema、required 仅 dataset_ref、source 默认 st_genescore。"""
    spec = reg.get("st_score_weight")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 600 == _ST_SCORE_WEIGHT_TIMEOUT
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert props["source"]["default"] == "st_genescore"
    assert props["source"]["enum"] == ["st_genescore", "st_metabolism"]
    for p in props.values():
        assert p["type"] in ("string", "integer", "number", "boolean")


def test_st_score_weight_dispatches_bio_image(runner, reg):
    """跨镜像分发（bio 镜像 + /opt/sc_tools，st_genescore 先例）+
    两参数透传（dataset_ref→dataset_id 容器侧键名）。"""
    out = reg.get("st_score_weight").handler(
        dataset_ref="oscc", source="st_metabolism")
    args, kw = runner.run.call_args
    assert args[0] == "st_score_weight"
    assert args[1] == {"dataset_id": "oscc", "source": "st_metabolism"}
    assert kw["image"] == "feishu-research-agent/bio:cpu-latest"
    assert kw["script_dir"] == "/opt/sc_tools"
    assert kw["timeout_sec"] == _ST_SCORE_WEIGHT_TIMEOUT
    assert out["n_celltypes"] == 13
    assert "ok" not in out


def test_st_score_weight_defaults(runner, reg):
    """可选参数默认：source=st_genescore。"""
    reg.get("st_score_weight").handler(dataset_ref="d1")
    args, _ = runner.run.call_args
    assert args[1]["source"] == "st_genescore"


def test_st_score_weight_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出（ST_WEIGHT_NO_DECONV 等容器侧码）。"""
    runner.run.side_effect = BioRunError(
        "ST_WEIGHT_NO_DECONV", "deconv.h5ad 缺失：请先运行 st_deconvolve")
    out = reg.get("st_score_weight").handler(dataset_ref="d1")
    assert out == {"error_code": "ST_WEIGHT_NO_DECONV",
                   "error_message": "deconv.h5ad 缺失：请先运行 st_deconvolve"}
```

- [x] **Step 2.2: 跑测试确认红（ImportError）**

Run: `python -m pytest tests/unit/test_l3_st_score_weight.py -q`
Expected: collection error——`ImportError: cannot import name '_ST_SCORE_WEIGHT_TIMEOUT' from 'orchestrator.tools.builtin.l3_spatial'`

- [x] **Step 2.3: 接线 `l3_spatial.py`（3 处）**

① L33 后追加常量：

```python
# Phase 76：反卷积加权打分（纯矩阵乘，远轻于打分 1800）
_ST_SCORE_WEIGHT_TIMEOUT = 600
```

② st_metabolism handler（L469 `return out` 之后、`registry.register(ToolSpec(` 之前）插入：

```python
    def st_score_weight(*, dataset_ref: str,
                        source: str = "st_genescore") -> dict[str, Any]:
        """反卷积加权打分（Phase 76）：读 {ds}/deconv.h5ad 的 q05 丰度
        与 source 指定的 st_genescore/st_metabolism scores csv，产出
        细胞型×通路丰度加权活性矩阵（W = AᵀS/总丰度）；bio 镜像跨镜像
        分发，产物落 {ds}/st_score_weight/。缺 deconv 报
        ST_WEIGHT_NO_DECONV 引导先跑 st_deconvolve。"""
        try:
            out = runner.run(
                "st_score_weight", {
                    "dataset_id": dataset_ref,
                    "source": source,
                }, image=bio_image, script_dir=_SC_SCRIPT_DIR,
                timeout_sec=_ST_SCORE_WEIGHT_TIMEOUT)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

```

③ 文件尾 st_metabolism ToolSpec（L1126 `))`）之后追加：

```python
    registry.register(ToolSpec(
        name="st_score_weight",
        description=(
            "反卷积加权打分（Phase 76）：cell2location q05 丰度 × "
            "st_genescore/st_metabolism 的 spot 级通路分，产出细胞型 × "
            "通路丰度加权活性矩阵（W=AᵀS/总丰度，细胞型间可比）。前置需"
            "先跑 st_deconvolve 与对应打分工具；缺 deconv.h5ad 报 "
            "ST_WEIGHT_NO_DECONV，spot 索引重合率 <80% 拒收。产物落 "
            "{ds}/st_score_weight/（csv 全量+方差 top30 热图）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "含 deconv.h5ad 与 "
                                               "st_genescore/"
                                               "st_metabolism 产物的 "
                                               "dataset_ref"},
                "source": {"type": "string", "default": "st_genescore",
                           "enum": ["st_genescore", "st_metabolism"],
                           "description": "打分产物来源（决定读取 "
                                          "st_progeny_scores.csv 或 "
                                          "st_metabolism_scores.csv）"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_score_weight,
        timeout_sec=_ST_SCORE_WEIGHT_TIMEOUT,
    ))
```

- [x] **Step 2.4: 名单 20→21（`tests/unit/test_l3_spatial.py` 两处）**

① `test_st_registers_fourteen_tools`（L31-45）：docstring 与名单整段替换：

```python
def test_st_registers_fourteen_tools(reg):
    """st_* 21 工具全部注册为 L1_compute（Phase 51 +st_trajectory、
    Phase 57 +st_cellchat_v2、Phase 65 +st_nichenet、Phase 68
    +st_niche_scan、Phase 69 +st_integrate、Phase 75
    +st_genescore/st_metabolism、Phase 76 +st_score_weight）。"""
    names = sorted(t.name for t in reg.list())
    assert names == ["st_cellchat_v2", "st_cnv", "st_commot",
                     "st_deconvolve", "st_domains", "st_genescore",
                     "st_integrate", "st_load", "st_markers",
                     "st_metabolism", "st_misty", "st_niche",
                     "st_niche_scan", "st_nichenet", "st_plot",
                     "st_process", "st_qc", "st_score_weight",
                     "st_stats", "st_trajectory", "st_vicinity"]
    for t in reg.list():
        assert t.risk_level == "L1_compute"
```

② `test_register_fourteen_st_tools`（L165-173）整段替换：

```python
def test_register_fourteen_st_tools(reg):
    """Phase 76 后 st_* 共 21 工具（20 + st_score_weight）。"""
    names = sorted(t.name for t in reg.list() if t.name.startswith("st_"))
    assert names == [
        "st_cellchat_v2", "st_cnv", "st_commot", "st_deconvolve",
        "st_domains", "st_genescore", "st_integrate", "st_load",
        "st_markers", "st_metabolism", "st_misty", "st_niche",
        "st_niche_scan", "st_nichenet", "st_plot", "st_process",
        "st_qc", "st_score_weight", "st_stats", "st_trajectory",
        "st_vicinity"]
```

- [x] **Step 2.5: SECTION_TITLES（`section_digest.py`）**

L60 `"st_metabolism": "空间代谢活性分析",` 行后插入：

```python
    "st_score_weight": "细胞型加权活性分析",
```

- [x] **Step 2.6: 附录 A 超时表 600 档追加（`2026-09-17-execution-plane-unification-design.md` L110 整行替换）**

旧行：

```markdown
| 600 | sc_load, sc_qc, sc_plot, sc_cellfreq, sc_meta, sc_cellcycle, st_load, st_qc, st_plot, st_niche, st_vicinity, st_trajectory |
```

新行：

```markdown
| 600 | sc_load, sc_qc, sc_plot, sc_cellfreq, sc_meta, sc_cellcycle, st_load, st_qc, st_plot, st_niche, st_vicinity, st_trajectory, st_score_weight |
```

- [x] **Step 2.7: 契约测试转绿 + 三守卫全绿**

Run: `python -m pytest tests/unit/test_l3_st_score_weight.py tests/unit/test_l3_spatial.py tests/unit/test_l3_dispatch_contract.py tests/unit/test_report_digest.py -q`
Expected: 全绿（4 新用例 + 三守卫，合计约 37 passed）

- [x] **Step 2.8: 门禁 + 全量**

Run: `ruff check .` → `python -m mypy` → `python -m pytest -m "not pg" -q`
Expected: ruff 零告警；mypy 204 files 零 issue；pytest **1366 passed**（基线 1362+4）

- [x] **Step 2.9: 单 commit 提交全部接线**

```bash
git add tests/unit/test_l3_st_score_weight.py orchestrator/tools/builtin/l3_spatial.py tests/unit/test_l3_spatial.py orchestrator/report/section_digest.py docs/superpowers/specs/2026-09-17-execution-plane-unification-design.md
git commit -m "feat: st_score_weight L3 接线（契约测试+三守卫联动单提交，Phase 76）"
```

---

### Task 3: CI 接入 + 三重门禁 + push

**Files:**
- Modify: `.github/workflows/ci.yml`（st_metabolism 冒烟步 L183-186 之后）

- [x] **Step 3.1: ci.yml 追加冒烟步**

st_metabolism 冒烟步（L183-186）之后插入：

```yaml
      - name: st_score_weight 合成冒烟（q05 丰度×scores 真值回收+全零型剔除+三拒收断网端到端）
        env:
          BIO_WORKSPACE_ROOT: ${{ runner.temp }}/bio_ws
        run: python scripts/_smoke_st_score_weight.py
```

- [x] **Step 3.2: 三重门禁**

Run: `ruff check .` → `python -m mypy` → `python -m pytest -m "not pg" -q`
Expected: 全绿，pytest 1366 passed 零回归

- [x] **Step 3.3: Commit + push + CI 验证**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: docker-smoke 接入 st_score_weight 冒烟（Phase 76 丰度加权真值回收）"
git push
```

Expected: pre-push 四连门全绿；push 后仓库已转 public（Actions 免费），`gh run list --limit 2` 应见新 run 进入 queued/in_progress（若 gh 无 token 则由用户网页确认；两个新冒烟步在手动触发的 bio-image-smoke job，需另触发 workflow_dispatch 验收——沿用 Phase 75 Task 4 口径）。

---

### Task 4: oscc deconvolve 前置（真机长任务，~2h 后台）

**Files:**
- Create: `bio_workspace/_eval/real_st_deconvolve_oscc.py`（bio_workspace 被 .gitignore，提交需 `git add -f`）

- [x] **Step 4.1: 写 deconvolve 驱动脚本**

参数全部沿用 2026-09-12 真实 Visium 验收定型口径（`_validate_st_real_chain.py` L145-148 + 测试总结 L847-876）；容器侧 payload 键名按 `l3_spatial.py` st_deconvolve handler L197-205 的 `sc_ref_path` 分支（bio_test_data 目录挂载 /data）；st 镜像 + /opt/st_tools（注意与 Task 1 工具的 bio 镜像不同）：

```python
"""oscc 反卷积前置（Phase 76 spec §6.1）：st_deconvolve 真机长任务。

参数定型（_validate_st_real_chain.py L146-148 + 2026-09-12 验收口径）：
sc_ref=bio_test_data/oscc_sc_ref_sub.h5ad（/data 挂载）、
ref_label_col=cell_type、max_epochs=2000、ref_max_cells_per_type=150。
CPU 量级：ref 训练 ~15min + 空间映射 ~2h（deconvolve.py L8-15 钉注），
driver 侧 subprocess 超时放宽到 9000s。幂等：deconv.h5ad 已存在则跳过。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

WS = Path("bio_workspace").resolve()
REF = Path("bio_test_data").resolve()
IMG = "feishu-research-agent/bio:st-cpu-latest"
DS = "oscc"

out_h5ad = WS / DS / "deconv.h5ad"
if out_h5ad.exists():
    print(f"[skip] {out_h5ad} 已存在，幂等跳过")
    sys.exit(0)
assert (REF / "oscc_sc_ref_sub.h5ad").exists(), "缺 sc 参考 h5ad"
assert (WS / DS / "processed.h5ad").exists(), "缺 oscc processed.h5ad"

base = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
        "-v", f"{str(REF).replace(chr(92), '/')}:/data",
        "-v", f"{str(Path('sandbox/st_tools').resolve()).replace(chr(92), '/')}"
        ":/opt/st_tools", IMG]
payload = {"dataset_id": DS, "sc_ref_path": "oscc_sc_ref_sub.h5ad",
           "ref_label_col": "cell_type", "max_epochs": 2000,
           "ref_epochs": 250, "num_samples": 1000,
           "ref_max_cells_per_type": 150,
           "n_cells_per_location": 8.0, "detection_alpha": 20.0}
t0 = time.time()
p = subprocess.run(base + ["python", "/opt/st_tools/deconvolve.py"],
                   input=json.dumps(payload), capture_output=True,
                   text=True, timeout=9000)
out = json.loads(p.stdout)
assert out["ok"], out
print(f"[deconvolve] 耗时 {time.time() - t0:.0f}s, "
      f"n_cell_types={out['n_cell_types']}, n_ref_cells={out['n_ref_cells']}")
print(f"[deconvolve] cell_types: {out['cell_types']}")
print(f"[deconvolve] mean_abundance: {out['mean_abundance']}")
assert out_h5ad.exists()
print("REAL DECONV OK: oscc deconv.h5ad 落盘")
```

- [x] **Step 4.2: 后台跑长任务**

Run（非阻塞后台，轮询查状态）: `python bio_workspace/_eval/real_st_deconvolve_oscc.py`
Expected: 输出 `REAL DECONV OK: oscc deconv.h5ad 落盘`（耗时约 2h；ref 训练阶段无 stdout 属正常，cell2location 进度在 stderr）。
若超时/失败：如实记录报错尾部（stderr 最后 600 字符），不阻塞 Task 5 之外的交付；历史教训（三次超时+僵尸容器）见测试总结 L863-876。

- [x] **Step 4.3: ruff + Commit**

```bash
ruff check bio_workspace/_eval/real_st_deconvolve_oscc.py
git add -f bio_workspace/_eval/real_st_deconvolve_oscc.py
git commit -m "feat: oscc deconvolve 前置驱动（Phase 76，历史定型参数幂等长任务）"
```

---

### Task 5: oscc 真机验收（双 source + S1/S2/S3 预注册判据）

**Files:**
- Create: `bio_workspace/_eval/real_st_score_weight_oscc.py`（git add -f）

- [x] **Step 5.1: 写验收脚本**

前置：Task 4 已落 `bio_workspace/oscc/deconv.h5ad`。S1/S2/S3 判据按 spec §6.3 预注册，不过不阻塞、如实记录：

```python
"""st_score_weight 真机验收（Phase 76 spec §6.2-6.4）：oscc。

前置：real_st_deconvolve_oscc.py 已落 deconv.h5ad（否则本脚本直接
断言退出）。双 source 跑通 + 预注册判据（不过不阻塞）：
  S1 Epithelial cells top3 含 JAK-STAT/EGFR/TGFb 之一（依据：
     2026-09-12 病理交叉验收 + Phase 75 Task 6 各域 top3）；
  S2 TGFb 在 Epithelial 行内排名 ≤ 其在 W 列均值口径的排名（加权
     不颠覆既有方向）；
  S3 dropped_celltypes 为空或仅低丰度型（如实记录）。
产物断言齐 + 判据打印 + summary json 落 _eval/ = 验收完成。
"""
import json
import subprocess
from pathlib import Path

import pandas as pd

WS = Path("bio_workspace").resolve()
IMG = "feishu-research-agent/bio:cpu-latest"
DS = "oscc"
base = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
        "-v", f"{str(Path('sandbox/sc_tools').resolve()).replace(chr(92), '/')}"
        ":/opt/sc_tools", IMG]


def run_tool(script: str, payload: dict, timeout: int = 600) -> dict:
    """容器断网跑单个 st 工具，断言 ok 并返回 emit dict。"""
    p = subprocess.run(
        base + ["python", f"/opt/sc_tools/{script}"],
        input=json.dumps(payload), capture_output=True, text=True,
        timeout=timeout)
    out = json.loads(p.stdout)
    assert out["ok"], out
    return out


def host(p: str) -> Path:
    """容器 /ws/<rel> → 宿主路径并断言存在。"""
    q = WS / Path(p).relative_to("/ws")
    assert q.exists(), p
    return q


assert (WS / DS / "deconv.h5ad").exists(), "先跑 real_st_deconvolve_oscc.py"

# ① source=st_genescore（14 通路）
o1 = run_tool("st_score_weight.py",
              {"dataset_id": DS, "source": "st_genescore"})
print(f"[gs] celltypes={o1['n_celltypes']} pathways={o1['n_pathways']} "
      f"overlap={o1['n_spots_overlap']} dropped={o1['dropped_celltypes']}")
for v in o1["products"].values():
    host(v)
print("[gs] 各细胞型 top3 通路：")
for ct, tops in o1["top_by_celltype"].items():
    print(f"  {ct}: {tops}")

# ② 判据（基于 st_genescore 口径矩阵）
w = pd.read_csv(host(o1["products"]["scores_csv"]), index_col=0)
epi = [c for c in w.index if "pithelial" in str(c)]
assert epi, f"未找到 Epithelial 型: {list(w.index)}"
ep = epi[0]
s1_hits = [t for t in o1["top_by_celltype"][ep][:3]
           if t in ("JAK-STAT", "EGFR", "TGFb")]
s1 = bool(s1_hits)
tgfb_rank_epi = float(w.loc[ep].rank(ascending=False)["TGFb"])
tgfb_rank_mean = float(w.mean(axis=0).rank(ascending=False)["TGFb"])
s2 = tgfb_rank_epi <= tgfb_rank_mean
s3 = o1["dropped_celltypes"]
print(f"[S1] {ep} top3 命中 {s1_hits or '无'} → {'pass' if s1 else 'not-pass'}")
print(f"[S2] TGFb 排名: Epithelial 行内 {tgfb_rank_epi:.0f} vs "
      f"列均值口径 {tgfb_rank_mean:.0f} → {'pass' if s2 else 'not-pass'}")
print(f"[S3] dropped_celltypes={s3 or '空'}")

# ③ source=st_metabolism（315 通路）
o2 = run_tool("st_score_weight.py",
              {"dataset_id": DS, "source": "st_metabolism"})
print(f"[mb] celltypes={o2['n_celltypes']} pathways={o2['n_pathways']} "
      f"overlap={o2['n_spots_overlap']} dropped={o2['dropped_celltypes']}")
for v in o2["products"].values():
    host(v)
print("[mb] 各细胞型 top3 代谢通路（截断 40 字）：")
for ct, tops in o2["top_by_celltype"].items():
    print(f"  {ct}: {[t[:40] for t in tops]}")

summary = {
    "dataset": DS,
    "n_celltypes": o1["n_celltypes"],
    "criteria": {
        "S1_epithelial_top3_hits": {"pass": s1, "detail": s1_hits},
        "S2_tgfb_rank_not_worse": {
            "pass": s2,
            "detail": {"epithelial_rank": tgfb_rank_epi,
                       "colmean_rank": tgfb_rank_mean}},
        "S3_dropped_celltypes": {"pass": True,
                                 "detail": s3},
    },
    "top_by_celltype_gs": o1["top_by_celltype"],
    "note": "判据不过不阻塞（spec §6.3），如实记录于测试总结 #21",
}
(WS / "_eval" / "real_st_score_weight_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print("\nREAL OK: st_score_weight oscc 双 source 验收完成"
      "（判据如实记录于测试总结 #21）")
```

- [x] **Step 5.2: 跑真机验收**

Run: `python bio_workspace/_eval/real_st_score_weight_oscc.py`
Expected: 末尾输出 `REAL OK`；记录：①两 source 的 n_celltypes/n_pathways/dropped；②Epithelial top3 与 S1/S2/S3 判定值；③各细胞型 top3（gs/mb 两口径）。

- [x] **Step 5.3: ruff + Commit**

```bash
ruff check bio_workspace/_eval/real_st_score_weight_oscc.py
git add -f bio_workspace/_eval/real_st_score_weight_oscc.py
git commit -m "feat: st_score_weight oscc 真机验收（双 source+S1/S2/S3 预注册判据，Phase 76）"
```

---

### Task 6: 测试总结 #21 回填 + 收口

**Files:**
- Modify: `测试总结+2026-09-09T01-55-00.md`（文末追加 #21 段落）
- Modify: 本计划文件（勾选全部 checkbox）

- [x] **Step 6.1: 回填测试总结**

在 `测试总结+2026-09-09T01-55-00.md` 文末追加 `## 2026-09-2X Phase 76 收口回填（#21 …）` 段落，沿用该文件既有"日期+Phase 段落"压缩文体，须涵盖：

- ① 工具+冒烟：st_score_weight 4 场景全绿（CellA×TGFb 真值回收实测值 / CellC 全零剔除 / 三拒收），commit hash
- ② 契约+接线：TDD 红→绿；三守卫 passed 数；pytest 1366 passed（1362+4），commit hash
- ③ CI：ci.yml 冒烟步接入，push hash；远端 CI 状态（public 后首次真实运行结果，含 bio-image-smoke 手动触发验收情况）
- ④ deconvolve 前置：耗时、n_cell_types、cell_types 清单、mean_abundance 要点，commit hash
- ⑤ 真机验收：双 source n_celltypes/n_pathways、Epithelial top3、S1/S2/S3 判定值、summary json 路径，commit hash
- 后续建议/挂账：如 deconvolve 长任务失败则挂账；生物学解读深化（如 CAF×TGFb 与联读结论交叉）另立

- [x] **Step 6.2: 勾选本计划全部 checkbox + 最终 push**

```bash
git add "测试总结+2026-09-09T01-55-00.md" docs/superpowers/plans/2026-09-21-phase76-st-score-weight.md
git commit -m "docs: 测试总结 #21 回填+Phase 76 计划勾选收口"
git push
```

Expected: pre-push 四连门全绿，Phase 76 收口。

---

## 自审清单（计划落盘前已核对）

- [x] **spec 覆盖**：§2 算法→Task 1；§3 接口/产物→Task 1；§4 错误码四档→Task 1（ST_WEIGHT_NO_DECONV×2 / INVALID_INPUT×2 全覆盖，冒烟场景②③④对应）；§5 测试四件套→Task 1（冒烟）/Task 2（契约+三守卫）/Task 3（CI）；§6 真机→Task 4/5；§7 非目标无任何 Task 越界
- [x] **无占位符**：全部步骤含完整代码/命令/预期输出
- [x] **类型一致**：容器 emit 键（n_celltypes/n_pathways/n_spots_overlap/dropped_celltypes/top_by_celltype/products.scores_csv|heatmap_png）与契约测试 fixture、验收脚本读取完全一致；handler 参数 dataset_ref→dataset_id 透传与 BioRunner.run 签名一致
- [x] **行号锚点**：l3_spatial.py L33/L469/L1126、section_digest.py L60、test_l3_spatial.py L31/L165、附录 A L110、ci.yml L183-186 均按当前 HEAD（2b713ca）实读核对；行号漂移时按内容锚点定位
- [x] **关键陷阱**：①deconvolve 走 st 镜像+/opt/st_tools（与工具本身的 bio 镜像不同，Task 4 挂载已区分）；②scores csv 首列 groupby 位置性丢弃（`iloc[:, 1:]`）；③q05 列名防御性前缀剥离（0.1.5 坑，测试总结+2026-09-02 L86-138）；④bio_workspace 两脚本需 `git add -f`；⑤全零细胞型剔除防 0/0 NaN

## 执行交接

**计划完成，两种执行方式：**
1. **Subagent-Driven（推荐）**——每 Task 派新 subagent，任务间两段式审查
2. **Inline**——本会话按 executing-plans 批量执行，Task 3/4/5 三个 push/长任务/真机步骤后设检查点

注：Task 4 是 ~2h 后台长任务，建议执行时先启动 Task 4 后台跑，并行做 Task 1-3（互不依赖：Task 1-3 不读 deconv.h5ad）。
