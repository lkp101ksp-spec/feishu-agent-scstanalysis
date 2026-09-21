# Phase 75 实施计划：genescore×cytosig 联读 + st_genescore / st_metabolism

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 兑现两条挂账——①真机联读脚本 `real_genescore_cytosig_599sub.py`（PROGENy 通路活性 × CytoSig beta 簇级 Spearman）；②两个 spot 级空间容器工具 `st_genescore.py` / `st_metabolism.py` 并完成 L3 注册与 CI 冒烟。

**Architecture:** 零镜像改动——st 双工具放 `sandbox/sc_tools/`（st_integrate/st_nichenet 先例），跑 bio 镜像（PROGENy 快照 + KEGG json 已在镜像层）；与 sc 版三点差异：obsm.spatial 校验、空间着色主图、groupby 默认 spatial_domain。联读不工具化（_eval/ 一次性脚本），599sub 鼠源 Titlecase 用宿主侧大写影子数据集桥接 human-only 卫兵。

**Tech Stack:** decoupler 2.x（dc.mt.mlm / dc.mt.aucell）、scanpy、anndata、scipy.stats.spearmanr、matplotlib；宿主侧 pytest 契约测试（mock BioRunner）+ docker 断网冒烟。

**Spec:** `docs/superpowers/specs/2026-09-20-phase75-crossread-stgenescore-stmetabolism-design.md`（§2 工作项 A / §3 工作项 B / §4 真机验收）

**质量门（每任务通用）:** `ruff check .` → `python -m mypy`（sandbox 在 mypy 包内，须过 strict；bio_workspace/ 不在包内）→ `python -m pytest -m "not pg" -q`

---

## 文件结构总览

| 动作 | 路径 | 职责 |
|---|---|---|
| Create | `sandbox/sc_tools/st_genescore.py` | spot 级 PROGENy 14 通路（MLM）容器工具 |
| Create | `sandbox/sc_tools/st_metabolism.py` | spot 级 KEGG 代谢（AUCell/mean）容器工具 |
| Create | `scripts/_smoke_st_genescore.py` | 断网冒烟：空间网格 + TGFb 注入真值回收 |
| Create | `scripts/_smoke_st_metabolism.py` | 断网冒烟：空间网格 + Glycolysis 注入 |
| Create | `tests/unit/test_l3_st_genescore.py` | 注册契约（mock BioRunner，4 用例） |
| Create | `tests/unit/test_l3_st_metabolism.py` | 注册契约（mock BioRunner，4 用例） |
| Modify | `orchestrator/tools/builtin/l3_spatial.py` | 2 常量 + 2 handler + 2 ToolSpec（文件尾） |
| Modify | `tests/unit/test_l3_spatial.py` | 两处工具名清单 18→20 |
| Modify | `orchestrator/report/section_digest.py` | SECTION_TITLES +2 行 |
| Modify | `docs/superpowers/specs/2026-09-17-execution-plane-unification-design.md` | 附录 A 1800 档行 +2 工具名 |
| Modify | `.github/workflows/ci.yml` | docker-smoke +2 冒烟步 |
| Create | `bio_workspace/_eval/real_genescore_cytosig_599sub.py` | 工作项 A 联读真机脚本 |
| Create | `bio_workspace/_eval/real_st_gm_oscc.py` | 工作项 B oscc 真机验收 |
| Modify | `测试总结+2026-09-19T00-12-54.md` | #20 回填收口 |

**守护测试联动（决定 Task 3 必须单 commit）:** 注册后自动被三个守护测试盯上——`test_l3_dispatch_contract.py`（超时/资源透传 + 附录 A 双向表）、`test_report_digest.py` L195/212（SECTION_TITLES 漏映射即红）、`test_l3_spatial.py` 两清单。故 l3_spatial + section_digest + 附录 A 必须同 commit 落地。

---

### Task 1: st_genescore 容器工具 + 本地断网冒烟

**Files:**
- Create: `sandbox/sc_tools/st_genescore.py`
- Create: `scripts/_smoke_st_genescore.py`

- [x] **Step 1.1: 写容器工具 `sandbox/sc_tools/st_genescore.py`**

逐行复用 `genescore.py`（sc 版），仅加三处 st 语义（obsm.spatial 校验 / 空间着色主图 / 默认 spatial_domain + 域数 ≥2）：

```python
"""st_genescore：spot 级 PROGENy 14 通路活性打分（Phase 75 空间版）。

stdin: {"dataset_id": ..., "groupby": "spatial_domain", "top_n": 14}
与 sc 版（genescore.py）三点差异（phase75 spec §3.1）：
①载入后校验 obsm["spatial"] 形状 (n,≥2)，无→INVALID_INPUT 引导
  sc_genescore；
②主图换空间着色图（方差 top1 通路 × obsm.spatial，等比坐标），UMAP
  存在则附加产出；
③groupby 默认 spatial_domain（sc 版 leiden），域数 <2 拒收引导换列。
模型快照/MLM 算法/低重叠门槛与 sc 版逐行一致；产物目录 {ds}/
st_genescore/ 与 sc 版 genescore/ 互不覆盖。仅支持 human（PROGENy
无 mouse 模型）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, load_adata, read_args, run, species_style_guard

PROGENY_TSV = Path("/opt/progeny/progeny_human_top500.tsv")
MIN_GENE_OVERLAP = 100  # 与 sc 版/st_misty 同口径：靶基因总交集下限
MIN_PATHWAYS = 7  # 14 通路至少活下来一半，否则视为低重叠
MIN_DOMAINS = 2  # 单域无组间方差可言，引导换列（spec §5 风险对策）


def main() -> None:
    """主流程：PROGENy MLM spot 级打分 → 域×通路均值 + 空间着色图。"""
    import decoupler as dc
    import matplotlib.pyplot as plt

    args = read_args()
    groupby = str(args.get("groupby", "spatial_domain")).strip()
    top_n = int(args.get("top_n", 14))

    if not PROGENY_TSV.exists():
        fail("GENESCORE_NO_MODEL", f"{PROGENY_TSV} 缺失（镜像快照层异常，重建 bio 镜像）")
        raise SystemExit(1)
    net = pd.read_csv(PROGENY_TSV, sep="\t")

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    sp = np.asarray(adata.obsm.get("spatial", []))
    if sp.ndim != 2 or sp.shape[1] < 2 or sp.shape[0] != adata.n_obs:
        fail("INVALID_INPUT",
             "缺 obsm['spatial']（需 (n,≥2) 空间坐标）；本工具为 spot 级"
             "空间版，普通 sc 数据请用 sc_genescore")
        raise SystemExit(1)
    species_style_guard("human", adata.var_names, "SC_SPECIES_MISMATCH")
    has_raw = adata.raw is not None
    universe = set(adata.raw.var_names) if has_raw else set(adata.var_names)

    n_overlap = len(set(net["target"]) & universe)
    if n_overlap < MIN_GENE_OVERLAP:
        fail(
            "GENESCORE_LOW_OVERLAP",
            f"PROGENy 靶基因与数据交集过少: {n_overlap} (<{MIN_GENE_OVERLAP}，基因名需为人类 symbol)",
        )
        raise SystemExit(1)

    if groupby not in adata.obs.columns:
        fail("INVALID_INPUT", f"groupby 列 {groupby!r} 不在 obs；可用列: {list(adata.obs.columns)[:20]}")
        raise SystemExit(1)
    grp = adata.obs[groupby].astype(str)
    if grp.nunique() < MIN_DOMAINS:
        fail("INVALID_INPUT",
             f"groupby {groupby!r} 仅 {grp.nunique()} 个域（<{MIN_DOMAINS} 无组间方差）；"
             "spatial 数据建议 spatial_domain，或显式传其它注释列")
        raise SystemExit(1)

    dc.mt.mlm(adata, net, tmin=5, raw=has_raw, verbose=False)
    scores = adata.obsm["score_mlm"].astype(np.float32)
    dead = [c for c in scores.columns if bool(scores[c].isna().all())]
    scores = scores.drop(columns=dead)
    if scores.shape[1] < MIN_PATHWAYS:
        fail(
            "GENESCORE_LOW_OVERLAP", f"可打分通路仅 {scores.shape[1]} (<{MIN_PATHWAYS}，全 NaN 通路: {dead})"
        )
        raise SystemExit(1)

    ds_dir = WS_ROOT / args["dataset_id"] / "st_genescore"
    ds_dir.mkdir(parents=True, exist_ok=True)

    full = scores.copy()
    full.insert(0, groupby, grp.values)
    scores_csv = ds_dir / "st_progeny_scores.csv"
    full.to_csv(scores_csv)

    group_mean = full.groupby(groupby, sort=False).mean()
    order = sorted(group_mean.index, key=lambda c: (len(c), c))
    group_mean = group_mean.loc[order]
    mean_csv = ds_dir / "st_progeny_group_mean.csv"
    group_mean.to_csv(mean_csv)

    variances = group_mean.var(axis=0)
    top = variances.sort_values(ascending=False).head(top_n)
    top_terms = list(top.index)
    hm = group_mean[top_terms].to_numpy(dtype=float)
    mu = hm.mean(axis=1, keepdims=True)
    sd = hm.std(axis=1, keepdims=True)
    z = np.divide(hm - mu, sd, out=np.zeros_like(hm), where=sd > 0)
    fig, ax = plt.subplots(figsize=(8, 0.28 * len(top_terms) + 1.6))
    im = ax.imshow(z, aspect="auto", cmap="RdBu_r", vmin=-2, vmax=2)
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels(order, fontsize=9)
    ax.set_yticks(range(len(top_terms)))
    ax.set_yticklabels(top_terms, fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.03, label="z-score (per pathway)")
    ax.set_title(f"PROGENy pathway activity by {groupby} (spatial)", fontsize=10)
    fig.tight_layout()
    heatmap_png = ds_dir / "st_progeny_heatmap.png"
    fig.savefig(heatmap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 空间着色主图（st 版差异②）：方差 top1 通路 × obsm.spatial，等比防组织形变
    top_col = top_terms[0]
    fig, ax = plt.subplots(figsize=(5, 4))
    s = ax.scatter(
        sp[:, 0], sp[:, 1], s=7,
        c=scores[top_col].to_numpy(dtype=float), cmap="viridis", linewidths=0
    )
    fig.colorbar(s, ax=ax, fraction=0.046)
    ax.set_title(f"PROGENy spatial: {top_col}", fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    spatial_png = ds_dir / "st_progeny_spatial.png"
    fig.savefig(spatial_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    umap_png = None
    if "X_umap" in adata.obsm:
        umap = np.asarray(adata.obsm["X_umap"])
        fig, ax = plt.subplots(figsize=(5, 4))
        s = ax.scatter(
            umap[:, 0], umap[:, 1], s=4,
            c=scores[top_col].to_numpy(dtype=float), cmap="viridis", linewidths=0
        )
        fig.colorbar(s, ax=ax, fraction=0.046)
        ax.set_title(f"PROGENy: {top_col}", fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.tight_layout()
        umap_png = ds_dir / "st_progeny_umap.png"
        fig.savefig(umap_png, dpi=150, bbox_inches="tight")
        plt.close(fig)

    top_by_group = {
        c: group_mean.loc[c].sort_values(ascending=False).head(3).index.tolist()
        for c in order
    }
    emit(
        {
            "ok": True,
            "dataset_ref": args["dataset_id"],
            "groupby": groupby,
            "method": "progeny_mlm",
            "n_spots": int(adata.n_obs),
            "n_pathways_total": int(net["source"].nunique()),
            "n_pathways_scored": int(scores.shape[1]),
            "n_domains": int(grp.nunique()),
            "dropped_pathways": dead,
            "top_by_group": top_by_group,
            "products": {
                "scores_csv": str(scores_csv),
                "group_mean_csv": str(mean_csv),
                "heatmap_png": str(heatmap_png),
                "spatial_png": str(spatial_png),
                "umap_png": str(umap_png) if umap_png else None,
            },
        }
    )


if __name__ == "__main__":
    run(main)
```

- [x] **Step 1.2: 写冒烟脚本 `scripts/_smoke_st_genescore.py`**

照抄 `_smoke_sc_genescore.py` 骨架，合成数据换 8×5 空间网格（左半 D1 注入 TGFb、右半 D2 纯噪声），加 ⑤无 spatial / ⑥单域 两个 st 专属拒收场景：

```python
"""st_genescore 冒烟（Phase 75 空间版，spec
2026-09-20-phase75-crossread-stgenescore-stmetabolism-design.md §3.5）：
空间网格 + spatial_domain 真值注入 + 容器断网跑。

场景：
①主场景：D1 域（网格左半）注入 2.0×TGFb top100 权重向量 → ok +
  n_pathways_scored=14 + n_domains=2 + 四产物落盘（含 spatial png）；
②csv 内容：TGFb@D1 组均值 idxmax 且 > D2×3、scores csv 行数=spot 数
  且含 spatial_domain 列、top_by_group 含 TGFb；
③低重叠拒收（600 FAKE 基因）→ GENESCORE_LOW_OVERLAP；
④groupby 缺列 → INVALID_INPUT + 候选列提示；
⑤无 obsm.spatial → INVALID_INPUT + 引导 sc_genescore（st 门槛）；
⑥单域 groupby → INVALID_INPUT（<2 域无组间方差）。

合成 h5ad：14 通路各 top50 靶基因并集 + 200 噪声基因 × 40 spot（8×5
网格，左半 D1/右半 D2 空间连续），N(6,0.8) 基底（seed=7，无 raw 层→
测 X 分支）；通路表从镜像动态导出（不硬编码）。
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", Path(__file__).resolve().parents[1] / "bio_workspace"))
DATA = WS / "_smoke_st_genescore_data"
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

DUMP_CMD = (
    "import pandas as pd; "
    "net = pd.read_csv('/opt/progeny/progeny_human_top500.tsv', "
    "sep='\\t'); "
    "net.to_csv('/ws/_smoke_st_genescore_data/progeny_full.csv', "
    "index=False)"
)

DATA.mkdir(parents=True, exist_ok=True)

r0 = subprocess.run(BASE + ["python", "-c", DUMP_CMD], capture_output=True, text=True, timeout=300)
if r0.returncode != 0:
    raise SystemExit(f"model dump rc={r0.returncode}\n{r0.stderr[-600:]}")
net = pd.read_csv(DATA / "progeny_full.csv", sep=None, engine="python")
assert net["source"].nunique() == 14, net["source"].unique()

top50 = net.sort_values("weight", ascending=False).groupby("source", sort=False).head(50)
tgfb = net[net["source"] == "TGFb"].nlargest(100, "weight")
tgfb_w = dict(zip(tgfb["target"], tgfb["weight"]))
UNIVERSE = sorted(set(top50["target"]) | set(tgfb_w))
assert len(UNIVERSE) >= 500, len(UNIVERSE)


def build_gex(ds: str, genes: list[str], inject_d1: bool,
              spatial: bool = True, single_domain: bool = False) -> str:
    """合成 processed.h5ad：genes × 40 spot（8×5 网格左半 D1/右半 D2）；
    inject_d1 时 D1 加 2.0×TGFb top100 权重向量（seed=7）。"""
    import anndata as ad

    rng = np.random.default_rng(7)
    n = 40
    cols = np.arange(n) % 8
    rows = np.arange(n) // 8
    dom = np.where(cols < 4, "D1", "D2")
    x = rng.normal(6.0, 0.8, (n, len(genes))).astype(np.float32)
    if inject_d1:
        sig_vec = np.array([tgfb_w.get(g, 0.0) for g in genes], dtype=np.float32)
        x[dom == "D1"] += 2.0 * sig_vec
    a = ad.AnnData(X=x)
    a.var_names = genes
    a.obs_names = [f"S{i:02d}" for i in range(n)]
    a.obs["spatial_domain"] = pd.Categorical(
        ["D1"] * n if single_domain else dom.tolist())
    if spatial:
        a.obsm["spatial"] = np.column_stack([cols, rows]).astype(np.float64)
    d = WS / ds
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "processed.h5ad")
    return ds


DS_MAIN = build_gex("_smoke_st_genescore_gex", UNIVERSE + [f"NOISE{i:04d}" for i in range(200)], True)
DS_LOWOV = build_gex("_smoke_st_genescore_lowov", [f"FAKE{i:05d}" for i in range(600)], False)
DS_NOSPAT = build_gex("_smoke_st_genescore_nospat", UNIVERSE, False, spatial=False)
DS_1DOM = build_gex("_smoke_st_genescore_1dom", UNIVERSE, False, single_domain=True)


def run_gs(**kw):
    """容器内跑 st_genescore.py（stdin JSON），整体解析 stdout。"""
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/st_genescore.py"],
        input=json.dumps(kw),
        capture_output=True,
        text=True,
        timeout=1200,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 主场景：14 通路全打分 + 四产物落盘（含空间图）
o1 = run_gs(dataset_id=DS_MAIN)
assert o1["ok"], o1
assert o1["method"] == "progeny_mlm" and o1["n_spots"] == 40, o1
assert o1["n_pathways_scored"] == 14, o1
assert o1["n_domains"] == 2, o1
assert o1["dropped_pathways"] == [], o1["dropped_pathways"]
for key in ("scores_csv", "group_mean_csv", "heatmap_png", "spatial_png"):
    assert (WS / Path(o1["products"][key]).relative_to("/ws")).exists(), o1["products"][key]
assert o1["products"]["umap_png"] is None  # 合成数据无 X_umap 属预期
assert "TGFb" in o1["top_by_group"]["D1"][:3], o1["top_by_group"]
print("① 主场景：14/14 通路 + 四产物落盘（含 spatial png）OK")

# ② csv 内容：TGFb@D1 idxmax 且 > D2×3；行数对齐 + 含 spatial_domain 列
gm = pd.read_csv(WS / Path(o1["products"]["group_mean_csv"]).relative_to("/ws"), index_col=0)
tgfb_d1, tgfb_d2 = float(gm.loc["D1", "TGFb"]), float(gm.loc["D2", "TGFb"])
assert gm.loc["D1"].idxmax() == "TGFb", gm.loc["D1"].sort_values(ascending=False).head(5)
assert tgfb_d1 > tgfb_d2 * 3.0, (tgfb_d1, tgfb_d2)
sc = pd.read_csv(WS / Path(o1["products"]["scores_csv"]).relative_to("/ws"))
assert len(sc) == 40 and "spatial_domain" in sc.columns, sc.shape
print(f"② csv：TGFb@D1={tgfb_d1:.3f} > D2={tgfb_d2:.3f}×3 + 40 行含 spatial_domain 列 OK")

# ③ 低重叠拒收
o3 = run_gs(dataset_id=DS_LOWOV)
assert not o3["ok"] and o3["error_code"] == "GENESCORE_LOW_OVERLAP", o3
print("③ 低重叠 GENESCORE_LOW_OVERLAP OK")

# ④ groupby 缺列拒收（带候选列提示）
o4 = run_gs(dataset_id=DS_MAIN, groupby="not_a_col")
assert not o4["ok"] and o4["error_code"] == "INVALID_INPUT", o4
assert "spatial_domain" in o4["error_message"], o4
print("④ groupby 缺列 INVALID_INPUT + 候选提示 OK")

# ⑤ 无 obsm.spatial 拒收（st 门槛，引导 sc_genescore）
o5 = run_gs(dataset_id=DS_NOSPAT)
assert not o5["ok"] and o5["error_code"] == "INVALID_INPUT", o5
assert "spatial" in o5["error_message"] and "sc_genescore" in o5["error_message"], o5
print("⑤ 无 obsm.spatial INVALID_INPUT + 引导 sc_genescore OK")

# ⑥ 单域 groupby 拒收
o6 = run_gs(dataset_id=DS_1DOM)
assert not o6["ok"] and o6["error_code"] == "INVALID_INPUT", o6
assert "1 个域" in o6["error_message"], o6
print("⑥ 单域 INVALID_INPUT + 换列引导 OK")

print("\nSMOKE OK: st_genescore 6 场景全绿")
```

- [x] **Step 1.3: 本地跑冒烟**

Run: `python scripts/_smoke_st_genescore.py`
Expected: 六行场景 OK + `SMOKE OK: st_genescore 6 场景全绿`（需本机 docker + bio:cpu-latest 镜像）

- [x] **Step 1.4: lint 冒烟脚本（sandbox 无独立测试，ruff 即门禁）**

Run: `ruff check sandbox/sc_tools/st_genescore.py scripts/_smoke_st_genescore.py`
Expected: 无输出（全绿）

- [x] **Step 1.5: Commit**

```bash
git add sandbox/sc_tools/st_genescore.py scripts/_smoke_st_genescore.py
git commit -m "feat: st_genescore 容器工具+断网冒烟（Phase 75 spot 级 PROGENy，空间网格真值回收）"
```

---

### Task 2: st_metabolism 容器工具 + 本地断网冒烟

**Files:**
- Create: `sandbox/sc_tools/st_metabolism.py`
- Create: `scripts/_smoke_st_metabolism.py`

- [x] **Step 2.1: 写容器工具 `sandbox/sc_tools/st_metabolism.py`**

复用 `metabolism.py`（sc 版）的 AUCell/mean 双口径与 `_matched` 闭包；参数校验统一 `fail("INVALID_INPUT")`（sc 版部分走 ValueError→SCRIPT_ERROR，st 版收敛为显式 INVALID_INPUT，spec §3.4"错误码透传"）；产物目录 `{ds}/st_metabolism/`：

```python
"""st_metabolism：spot 级 KEGG 代谢通路活性（Phase 75 空间版）。

stdin: {"dataset_id": ..., "method": "aucell"|"mean",
        "groupby": "spatial_domain", "species": "human"|"mouse",
        "top_n": 30}
与 sc 版（metabolism.py）三点差异同 st_genescore（obsm.spatial 校验/
空间着色主图/默认 spatial_domain + 域数 ≥2）；method/species 语义与
sc 版一致（aucell=decoupler AUCell，n_up=前 10% 特征；mean=
score_genes 均值差）。参数校验统一 INVALID_INPUT（sc 版部分
ValueError→SCRIPT_ERROR，st 版收敛显式化）。产物目录 {ds}/
st_metabolism/ 与 sc 版 metabolism/ 互不覆盖。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, load_adata, read_args, run, species_style_guard, upper_gene_map

GENE_SET_DIR = Path("/opt/gene_sets")
MIN_PATHWAY_GENES = 5  # 与 scMetabolism/GSEA min_size 惯例一致
MIN_DOMAINS = 2  # 单域无组间方差，引导换列
SPECIES_LIB = {"human": "kegg.json", "mouse": "kegg_mouse.json"}


def main() -> None:
    """主流程：AUCell/score_genes spot 级打分 → 域均值 + 空间着色图。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    top_n = int(args.get("top_n", 30))
    method = str(args.get("method", "aucell")).strip().lower()
    if method not in ("aucell", "mean"):
        fail("INVALID_INPUT", f"method must be 'aucell' or 'mean'; got {method!r}")
        raise SystemExit(1)
    species = str(args.get("species", "human")).strip().lower()
    if species not in SPECIES_LIB:
        fail("INVALID_INPUT", f"species must be one of {sorted(SPECIES_LIB)}; got {species!r}")
        raise SystemExit(1)
    groupby = str(args.get("groupby", "spatial_domain")).strip()
    if not groupby:
        fail("INVALID_INPUT", "groupby must be a non-empty obs column name")
        raise SystemExit(1)

    lib_path = GENE_SET_DIR / SPECIES_LIB[species]
    if not lib_path.exists():
        raise FileNotFoundError(
            f"{lib_path} not found; rebuild bio image with gene_sets stage "
            "(docker build ... sandbox/bio.Dockerfile)")
    pathways: dict[str, list[str]] = json.loads(
        lib_path.read_text(encoding="utf-8"))

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    sp = np.asarray(adata.obsm.get("spatial", []))
    if sp.ndim != 2 or sp.shape[1] < 2 or sp.shape[0] != adata.n_obs:
        fail("INVALID_INPUT",
             "缺 obsm['spatial']（需 (n,≥2) 空间坐标）；本工具为 spot 级"
             "空间版，普通 sc 数据请用 sc_metabolism")
        raise SystemExit(1)
    species_style_guard(species, adata.var_names, "SC_SPECIES_MISMATCH")
    if groupby not in adata.obs:
        fail("INVALID_INPUT",
             f"processed.h5ad lacks {groupby!r}; run st_process first")
        raise SystemExit(1)
    clusters = adata.obs[groupby].astype(str)
    if clusters.nunique() < MIN_DOMAINS:
        fail("INVALID_INPUT",
             f"groupby {groupby!r} 仅 {clusters.nunique()} 个域（<{MIN_DOMAINS} 无组间方差）；"
             "spatial 数据建议 spatial_domain，或显式传其它注释列")
        raise SystemExit(1)

    raw_vars = set(adata.raw.var_names)

    # mouse 库符号全大写，数据 var 是 Mki67 式，upper_gene_map 对齐；
    # human 精确匹配 + 保序去重（Enrichr json 通路内偶见重复基因）
    def _matched(genes: list[str]) -> list[str]:
        """通路基因与数据 var 对齐：mouse 大写映射 / human 精确匹配。"""
        if species == "mouse":
            # str() 收口：mypy 跨模块解析 common 不可见时退化为 Any
            return [str(g) for g in upper_gene_map(sorted(raw_vars), genes)]
        return list(dict.fromkeys(g for g in genes if g in raw_vars))

    terms: list[str] = []
    note: str
    if method == "aucell":
        import decoupler as dc

        net_rows: list[dict[str, object]] = []
        for term, genes in pathways.items():
            matched = _matched(genes)
            if len(matched) < MIN_PATHWAY_GENES:
                continue
            terms.append(term)
            net_rows += [{"source": term, "target": g, "weight": 1.0}
                         for g in matched]
        if not terms:
            raise ValueError(
                f"no KEGG pathway has >= {MIN_PATHWAY_GENES} genes in data")
        # AUCell 排名截断：skill 口径前 10% 特征（decoupler 2.x 默认 top
        # 5%，显式传参覆盖；tmin 与 MIN_PATHWAY_GENES 对齐防 prune 丢通路）
        n_up = int(np.ceil(0.1 * len(raw_vars)))
        dc.mt.aucell(adata, pd.DataFrame(net_rows),
                     tmin=MIN_PATHWAY_GENES, raw=True, n_up=n_up,
                     verbose=False)
        scores_df = adata.obsm["score_aucell"]
        terms = list(scores_df.columns)
        mat = scores_df.to_numpy(dtype=float)  # spot × 通路
        note = (f"AUCell 排名打分（n_up=前10%特征={n_up}）；"
                "method='mean' 回 score_genes 均值差口径")
    else:
        # 逐通路打分（列名用短代号，防 obs 列名过长）
        cols: list[str] = []
        for i, (term, genes) in enumerate(pathways.items()):
            matched = _matched(genes)
            if len(matched) < MIN_PATHWAY_GENES:
                continue
            col = f"pw_{i:03d}"
            sc.tl.score_genes(adata, gene_list=matched, score_name=col,
                              use_raw=True)
            terms.append(term)
            cols.append(col)
        if not terms:
            raise ValueError(
                f"no KEGG pathway has >= {MIN_PATHWAY_GENES} genes in data")
        mat = adata.obs[cols].to_numpy(dtype=float)  # spot × 通路
        note = "均值差口径（score_genes）"

    ds_dir = WS_ROOT / args["dataset_id"] / "st_metabolism"
    ds_dir.mkdir(parents=True, exist_ok=True)

    # spot×通路全矩阵（只落 csv，不进 JSON）
    full = pd.DataFrame(mat, index=adata.obs_names, columns=terms)
    full.insert(0, groupby, clusters.values)
    scores_csv = ds_dir / "st_metabolism_scores.csv"
    full.to_csv(scores_csv)

    # 域 × 通路均值矩阵
    cm = pd.DataFrame(mat, columns=terms)
    cm.insert(0, groupby, clusters.values)
    group_mean = cm.groupby(groupby).mean()
    order = sorted(group_mean.index, key=lambda c: (len(c), c))
    group_mean = group_mean.loc[order]
    mean_csv = ds_dir / "st_metabolism_group_mean.csv"
    group_mean.to_csv(mean_csv)

    # 域间方差 top_n → 热图（通路行 z-score）
    variances = group_mean.var(axis=0)
    top = variances.sort_values(ascending=False).head(top_n)
    top_terms = list(top.index)
    hm = group_mean[top_terms].to_numpy(dtype=float)
    mu = hm.mean(axis=1, keepdims=True)
    sd = hm.std(axis=1, keepdims=True)
    z = np.divide(hm - mu, sd, out=np.zeros_like(hm), where=sd > 0)
    fig, ax = plt.subplots(figsize=(8, 0.28 * len(top_terms) + 1.6))
    im = ax.imshow(z, aspect="auto", cmap="RdBu_r", vmin=-2, vmax=2)
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels(order, fontsize=9)
    ax.set_yticks(range(len(top_terms)))
    ax.set_yticklabels([t[:58] for t in top_terms], fontsize=7)
    fig.colorbar(im, ax=ax, fraction=0.03, label="z-score (per pathway)")
    ax.set_title(f"st metabolism: top {len(top_terms)} variable KEGG pathways",
                 fontsize=10)
    fig.tight_layout()
    heatmap_png = ds_dir / "st_metabolism_heatmap.png"
    fig.savefig(heatmap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 空间着色主图（st 版差异②）：方差 top1 通路 × obsm.spatial
    top_idx = terms.index(top_terms[0])
    fig, ax = plt.subplots(figsize=(5, 4))
    s = ax.scatter(sp[:, 0], sp[:, 1], s=7, c=mat[:, top_idx],
                   cmap="viridis", linewidths=0)
    fig.colorbar(s, ax=ax, fraction=0.046)
    ax.set_title(top_terms[0][:60], fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    spatial_png = ds_dir / "st_metabolism_spatial.png"
    fig.savefig(spatial_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    umap_png = None
    if "X_umap" in adata.obsm:
        umap = np.asarray(adata.obsm["X_umap"])
        fig, ax = plt.subplots(figsize=(5, 4))
        s = ax.scatter(umap[:, 0], umap[:, 1], s=4, c=mat[:, top_idx],
                       cmap="viridis", linewidths=0)
        fig.colorbar(s, ax=ax, fraction=0.046)
        ax.set_title(top_terms[0][:60], fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.tight_layout()
        umap_png = ds_dir / "st_metabolism_umap.png"
        fig.savefig(umap_png, dpi=150, bbox_inches="tight")
        plt.close(fig)

    top_by_group = {
        c: group_mean.loc[c].sort_values(ascending=False).head(3).index.tolist()
        for c in order
    }
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "species": species,
        "method": method,
        "groupby": groupby,
        "method_note": note,
        "n_spots": int(adata.n_obs),
        "n_pathways_total": len(pathways),
        "n_pathways_scored": len(terms),
        "n_domains": int(clusters.nunique()),
        "top_by_group": top_by_group,
        "products": {
            "scores_csv": str(scores_csv),
            "group_mean_csv": str(mean_csv),
            "heatmap_png": str(heatmap_png),
            "spatial_png": str(spatial_png),
            "umap_png": str(umap_png) if umap_png else None,
        },
    })


if __name__ == "__main__":
    run(main)
```

- [x] **Step 2.2: 写冒烟脚本 `scripts/_smoke_st_metabolism.py`**

照抄 `_smoke_sc_metabolism.py` 骨架，80 spot 8×10 网格（左半 D1 糖酵解 +2.5）。**注意注入必须用 `np.ix_`**（`x[mask][:, idx] += v` 是副本写入会静默丢失，sc 版单掩码无此坑、st 版双索引有）：

```python
"""st_metabolism 冒烟（Phase 75 空间版，spec phase75 §3.5）：
空间网格 + Glycolysis 真值注入 + 容器断网跑。

场景：
①method=aucell 主场景：D1 域（网格左半）糖酵解基因 +2.5 → ok +
  n_domains=2 + n_pathways_scored>=300 + 四产物落盘（含 spatial png）；
②csv 内容：Glycolysis 方差 rank<=3 且 D1 均值 > D2×3、top_by_group
  D1 前 3 含 Glycolysis；
③method=mean 回归：score_genes 口径 Glycolysis 仍 D1>D2（z>1.5）；
④method=bad → INVALID_INPUT；
⑤无 obsm.spatial → INVALID_INPUT + 引导 sc_metabolism；
⑥单域 groupby → INVALID_INPUT。

合成 h5ad：KEGG 全通路靶基因并集 + 200 噪声基因 × 80 spot（8×10
网格左半 D1/右半 D2），N(6,0.8) 基底（seed=7），raw=X 同层。
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", Path(__file__).resolve().parents[1] / "bio_workspace"))
DATA = WS / "_smoke_st_metabolism_data"
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")

REPO = Path(__file__).resolve().parents[1]
MOUNTS = [
    "-v",
    f"{str(WS).replace(chr(92), '/')}:/ws",
    "-v",
    f"{str(REPO / 'sandbox' / 'sc_tools').replace(chr(92), '/')}:/opt/sc_tools",
]
BASE = ["docker", "run", "--rm", "-i", "--network", "none", *MOUNTS, IMG]

DUMP_CMD = (
    "import shutil; "
    "shutil.copy('/opt/gene_sets/kegg.json', "
    "'/ws/_smoke_st_metabolism_data/kegg.json')"
)

DATA.mkdir(parents=True, exist_ok=True)

r0 = subprocess.run(BASE + ["python", "-c", DUMP_CMD], capture_output=True, text=True, timeout=300)
if r0.returncode != 0:
    raise SystemExit(f"kegg dump rc={r0.returncode}\n{r0.stderr[-600:]}")
pathways = json.loads((DATA / "kegg.json").read_text(encoding="utf-8"))
assert len(pathways) >= 300, len(pathways)

gly_key = next(k for k in pathways if "Glycolysis" in k)
UNIVERSE = sorted({g for genes in pathways.values() for g in genes})
assert len(UNIVERSE) >= 500, len(UNIVERSE)
gly_genes = [g for g in pathways[gly_key] if g in set(UNIVERSE)]
assert len(gly_genes) >= 20, (gly_key, len(gly_genes))


def build_gex(ds: str, spatial: bool = True,
              single_domain: bool = False) -> str:
    """合成 processed.h5ad：UNIVERSE+噪声 × 80 spot（8×10 网格，
    左半 D1/右半 D2）；D1 糖酵解基因 +2.5（seed=7），raw=X 同层。"""
    import anndata as ad

    rng = np.random.default_rng(7)
    n = 80
    cols = np.arange(n) % 8
    rows = np.arange(n) // 8
    dom = np.where(cols < 4, "D1", "D2")
    genes = UNIVERSE + [f"NOISE{i:04d}" for i in range(200)]
    x = rng.normal(6.0, 0.8, (n, len(genes))).astype(np.float32)
    gly_idx = [genes.index(g) for g in gly_genes]
    # np.ix_ 双索引原位注入（x[mask][:, idx]+=v 是副本写会静默丢失）
    x[np.ix_(np.where(dom == "D1")[0], gly_idx)] += 2.5
    a = ad.AnnData(X=x)
    a.var_names = genes
    a.obs_names = [f"S{i:02d}" for i in range(n)]
    a.obs["spatial_domain"] = pd.Categorical(
        ["D1"] * n if single_domain else dom.tolist())
    if spatial:
        a.obsm["spatial"] = np.column_stack([cols, rows]).astype(np.float64)
    a.raw = a
    d = WS / ds
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "processed.h5ad")
    return ds


DS_MAIN = build_gex("_smoke_st_metabolism_gex")
DS_NOSPAT = build_gex("_smoke_st_metabolism_nospat", spatial=False)
DS_1DOM = build_gex("_smoke_st_metabolism_1dom", single_domain=True)


def run_mt(**kw):
    """容器内跑 st_metabolism.py（stdin JSON），整体解析 stdout。"""
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/st_metabolism.py"],
        input=json.dumps(kw),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 主场景：AUCell 打分 + 四产物落盘（含空间图）
o1 = run_mt(dataset_id=DS_MAIN, species="human", method="aucell")
assert o1["ok"], o1
assert o1["method"] == "aucell" and "AUCell" in o1["method_note"], o1
assert o1["n_spots"] == 80 and o1["n_domains"] == 2, o1
assert o1["n_pathways_scored"] >= 300, o1["n_pathways_scored"]
for key in ("scores_csv", "group_mean_csv", "heatmap_png", "spatial_png"):
    assert (WS / Path(o1["products"][key]).relative_to("/ws")).exists(), o1["products"][key]
assert o1["products"]["umap_png"] is None  # 合成数据无 X_umap 属预期
assert any("Glycolysis" in t for t in o1["top_by_group"]["D1"][:3]), o1["top_by_group"]["D1"]
print(f"① AUCell：{o1['n_pathways_scored']} 通路 + 四产物落盘（含 spatial png）OK")

# ② csv 内容：Glycolysis 方差 rank<=3 且 D1 均值 > D2×3
gm = pd.read_csv(WS / Path(o1["products"]["group_mean_csv"]).relative_to("/ws"), index_col=0)
gly_a, gly_b = float(gm.loc["D1", gly_key]), float(gm.loc["D2", gly_key])
assert gly_a > gly_b * 3.0, (gly_a, gly_b)
sc = pd.read_csv(WS / Path(o1["products"]["scores_csv"]).relative_to("/ws"))
assert len(sc) == 80 and gly_key in sc.columns, (sc.shape, sc.columns[:3])
print(f"② csv：{gly_key}@D1={gly_a:.4f} > D2={gly_b:.4f}×3 + 80 行含通路列 OK")

# ③ method=mean 回归：score_genes 口径 Glycolysis 仍分离
o3 = run_mt(dataset_id=DS_MAIN, species="human", method="mean")
assert o3["ok"] and o3["method"] == "mean", o3
gm3 = pd.read_csv(WS / Path(o3["products"]["group_mean_csv"]).relative_to("/ws"), index_col=0)
gly_a3, gly_b3 = float(gm3.loc["D1", gly_key]), float(gm3.loc["D2", gly_key])
assert gly_a3 > 1.5 and gly_a3 > gly_b3, (gly_a3, gly_b3)
print(f"③ mean 回归：score_genes D1={gly_a3:.3f} > D2={gly_b3:.3f} OK")

# ④ method 非法 → INVALID_INPUT（message 含 method 提示）
o4 = run_mt(dataset_id=DS_MAIN, species="human", method="bad")
assert not o4["ok"] and o4["error_code"] == "INVALID_INPUT", o4
assert "method" in o4["error_message"], o4["error_message"]
print("④ method=bad INVALID_INPUT + 提示 OK")

# ⑤ 无 obsm.spatial 拒收（st 门槛，引导 sc_metabolism）
o5 = run_mt(dataset_id=DS_NOSPAT, species="human", method="aucell")
assert not o5["ok"] and o5["error_code"] == "INVALID_INPUT", o5
assert "spatial" in o5["error_message"] and "sc_metabolism" in o5["error_message"], o5
print("⑤ 无 obsm.spatial INVALID_INPUT + 引导 sc_metabolism OK")

# ⑥ 单域 groupby 拒收
o6 = run_mt(dataset_id=DS_1DOM, species="human", method="aucell")
assert not o6["ok"] and o6["error_code"] == "INVALID_INPUT", o6
assert "1 个域" in o6["error_message"], o6
print("⑥ 单域 INVALID_INPUT + 换列引导 OK")

print("\nSMOKE OK: st_metabolism 6 场景全绿")
```

- [x] **Step 2.3: 本地跑冒烟**

Run: `python scripts/_smoke_st_metabolism.py`
Expected: 六行场景 OK + `SMOKE OK: st_metabolism 6 场景全绿`

- [x] **Step 2.4: lint**

Run: `ruff check sandbox/sc_tools/st_metabolism.py scripts/_smoke_st_metabolism.py`
Expected: 无输出

- [x] **Step 2.5: Commit**

```bash
git add sandbox/sc_tools/st_metabolism.py scripts/_smoke_st_metabolism.py
git commit -m "feat: st_metabolism 容器工具+断网冒烟（Phase 75 spot 级 KEGG，AUCell/mean 双口径）"
```

---

### Task 3: L3 全接线（契约测试 + 注册 + 命名清单 + SECTION_TITLES + 附录 A，单 commit）

**Files:**
- Create: `tests/unit/test_l3_st_genescore.py`
- Create: `tests/unit/test_l3_st_metabolism.py`
- Modify: `orchestrator/tools/builtin/l3_spatial.py`（4 处：常量区 L30 后、handler 区 L427 后、ToolSpec 区文件尾）
- Modify: `tests/unit/test_l3_spatial.py`（两处清单 18→20）
- Modify: `orchestrator/report/section_digest.py`（SECTION_TITLES +2）
- Modify: `docs/superpowers/specs/2026-09-17-execution-plane-unification-design.md`（附录 A 1800 行）

**为什么单 commit:** 注册后 `test_l3_dispatch_contract.py`（附录 A 双向表）、`test_report_digest.py` L195/212（SECTION_TITLES 守护）、`test_l3_spatial.py` 两清单会立即红——六处改动必须同 commit 才绿。

- [x] **Step 3.1: 写失败契约测试 `tests/unit/test_l3_st_genescore.py`**

（模板：`tests/unit/test_l3_sc_genescore.py` 76 行裸 mock 模式）

```python
"""st_genescore 注册测试（mock BioRunner，不发 docker）。"""

from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_spatial import (
    _ST_GENESCORE_TIMEOUT,
    register_l3_spatial,
)
from orchestrator.tools.tool_registry import ToolRegistry


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.run.return_value = {
        "ok": True,
        "dataset_ref": "oscc_1",
        "groupby": "spatial_domain",
        "method": "progeny_mlm",
        "n_spots": 2500,
        "n_pathways_scored": 14,
        "n_domains": 6,
        "top_by_group": {"D1": ["TGFb", "EGFR", "MAPK"]},
        "products": {
            "scores_csv": "/ws/oscc/st_genescore/st_progeny_scores.csv",
            "spatial_png": "/ws/oscc/st_genescore/st_progeny_spatial.png",
        },
    }
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_genescore_registered(reg):
    """第 19 个 st_* 工具：L1_compute、timeout 1800（附录 A 档）、
    浅层 schema、required 仅 dataset_ref、默认 groupby=spatial_domain。"""
    spec = reg.get("st_genescore")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 1800 == _ST_GENESCORE_TIMEOUT
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert props["groupby"]["default"] == "spatial_domain"
    assert props["top_n"]["default"] == 14
    assert props["top_n"]["minimum"] == 3
    assert props["top_n"]["maximum"] == 14
    for p in props.values():
        assert p["type"] in ("string", "integer", "number", "boolean")


def test_st_genescore_dispatches_bio_image(runner, reg):
    """跨镜像分发（bio 镜像 + /opt/sc_tools，st_integrate 先例）+
    三参数透传（dataset_ref→dataset_id 容器侧键名）。"""
    out = reg.get("st_genescore").handler(
        dataset_ref="oscc", groupby="cluster_annotations", top_n=10)
    args, kw = runner.run.call_args
    assert args[0] == "st_genescore"
    assert args[1] == {"dataset_id": "oscc",
                       "groupby": "cluster_annotations", "top_n": 10}
    assert kw["image"] == "feishu-research-agent/bio:cpu-latest"
    assert kw["script_dir"] == "/opt/sc_tools"
    assert kw["timeout_sec"] == _ST_GENESCORE_TIMEOUT
    assert out["n_pathways_scored"] == 14
    assert "ok" not in out


def test_st_genescore_defaults(runner, reg):
    """可选参数默认：groupby=spatial_domain、top_n=14。"""
    reg.get("st_genescore").handler(dataset_ref="d1")
    args, _ = runner.run.call_args
    assert args[1]["groupby"] == "spatial_domain"
    assert args[1]["top_n"] == 14


def test_st_genescore_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出（INVALID_INPUT/GENESCORE_* 容器侧码）。"""
    runner.run.side_effect = BioRunError(
        "INVALID_INPUT", "缺 obsm['spatial']")
    out = reg.get("st_genescore").handler(dataset_ref="d1")
    assert out == {"error_code": "INVALID_INPUT",
                   "error_message": "缺 obsm['spatial']"}
```

- [x] **Step 3.2: 写失败契约测试 `tests/unit/test_l3_st_metabolism.py`**

```python
"""st_metabolism 注册测试（mock BioRunner，不发 docker）。"""

from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_spatial import (
    _ST_METABOLISM_TIMEOUT,
    register_l3_spatial,
)
from orchestrator.tools.tool_registry import ToolRegistry


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.run.return_value = {
        "ok": True,
        "dataset_ref": "oscc_1",
        "species": "human",
        "method": "aucell",
        "groupby": "spatial_domain",
        "method_note": "AUCell 排名打分",
        "n_spots": 2500,
        "n_pathways_scored": 320,
        "n_domains": 6,
        "top_by_group": {"D1": ["Glycolysis / Gluconeogenesis"]},
        "products": {
            "scores_csv": "/ws/oscc/st_metabolism/st_metabolism_scores.csv",
            "spatial_png": "/ws/oscc/st_metabolism/st_metabolism_spatial.png",
        },
    }
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_metabolism_registered(reg):
    """第 20 个 st_* 工具：L1_compute、timeout 1800、species 枚举
    human|mouse 且默认 human（spec §3.4，无空串自动探测）。"""
    spec = reg.get("st_metabolism")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 1800 == _ST_METABOLISM_TIMEOUT
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert props["method"]["enum"] == ["aucell", "mean"]
    assert props["method"]["default"] == "aucell"
    assert props["groupby"]["default"] == "spatial_domain"
    assert props["species"]["enum"] == ["human", "mouse"]
    assert props["species"]["default"] == "human"
    for p in props.values():
        assert p["type"] in ("string", "integer", "number", "boolean")


def test_st_metabolism_dispatches_bio_image(runner, reg):
    """跨镜像分发 + 四参数透传。"""
    out = reg.get("st_metabolism").handler(
        dataset_ref="oscc", method="mean", groupby="cluster_annotations",
        species="mouse")
    args, kw = runner.run.call_args
    assert args[0] == "st_metabolism"
    assert args[1] == {"dataset_id": "oscc", "method": "mean",
                       "groupby": "cluster_annotations", "species": "mouse"}
    assert kw["image"] == "feishu-research-agent/bio:cpu-latest"
    assert kw["script_dir"] == "/opt/sc_tools"
    assert kw["timeout_sec"] == _ST_METABOLISM_TIMEOUT
    assert out["n_pathways_scored"] == 320
    assert "ok" not in out


def test_st_metabolism_defaults(runner, reg):
    """可选参数默认：method=aucell、groupby=spatial_domain、species=human。"""
    reg.get("st_metabolism").handler(dataset_ref="d1")
    args, _ = runner.run.call_args
    assert args[1]["method"] == "aucell"
    assert args[1]["groupby"] == "spatial_domain"
    assert args[1]["species"] == "human"


def test_st_metabolism_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出。"""
    runner.run.side_effect = BioRunError(
        "INVALID_INPUT", "groupby 'x' 仅 1 个域（<2）")
    out = reg.get("st_metabolism").handler(dataset_ref="d1")
    assert out == {"error_code": "INVALID_INPUT",
                   "error_message": "groupby 'x' 仅 1 个域（<2）"}
```

- [x] **Step 3.3: 跑两文件验证失败**

Run: `python -m pytest tests/unit/test_l3_st_genescore.py tests/unit/test_l3_st_metabolism.py -q`
Expected: 收集期 `ImportError: cannot import name '_ST_GENESCORE_TIMEOUT'`（红）

- [x] **Step 3.4: l3_spatial.py 四处编辑**

**4a. 常量区**——`_ST_INTEGRATE_TIMEOUT = 1800` 后追加：

```python
_ST_INTEGRATE_TIMEOUT = 1800
# Phase 75：st 侧通路/代谢（bio 镜像 sc_tools 跨镜像分发，st_integrate 先例）
_ST_GENESCORE_TIMEOUT = 1800
_ST_METABOLISM_TIMEOUT = 1800
```

**4b. handler 区**——`st_integrate` handler 的 `return out` 之后、首个 `registry.register(ToolSpec(` 之前插入：

```python
    def st_genescore(*, dataset_ref: str, groupby: str = "spatial_domain",
                     top_n: int = 14) -> dict[str, Any]:
        """spot 级 PROGENy 通路活性（Phase 75）：要求 obsm.spatial
        （st_process 产物），dc.mt.mlm 逐 spot 计算（无空间平滑），
        groupby 默认 spatial_domain；bio 镜像跨镜像分发，产物落
        {ds}/st_genescore/（与 sc 版 genescore/ 目录互不覆盖）。"""
        try:
            out = runner.run(
                "st_genescore", {
                    "dataset_id": dataset_ref,
                    "groupby": groupby,
                    "top_n": top_n,
                }, image=bio_image, script_dir=_SC_SCRIPT_DIR,
                timeout_sec=_ST_GENESCORE_TIMEOUT)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_metabolism(*, dataset_ref: str, method: str = "aucell",
                      groupby: str = "spatial_domain",
                      species: str = "human") -> dict[str, Any]:
        """spot 级 KEGG 代谢活性（Phase 75）：AUCell（默认）/score_genes
        双口径与 sc_metabolism 一致，要求 obsm.spatial，groupby 默认
        spatial_domain；产物落 {ds}/st_metabolism/。"""
        try:
            out = runner.run(
                "st_metabolism", {
                    "dataset_id": dataset_ref,
                    "method": method,
                    "groupby": groupby,
                    "species": species,
                }, image=bio_image, script_dir=_SC_SCRIPT_DIR,
                timeout_sec=_ST_METABOLISM_TIMEOUT)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out
```

**4c. ToolSpec 区**——文件尾（st_integrate ToolSpec 的最后一个 `))` 之后）追加：

```python
    registry.register(ToolSpec(
        name="st_genescore",
        description=(
            "spot 级空间版本 PROGENy 14 通路活性打分（Phase 75）：要求"
            " obsm['spatial']（st_load/st_process 产物），dc.mt.mlm 逐"
            " spot 计算（无空间平滑），groupby 默认 spatial_domain。"
            "产物落 {ds}/st_genescore/（scores/group_mean/heatmap/"
            "spatial 四件套+可选 umap），与 sc_genescore（细胞级、"
            "leiden 默认）目录互不覆盖。仅支持 human（PROGENy 无 "
            "mouse 模型）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_process 输出的 "
                                               "dataset_ref（含 "
                                               "obsm.spatial）"},
                "groupby": {"type": "string",
                            "default": "spatial_domain",
                            "description": "分组 obs 列（空间域）；"
                                           "域数 <2 拒收引导换列"},
                "top_n": {"type": "integer", "default": 14,
                          "minimum": 3, "maximum": 14,
                          "description": "方差排序取前 N 通路"
                                         "（热图/空间图）"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_genescore,
        timeout_sec=_ST_GENESCORE_TIMEOUT,
    ))
    registry.register(ToolSpec(
        name="st_metabolism",
        description=(
            "spot 级空间版本 KEGG 代谢通路活性（Phase 75）：要求 "
            "obsm['spatial']，AUCell（默认）/score_genes 双口径与 "
            "sc_metabolism 一致，groupby 默认 spatial_domain，species "
            "human|mouse（默认 human）。产物落 {ds}/st_metabolism/"
            "（scores/group_mean/heatmap/spatial 四件套+可选 umap）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_process 输出的 "
                                               "dataset_ref（含 "
                                               "obsm.spatial）"},
                "method": {"type": "string", "default": "aucell",
                           "enum": ["aucell", "mean"],
                           "description": "aucell=排名 AUC（默认，"
                                          "对齐 scMetabolism）；"
                                          "mean=score_genes 均值差"},
                "groupby": {"type": "string",
                            "default": "spatial_domain",
                            "description": "分组 obs 列（空间域）"},
                "species": {"type": "string",
                            "enum": ["human", "mouse"],
                            "default": "human",
                            "description": "KEGG 库（kegg.json / "
                                           "kegg_mouse.json）"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_metabolism,
        timeout_sec=_ST_METABOLISM_TIMEOUT,
    ))
```

- [x] **Step 3.5: test_l3_spatial.py 两处清单 18→20**

**3.5a.** `test_st_registers_fourteen_tools`（L31-43）——docstring 加"Phase 75 +st_genescore/st_metabolism"，清单改为：

```python
    assert names == ["st_cellchat_v2", "st_cnv", "st_commot",
                     "st_deconvolve", "st_domains", "st_genescore",
                     "st_integrate", "st_load", "st_markers",
                     "st_metabolism", "st_misty", "st_niche",
                     "st_niche_scan", "st_nichenet", "st_plot",
                     "st_process", "st_qc", "st_stats", "st_trajectory",
                     "st_vicinity"]
```

**3.5b.** `test_register_fourteen_st_tools`（L162-169）——docstring 改"Phase 75 后 st_* 共 20 工具（18 + st_genescore + st_metabolism）"，清单改为：

```python
    assert names == [
        "st_cellchat_v2", "st_cnv", "st_commot", "st_deconvolve",
        "st_domains", "st_genescore", "st_integrate", "st_load",
        "st_markers", "st_metabolism", "st_misty", "st_niche",
        "st_niche_scan", "st_nichenet", "st_plot", "st_process",
        "st_qc", "st_stats", "st_trajectory", "st_vicinity"]
```

- [x] **Step 3.6: section_digest.py SECTION_TITLES +2**

`"st_domains": "空间结构域识别",` 行后插入两行：

```python
    "st_domains": "空间结构域识别",
    "st_genescore": "空间通路活性分析（PROGENy）",
    "st_metabolism": "空间代谢活性分析",
```

- [x] **Step 3.7: 附录 A 1800 档行 +2 工具名**

`docs/superpowers/specs/2026-09-17-execution-plane-unification-design.md` L111 整行替换为：

```
| 1800 | sc_process, sc_enrichment, sc_score_genes, sc_metabolism, sc_subcluster, sc_integrate, sc_annotate, sc_cytotrace2, sc_cytosig, sc_genescore, st_process, st_commot, st_stats, st_misty, st_nichenet, st_integrate, st_genescore, st_metabolism |
```

- [x] **Step 3.8: 跑全部受影响测试验证通过**

Run: `python -m pytest tests/unit/test_l3_st_genescore.py tests/unit/test_l3_st_metabolism.py tests/unit/test_l3_spatial.py tests/unit/test_l3_dispatch_contract.py tests/unit/test_report_digest.py -q`
Expected: 全 PASS（新 8 用例 + 附录 A/SECTION_TITLES 两守护同步绿）

- [x] **Step 3.9: Commit**

```bash
git add tests/unit/test_l3_st_genescore.py tests/unit/test_l3_st_metabolism.py orchestrator/tools/builtin/l3_spatial.py tests/unit/test_l3_spatial.py orchestrator/report/section_digest.py docs/superpowers/specs/2026-09-17-execution-plane-unification-design.md
git commit -m "feat: l3_spatial 注册 st_genescore/st_metabolism（双契约测试+清单 18→20+SECTION_TITLES+附录 A 同步）"
```

---

### Task 4: ci.yml 两冒烟步 + 全门禁 + push

**Files:**
- Modify: `.github/workflows/ci.yml`（docker-smoke job，sc_cellfreq 步 L174-177 之后）

- [x] **Step 4.1: 加两冒烟步**

在 `sc_cellfreq 合成冒烟` 步之后（同 job 同缩进）追加：

```yaml
      - name: st_genescore 合成冒烟（空间网格+TGFb 注入真值回收+spatial 图落盘+三拒收断网端到端）
        env:
          BIO_WORKSPACE_ROOT: ${{ runner.temp }}/bio_ws
        run: python scripts/_smoke_st_genescore.py

      - name: st_metabolism 合成冒烟（空间网格+Glycolysis 注入 AUCell/mean 双口径+spatial 图落盘+拒收断网端到端）
        env:
          BIO_WORKSPACE_ROOT: ${{ runner.temp }}/bio_ws
        run: python scripts/_smoke_st_metabolism.py
```

- [x] **Step 4.2: 全门禁（三连）**

Run: `ruff check .`
Expected: 无输出

Run: `python -m mypy`
Expected: `Success: no issues found`（sandbox 在 mypy 包内，st 双工具过 strict；本批无平台专属符号，无需 linux 反模拟）

Run: `python -m pytest -m "not pg" -q`
Expected: 全 PASS 零回归（基线 + 新 8 用例：test_l3_st_genescore 4 + test_l3_st_metabolism 4）

- [x] **Step 4.3: Commit + push + CI 验收**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: docker-smoke 增 st_genescore/st_metabolism 两冒烟步（空间网格真值回收）"
git push
```

Expected: CI 四 job 全绿；docker-smoke 两个新步 step 级 success。若 CI 队列慢可用 `python bio_workspace/_eval/_ci_verify_<run_id>.py` 模式轮询（scripts/gh 鉴权先例）。

---

### Task 5: 工作项 A——genescore×cytosig 联读真机脚本（599sub）

**Files:**
- Create: `bio_workspace/_eval/real_genescore_cytosig_599sub.py`

**关键陷阱（已预判）:** 599sub_bbknn 为鼠源 Titlecase 符号，`sc_genescore` 的 `species_style_guard("human", ...)` 会在 load 后毫秒级拦截（title≠upper）。解法：宿主侧构建大写影子数据集 `599sub_gs_up`（var/raw 双层 upper、撞名保首），PROGENy 靶基因全大写、upper 对齐即标准大小写桥接；产物拷回 `599sub_bbknn/genescore/` 保证幂等复跑。

- [x] **Step 5.1: 写真机脚本 `bio_workspace/_eval/real_genescore_cytosig_599sub.py`**

```python
"""genescore×cytosig 联读真机验证（Phase 75 工作项 A）：599sub_bbknn。

spec 2026-09-20-phase75 §2：PROGENy 通路活性（MLM）× CytoSig beta 的
簇级 Spearman 联读；real_cytosig_599sub.py 骨架复用。

流程：
①宿主 marker 注释（Ptprc/Col1a1/...argmax，real_cytosig 同款）；
②影子桥：599sub 鼠源 Titlecase，sc_genescore human-only 卫兵会拦——
  宿主构建大写影子 599sub_gs_up（var/raw 双层 upper，撞名保首）；
③容器断网补跑 genescore（影子 → 产物拷回 599sub_bbknn/genescore/）；
④簇级对齐：scores 按 leiden 求均值（簇×14）vs cytosig beta pivot；
⑤三层联读：TGFb×TGFB 容差族逐对 / 14×43 全景 |rho|>=0.8 / CAF 叙事；
⑥产物 _eval/ 四件套 + C1/C2/C3 预注册判据（不过不阻塞，如实记录）。
"""
import json
import shutil
import subprocess
from pathlib import Path

import anndata as ad
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

WS = Path("bio_workspace").resolve()
EVAL = WS / "_eval"
IMG = "feishu-research-agent/bio:cpu-latest"
DS = "599sub_bbknn"
UP = "599sub_gs_up"  # 大写影子数据集（联读专用）
TGF_CORE = ["TGFB1", "TGFB2", "TGFB3"]
TGF_WIDE = TGF_CORE + ["Activin A", "BMP2", "BMP4"]  # 容差族（有则算）
CAF_MARKERS = ("Col1a1", "Dcn", "Acta2")

a0 = ad.read_h5ad(WS / DS / "processed.h5ad")

# ── ① 宿主侧 marker 注释（argmax 粗注） ───────────────────────────────
MARKERS = ["Ptprc", "Col1a1", "Dcn", "Epcam", "Krt18", "Lyz2",
           "Cd3e", "Pecam1", "Acta2"]
r0 = a0.raw.to_adata()
mk = [g for g in MARKERS if g in r0.var_names]
mm = pd.DataFrame(
    {g: np.asarray(r0[:, g].X.todense()).ravel() for g in mk},
    index=[str(c) for c in a0.obs_names])
mm["leiden"] = a0.obs["leiden"].astype(str).to_numpy()
gmean = mm.groupby("leiden").mean().round(2)
ident = gmean.idxmax(axis=1).to_dict()
print("== 各 leiden 簇 marker 均值（粗注：行 argmax） ==")
print(gmean.to_string())
print("粗注：", ident)

# ── ② 影子数据集（仅首次构建） ────────────────────────────────────────
if not (WS / UP / "processed.h5ad").exists():
    a = a0.copy()
    a.var_names = pd.Index([str(g).upper() for g in a.var_names])
    dup = a.var_names[a.var_names.duplicated()].unique().tolist()
    if dup:
        print(f"[影子] upper 撞名 {len(dup)} 个保首个: {dup[:5]}")
        a = a[:, ~a.var_names.duplicated()].copy()
    if a.raw is not None:
        rr = a.raw.to_adata()
        rr.var_names = pd.Index([str(g).upper() for g in rr.var_names])
        rr = rr[:, ~rr.var_names.duplicated()].copy()
        a.raw = rr
    (WS / UP).mkdir(parents=True, exist_ok=True)
    a.write_h5ad(WS / UP / "processed.h5ad")
    print(f"[影子] {UP}: {a.shape}, raw={a.raw is not None}")

# ── ③ 容器补跑 genescore（产物拷回原数据集，幂等） ────────────────────
scores_csv = WS / DS / "genescore" / "progeny_scores.csv"
if not scores_csv.exists():
    base = ["docker", "run", "--rm", "-i", "--network", "none",
            "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
            "-v", f"{str(Path('sandbox/sc_tools').resolve()).replace(chr(92), '/')}"
            ":/opt/sc_tools", IMG]
    p = subprocess.run(
        base + ["python", "/opt/sc_tools/genescore.py"],
        input=json.dumps({"dataset_id": UP, "groupby": "leiden",
                          "top_n": 14}),
        capture_output=True, text=True, timeout=1500)
    out = json.loads(p.stdout)
    assert out["ok"], out
    src = WS / Path(out["scores_csv"]).relative_to("/ws")
    scores_csv.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, scores_csv)
    print(f"[genescore] n_pathways={out['n_pathways_scored']}，"
          f"产物归位 {DS}/genescore/")

# ── ④ 簇级对齐 ────────────────────────────────────────────────────────
sc_df = pd.read_csv(scores_csv, index_col=0)  # 细胞×14，首列 leiden
P = sc_df.groupby("leiden").mean()  # 簇×14
cyto = pd.read_csv(WS / DS / "cytosig" / "cytosig_scores.csv")
B = cyto[cyto["kind"] == "beta"].pivot(
    index="sample", columns="factor", values="value")  # 簇×43
clusters = sorted(set(P.index) & set(B.index),
                  key=lambda c: (len(str(c)), str(c)))
P, B = P.loc[clusters], B.loc[clusters]
print(f"[对齐] 簇交集 {len(clusters)}: {clusters}")

# ── ⑤a 焦点对照：TGFb × TGF 容差族逐对 Spearman ───────────────────────
focus: dict[str, dict[str, float]] = {}
for f in TGF_WIDE:
    if f not in B.columns:
        continue
    rho, p = spearmanr(P["TGFb"], B[f])
    focus[f] = {"rho": round(float(rho), 4), "p": round(float(p), 4)}
    print(f"  TGFb × {f:<10} rho={rho:+.3f} p={p:.3f}")

# ── ⑤b 全景矩阵 14×43（常量列跳过防 nan 告警） ────────────────────────
rho_mat = pd.DataFrame(np.nan, index=list(P.columns), columns=list(B.columns))
p_mat = rho_mat.copy(deep=True)
for pw in P.columns:
    for f in B.columns:
        if P[pw].nunique() < 2 or B[f].nunique() < 2:
            continue
        rho, p = spearmanr(P[pw], B[f])
        rho_mat.loc[pw, f] = rho
        p_mat.loc[pw, f] = p
strong = [
    {"pathway": pw, "factor": f,
     "rho": round(float(rho_mat.loc[pw, f]), 4),
     "p": round(float(p_mat.loc[pw, f]), 4)}
    for pw in P.columns for f in B.columns
    if abs(float(rho_mat.loc[pw, f])) >= 0.8
]
print(f"[全景] |rho|>=0.8 对数: {len(strong)}")
for s in strong[:10]:
    print(f"  {s['pathway']} × {s['factor']}: "
          f"rho={s['rho']:+.3f} (p={s['p']:.3f})")

# ── ⑤c CAF 叙事：TGFb 域排名 + TGFB 家族 beta 并列 ───────────────────
caf = [c for c in clusters if ident.get(c) in CAF_MARKERS]
tgfb_rank = P["TGFb"].rank(ascending=False)
print(f"[CAF] 粗注命中簇: {[(c, ident[c]) for c in caf] or '无（C2 记 not-pass）'}")
for c in caf:
    fam = {f: float(B.loc[c, f]) for f in TGF_CORE if f in B.columns}
    print(f"[CAF {c}] TGFb 排名={int(tgfb_rank[c])}/{len(clusters)}  "
          + "  ".join(f"{k}={v:+.3f}" for k, v in fam.items()))

# ── ⑥ 散点图：TGFb × 最强 TGFB 家族成员 ───────────────────────────────
cand = [f for f in TGF_CORE if f in focus]
best_f = max(cand, key=lambda f: abs(focus[f]["rho"]))
x_, y_ = P["TGFb"], B[best_f]
fig, ax = plt.subplots(figsize=(4.5, 4))
ax.scatter(x_, y_, s=40)
for c in clusters:
    ax.annotate(str(c), (float(x_[c]), float(y_[c])), fontsize=8,
                xytext=(3, 3), textcoords="offset points")
k, b_ = np.polyfit(x_.to_numpy(dtype=float), y_.to_numpy(dtype=float), 1)
xs = np.linspace(float(x_.min()), float(x_.max()), 50)
ax.plot(xs, k * xs + b_, "--", lw=1)
rho, p = spearmanr(x_, y_)
ax.set_title(f"PROGENy TGFb × CytoSig {best_f}  "
             f"rho={rho:+.2f} p={p:.2f}", fontsize=9)
ax.set_xlabel("PROGENy TGFb（MLM 簇均值）")
ax.set_ylabel(f"CytoSig {best_f} beta")
fig.tight_layout()
fig.savefig(EVAL / "crossread_tgfb_scatter.png", dpi=150)
plt.close(fig)

# ── ⑦ 判据汇总（C1/C2/C3 预注册，不过不阻塞） ────────────────────────
c1_detail = [f"{f}: rho={focus[f]['rho']:+.3f}" for f in TGF_CORE
             if f in focus and focus[f]["rho"] > 0
             and abs(focus[f]["rho"]) >= 0.6]
summary = {
    "dataset": DS,
    "n_clusters": len(clusters),
    "shadow_dataset": UP,
    "criteria": {
        "C1_tgfb_family_rho_pos>=0.6": {
            "pass": bool(c1_detail), "detail": c1_detail},
        "C2_caf_tgfb_rank<=3": {
            "pass": any(int(tgfb_rank[c]) <= 3 for c in caf),
            "detail": {c: int(tgfb_rank[c]) for c in caf}},
        "C3_any_pair_|rho|>=0.8": {
            "pass": len(strong) >= 1, "n_strong": len(strong),
            "top": strong[:10]},
    },
    "focus_family": focus,
    "cluster_identity": ident,
    "note": "判据不过不阻塞收口（簇数少 rho 方差大），差异记录于测试总结 #20",
}
EVAL.mkdir(parents=True, exist_ok=True)
rho_mat.to_csv(EVAL / "crossread_pathway_factor_rho.csv")
p_mat.to_csv(EVAL / "crossread_pathway_factor_pval.csv")
(EVAL / "real_crossread_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary["criteria"], ensure_ascii=False, indent=2))
print("\nREAL CROSSREAD OK: 四件套落盘 _eval/，判据逐条记录如上")
```

- [x] **Step 5.2: 跑真机联读**

Run: `python bio_workspace/_eval/real_genescore_cytosig_599sub.py`
Expected: `REAL CROSSREAD OK`；重点核对 C1（TGFB 家族至少一员 rho≥0.6 且正向）/ C2（CAF 簇 TGFb 排名≤3）/ C3（≥1 对 |rho|≥0.8）逐条输出；判据不过不阻塞，把实际值抄入 Task 7 测试总结。

- [x] **Step 5.3: lint + Commit**

Run: `ruff check bio_workspace/_eval/real_genescore_cytosig_599sub.py`
Expected: 无输出

```bash
git add bio_workspace/_eval/real_genescore_cytosig_599sub.py
git commit -m "feat: genescore×cytosig 联读真机脚本（599sub 大写影子桥+簇级 Spearman+三判据）"
```

---

### Task 6: 工作项 B 真机验收——oscc 双工具（spec §4）

**Files:**
- Create: `bio_workspace/_eval/real_st_gm_oscc.py`

- [x] **Step 6.1: 写验收脚本 `bio_workspace/_eval/real_st_gm_oscc.py`**

```python
"""st_genescore / st_metabolism 真机验收（Phase 75 工作项 B）：oscc。

spec §4：groupby=spatial_domain、species=human 双工具跑 oscc——
①st_genescore：spot 级 14 通路 + 域均值热图 + 空间着色图；与 sc 版
  （cluster_annotations 口径）TGFb/JAK-STAT 排名方向对照（一致即
  通过，不一致如实记录进测试总结 #20）；
②st_metabolism：域级代谢热图 + top1 通路空间图。
产物断言齐 + 两口径对照打印 = 验收通过。
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


def run_tool(script: str, payload: dict) -> dict:
    """容器断网跑单个 st 工具，断言 ok 并返回 emit dict。"""
    p = subprocess.run(
        base + ["python", f"/opt/sc_tools/{script}"],
        input=json.dumps(payload), capture_output=True, text=True,
        timeout=1800)
    out = json.loads(p.stdout)
    assert out["ok"], out
    return out


def host(p: str) -> Path:
    """容器 /ws/<rel> → 宿主路径并断言存在。"""
    q = WS / Path(p).relative_to("/ws")
    assert q.exists(), p
    return q


# ① st_genescore（spatial_domain 口径）
o1 = run_tool("st_genescore.py",
              {"dataset_id": DS, "groupby": "spatial_domain", "top_n": 14})
print(f"[st_genescore] spots={o1['n_spots']} domains={o1['n_domains']} "
      f"pathways={o1['n_pathways_scored']}")
for v in o1["products"].values():
    if v:
        host(v)
gm = pd.read_csv(host(o1["products"]["group_mean_csv"]), index_col=0)
print("[st_genescore] 各域 top3 通路：")
for d, tops in o1["top_by_group"].items():
    print(f"  {d}: {tops}")

# 两口径对照：sc 版 groupby=cluster_annotations（Phase 72 产物）
sc_gm = pd.read_csv(WS / DS / "genescore" / "progeny_group_mean.csv",
                    index_col=0)
for pw in ("TGFb", "JAK-STAT"):
    st_top = gm[pw].idxmax() if pw in gm.columns else "?"
    sc_top = sc_gm[pw].idxmax() if pw in sc_gm.columns else "?"
    print(f"[对照] {pw}: st(spatial_domain) top={st_top} "
          f"vs sc(cluster_annotations) top={sc_top}")

# ② st_metabolism（AUCell 默认口径）
o2 = run_tool("st_metabolism.py",
              {"dataset_id": DS, "method": "aucell",
               "groupby": "spatial_domain", "species": "human"})
print(f"[st_metabolism] spots={o2['n_spots']} "
      f"pathways={o2['n_pathways_scored']} domains={o2['n_domains']}")
for v in o2["products"].values():
    if v:
        host(v)
print("[st_metabolism] 各域 top3 通路（截断 50 字）：")
for d, tops in o2["top_by_group"].items():
    print(f"  {d}: {[t[:50] for t in tops]}")

print("\nREAL OK: st_genescore/st_metabolism oscc 真机验收全绿"
      "（两口径对照结论记录于测试总结 #20）")
```

- [x] **Step 6.2: 跑真机验收**

Run: `python bio_workspace/_eval/real_st_gm_oscc.py`
Expected: `REAL OK`；记录：①st_genescore 域数/每域 top3；②TGFb、JAK-STAT 在 st（spatial_domain）vs sc（cluster_annotations）两口径的 top 域方向是否一致；③st_metabolism top 通路。

- [x] **Step 6.3: lint + Commit**

Run: `ruff check bio_workspace/_eval/real_st_gm_oscc.py`
Expected: 无输出

```bash
git add bio_workspace/_eval/real_st_gm_oscc.py
git commit -m "test: oscc 真机验收脚本（st 双工具 spatial_domain 口径+两口径对照）"
```

---

### Task 7: 测试总结 #20 回填 + 收口

**Files:**
- Modify: `测试总结+2026-09-19T00-12-54.md`
- Modify: 本计划文件（勾选全部 checkbox）

- [x] **Step 7.1: 回填测试总结表**

在 `测试总结+2026-09-19T00-12-54.md` 测试情况表追加 #20 行（编号顺延现状），格式对齐既有行——一行压缩五段：

```markdown
| 20 | Phase 75 落地：st_genescore/st_metabolism 双工具（契约 8 用例+冒烟 12 场景+CI 两步）+ genescore×cytosig 联读真机 | <全绿/见说明> | <按实际结果填写：①冒烟两脚本 6+6 场景（TGFb/Glycolysis 注入真值回收+spatial png 落盘+无 spatial/单域/缺列/低重叠拒收）；②契约 test_l3_st_* 8 passed+清单 18→20+附录 A/SECTION_TITLES 同步；③联读 599sub（大写影子桥绕 human 卫兵）：C1/C2/C3 实际值 <rho=?><rank=?></>；④oscc 真机：<spots/domains/top 通路/两口径对照结论>；⑤门禁 ruff/mypy/pytest <N> passed+CI run <id> 四 job 绿 |
```

同时在文末"后续建议/挂账"区补两条：
- 反卷积加权打分（Cell2Location q05 权重）仍挂账——需先在 oscc 跑 deconvolve（spec §6 非目标 1）；
- 联读如需复用为工具，待更多数据集验证后另立 Phase（spec §0 原则 4）。

- [x] **Step 7.2: 勾选本计划全部 checkbox + 最终 push**

```bash
git add "测试总结+2026-09-19T00-12-54.md" docs/superpowers/plans/2026-09-20-phase75-crossread-stgenescore-stmetabolism.md
git commit -m "docs: 测试总结 #20 回填+Phase 75 计划勾选收口"
git push
```

Expected: CI 全绿，Phase 75 收口。

---

## 自审清单（写计划时已核对）

1. **Spec 覆盖**：§2 工作项 A（Task 5：影子桥+三层联读+三判据+四件套）；§3.1 三点差异（Task 1/2 容器层）；§3.2/§3.3 产物与 emit（products 嵌套 dict，`_harvest` 递归兼容）；§3.4 ToolSpec 双工具+超时+枚举（Task 3）；§3.5 双契约文件+两冒烟步（Task 3/4）；§4 oscc 验收（Task 6）；§5 风险对策（域数<2 拒收→Task 1/2；簇少 rho 方差大→判据不阻塞，Task 5）——无缺口。
2. **无占位符**：所有代码步骤给全文；测试总结行属运行期数据，给出精确模板与填写字段（唯一例外，明示）。
3. **类型/命名一致性**：`_ST_GENESCORE_TIMEOUT`/`_ST_METABOLISM_TIMEOUT` 在 l3_spatial 常量、handler、ToolSpec、两测试文件四处一致；容器 emit 键 `products.scores_csv` 等与冒烟断言、测试 fixture 逐字对齐；`st_progeny_*`/`st_metabolism_*` 产物名与 spec §3.2/§3.3 逐字一致。
4. **守护测试联动**：Task 3 单 commit 覆盖 l3_spatial+section_digest+附录 A（test_report_digest/test_spec_timeout_table_guard 红线驱动）；新工具不声明 cpus/memory（None==None 过 test_l3_timeout_contract，st_integrate 同款）。
5. **已知陷阱内嵌**：599sub 鼠源 Titlecase→影子桥（Task 5）；numpy 双索引注入须 np.ix_（Task 2）；anndata 写盘拒绝重复 var_names→撞名保首（Task 5）。

## 执行交接

- **Subagent-Driven（推荐）**：每 Task 派新 subagent，两段式审查，Task 1-7 顺序执行（Task 3 内部步骤不可拆 commit）。
- **Inline**：executing-plans 批量执行，Task 4/5/6 三个真机/docker 步骤后设检查点。

