# Phase 21 批③（st_deconvolve 反卷积）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付批③ st_deconvolve——cell2location 细胞类型反卷积（双参考来源：同任务 sc 产物 dataset_ref / 白名单 h5ad 路径），真机 /research 串联场景出各细胞类型空间分布图。

**Architecture:** 复用批①②基建。镜像追加 torch CPU + cell2location；参考双来源在工具层分流（12 位 hex → workspace 数据集拼接 counts+leiden；路径 → 白名单挂载 + 注释列自动探测）；独立超时 st_deconvolve_timeout_sec（默认 3600s）。tiny scRNA 参考数据集与 tiny visium 同基因空间（190 基因、3 细胞类型对应 3 空间域），保证反卷积可验证。

**Tech Stack:** cell2location（PyPI，scvi-tools/pyro 架构）+ torch CPU wheel（清华 pytorch-wheels 镜像优先，失败回退默认 torch）。

## 关键设计决策

1. **参考双来源分流（spec §2.4 落地）**：工具层判定——`sc_ref` 匹配 12 位 hex → 场景 A（workspace 内 sc 数据集：读 filtered.h5ad 的 counts + processed.h5ad 的 obs["leiden"] 按 barcode 对齐拼接；无 processed → ST_REF_INVALID 提示先跑 sc_process）；否则视为路径 → 场景 B（resolve_data_path 白名单挂载 /data，X 校验非负整数否则试 adata.raw）。
2. **注释列**：场景 A 用 leiden（sc_process 固定输出列名）；场景 B `ref_label_col` 参数可指定，留空自动探测（celltype/cell_type/CellType/leiden/cluster），找不到 → ST_REF_INVALID 附 obs 列清单。细胞类型数 sane check 2~30（spec 风险表）。
3. **cell2location 用 counts**：参考与空间都用原始计数（sc filtered.h5ad / st filtered.h5ad——两链 qc 均不改值只过滤行）；官方建议剔除 MT 基因后宽松基因过滤（`cell2location.utils.filtering.filter_genes`）。
4. **API 版本兼容（批②教训：文档与实现常不符）**：训练参数 `use_gpu` vs `accelerator='cpu'`、`uns['mod']['factor_names']` 键名、obsm 丰度键——T1 容器内探测签名后再定稿调用，计划代码为基准、以实际报错为准修正。
5. **超参**：N_cells_per_location=8、detection_alpha=20（Visium 官方推荐起点）；spatial max_epochs 默认 30000（官方建议，小数据收敛早停），ref 训练 max_epochs=250。

## File Structure

- Modify: `sandbox/st.Dockerfile`（torch CPU + cell2location 层）
- Create: `sandbox/st_tools/deconvolve.py`（参考拼接 + 双模型训练 + 丰度空间图）
- Modify: `config/settings.py`（st_deconvolve_timeout_sec + env）
- Modify: `orchestrator/tools/builtin/l3_spatial.py`（注册 st_deconvolve，7→8 工具；签名加 deconv 超时注入）
- Modify: `orchestrator/app.py`（装配传超时）
- Create: `scripts/make_tiny_scrna.py`（tiny sc 参考数据）
- Modify: `scripts/smoke_st_chain.py`（+deconvolve 步）
- Test: `tests/unit/test_l3_spatial.py`（+2 用例）

---

### Task 1: st 镜像追加 torch CPU + cell2location + API 探测

**Files:**
- Modify: `sandbox/st.Dockerfile`

- [ ] **Step 1: Dockerfile 追加安装层（在现有 pip 层之后、useradd 之前）**

```dockerfile
# 批③：torch CPU wheel（清华 pytorch-wheels 镜像优先，失败回退默认源）
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -f https://mirrors.tuna.tsinghua.edu.cn/pytorch-wheels/cpu/ \
    torch --prefer-binary \
    || pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple torch
# cell2location（scvi-tools/pyro；numpy2 兼容 sed 同 commot 层后追加保险）
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple cell2location \
    && sed -i 's/np\.Inf\b/np.inf/g' \
        /usr/local/lib/python3.12/site-packages/cell2location/**/*.py 2>/dev/null; \
    python -c "import cell2location; print('cell2location', cell2location.__version__)"
```

（`-f` find-links 让 pip 优先取 +cpu wheel；tuna 目录若无匹配版本自动回退普通 torch。sed 的 glob 若 shell 展开为空不报错——`2>/dev/null;` 后用 import 验证兜底）

- [ ] **Step 2: 重建镜像**

Run: `docker build -t feishu-research-agent/bio:st-cpu-latest sandbox -f sandbox/st.Dockerfile`
Expected: 成功（cell2location 拉取 torch/pyro/scvi-tools；层耗时数分钟属正常）

- [ ] **Step 3: API 探测（写临时脚本容器内跑，结论记入文件注释）**

Run:
```
docker run --rm feishu-research-agent/bio:st-cpu-latest python -c "
import inspect, torch
from cell2location.models import RegressionModel, Cell2location
print('torch', torch.__version__, 'cuda', torch.cuda.is_available())
print('RM.setup:', inspect.signature(RegressionModel.setup_anndata))
print('RM.train:', inspect.signature(RegressionModel.train))
print('C2l.train:', inspect.signature(Cell2location.train))
"
```
确认三点并记录：① train 是 `use_gpu` 还是 `accelerator` 参数；② setup_anndata 支持 batch_key=None/labels_key；③ cell2location 版本号。
另跑 `docker run --rm ... python -c "from cell2location.utils.filtering import filter_genes; print('filter_genes ok')"`。

- [ ] **Step 4: Commit**

```bash
git add sandbox/st.Dockerfile
git commit -m "feat(phase21-b3): st 镜像追加 cell2location（torch CPU wheel + API 探测）"
```

---

### Task 2: st_tools/deconvolve.py 脚本

**Files:**
- Create: `sandbox/st_tools/deconvolve.py`

- [ ] **Step 1: 写完整脚本（train 调用参数以 Task 1 探测结论为准修正）**

```python
"""st_deconvolve：cell2location 细胞类型反卷积 → 每 spot 类型丰度 + 空间图。

stdin: {"dataset_id": "<st 数据集>", "sc_ref_dataset": "<sc 12hex>" 或
        "sc_ref_path": "/data/相对路径.h5ad", "ref_label_col": "",
        "max_epochs": 30000, "n_cells_per_location": 8, "detection_alpha": 20}
双参考来源（spec §2.4）：workspace 内 sc 产物（filtered counts + processed
leiden 按 barcode 对齐）或 /data 挂载的独立 h5ad（注释列可指定/自动探测）。
参考与空间均用原始 counts；官方建议剔除 MT 基因 + 宽松基因过滤。
产出：deconv.h5ad + 每细胞类型空间着色图（pngs）+ 丰度摘要。
"""
from __future__ import annotations

from common import emit, run


def _load_ref(args: dict):
    """双来源读取参考：返回 counts AnnData + obs['c2l_label'] 注释列。"""
    import anndata as ad
    from common import DATA_ROOT, WS_ROOT, fail

    label_col = str(args.get("ref_label_col") or "").strip()
    if args.get("sc_ref_dataset"):
        ds = WS_ROOT / args["sc_ref_dataset"]
        proc = ds / "processed.h5ad"
        if not proc.exists():
            fail("ST_REF_INVALID",
                 f"sc ref {args['sc_ref_dataset']} has no processed.h5ad; "
                 "run sc_process first (deconvolve uses its leiden labels)")
            raise SystemExit(1)
        counts_p = next((ds / n for n in ("filtered.h5ad", "raw.h5ad")
                         if (ds / n).exists()), None)
        if counts_p is None:
            fail("ST_REF_INVALID",
                 f"sc ref {args['sc_ref_dataset']} has no filtered/raw h5ad")
            raise SystemExit(1)
        ref = ad.read_h5ad(counts_p)
        proc_obs = ad.read_h5ad(proc, backed="r").obs
        common = ref.obs_names.intersection(proc_obs.index)
        if len(common) < ref.n_obs * 0.5:
            fail("ST_REF_INVALID",
                 "sc ref barcodes mismatch between counts and processed")
            raise SystemExit(1)
        label = proc_obs.loc[common, "leiden"].astype(str)
        ref = ref[common].copy()
        ref.obs["c2l_label"] = label.values
        return ref, "workspace sc dataset"
    ref = ad.read_h5ad(DATA_ROOT / args["sc_ref_path"])
    if ref.X is not None and (ref.X.min() < 0 if ref.n_vars else False):
        if ref.raw is None:
            fail("ST_REF_INVALID",
                 "ref X contains negatives (scaled?) and no raw counts")
            raise SystemExit(1)
        ref = ref.raw.to_adata()
    cands = ([label_col] if label_col else []) + [
        "celltype", "cell_type", "CellType", "leiden", "cluster"]
    for c in cands:
        if c in ref.obs.columns and ref.obs[c].nunique() >= 2:
            ref.obs["c2l_label"] = ref.obs[c].astype(str).values
            return ref, c
    fail("ST_REF_INVALID",
         f"ref h5ad has no usable label column; obs columns: "
         f"{list(ref.obs.columns)[:20]}; pass ref_label_col")
    raise SystemExit(1)


def main() -> None:
    from common import WS_ROOT, ensure_spatial, fail, read_args

    args = read_args()
    max_epochs = int(args.get("max_epochs", 30000))
    n_cells = float(args.get("n_cells_per_location", 8))
    d_alpha = float(args.get("detection_alpha", 20))

    import anndata as ad
    sp = ad.read_h5ad(WS_ROOT / args["dataset_id"] / "filtered.h5ad")
    ensure_spatial(sp)
    ref, label_src = _load_ref(args)

    import matplotlib.pyplot as plt
    import numpy as np
    import scanpy as sc
    import squidpy as sq
    from cell2location.models import Cell2location, RegressionModel
    from cell2location.utils.filtering import filter_genes

    n_types = ref.obs["c2l_label"].nunique()
    if not 2 <= n_types <= 30:
        fail("ST_REF_INVALID",
             f"ref label column has {n_types} types (expect 2~30)")
        raise SystemExit(1)

    # 基因交集 + 剔 MT（官方建议）+ 宽松基因过滤
    sp = sp[:, sp.var_names.isin(ref.var_names)].copy()
    ref = ref[:, ref.var_names.isin(sp.var_names)].copy()
    for a in (sp, ref):
        mt = a.var_names.str.upper().str.startswith("MT-")
        a.obsm["MT"] = np.asarray(a[:, mt.values].X.todense()) \
            if hasattr(a[:, mt.values].X, "todense") else \
            a[:, mt.values].X.toarray()
        a._inplace_subset_var(~mt.values)
    sel = filter_genes(ref, cell_count_cutoff=5, cell_percentage_cutoff2=0.03,
                       nonz_mean_cutoff=1.12)
    shared = ref.var_names[sel].intersection(sp.var_names)
    if len(shared) < 20:
        fail("ST_REF_INVALID",
             f"only {len(shared)} shared genes after filtering")
        raise SystemExit(1)
    ref = ref[:, shared].copy()
    sp = sp[:, shared].copy()

    # ① 参考签名（NB 回归）
    RegressionModel.setup_anndata(
        adata=ref, batch_key=None, labels_key="c2l_label")
    mod_r = RegressionModel(ref)
    mod_r.train(max_epochs=250, batch_size=2500, accelerator="cpu")
    ref = mod_r.export_posterior(
        ref, sample_kwargs={"num_samples": 1000, "batch_size": 2500,
                            "accelerator": "cpu"})
    inf_aver = ref.var[[f"means_est_inf_{c}"
                        for c in ref.uns["mod"]["factor_names"]]].T

    # ② 空间映射
    Cell2location.setup_anndata(adata=sp)
    mod_s = Cell2location(
        sp, cell_state_df=inf_aver,
        N_cells_per_location=n_cells, detection_alpha=d_alpha)
    mod_s.train(max_epochs=max_epochs, batch_size=None, accelerator="cpu")
    sp = mod_s.export_posterior(
        sp, sample_kwargs={"num_samples": 1000, "batch_size": sp.n_obs,
                           "accelerator": "cpu"})

    abund = sp.obsm["q05_cell_abundance_w_sf"].copy()
    cell_types = list(abund.columns)
    sp.obs[cell_types] = abund

    ds_dir = WS_ROOT / args["dataset_id"]
    pngs = []
    top_types = abund.mean(axis=0).sort_values(ascending=False).index[:6]
    for ct in top_types:
        ax = sq.pl.spatial_scatter(sp, color=ct, return_ax=True, cmap="magma")
        ax.figure.savefig(ds_dir / f"deconv_{ct}.png", dpi=150,
                          bbox_inches="tight")
        plt.close("all")
        pngs.append(f"/ws/{args['dataset_id']}/deconv_{ct}.png")

    sp.write_h5ad(ds_dir / "deconv.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "ref_source": label_src,
        "n_cell_types": int(n_types),
        "cell_types": cell_types,
        "mean_abundance": {ct: round(float(abund[ct].mean()), 3)
                           for ct in cell_types},
        "pngs": pngs,
    })


if __name__ == "__main__":
    run(main)
```

风险点（按批②经验以容器实测修正）：`train(accelerator="cpu")` 若 TypeError 换 `use_gpu=False`；`means_est_inf_` 列名前缀 / `factor_names` 键名以 `ref.uns['mod'].keys()` 实测为准；`export_posterior` sample_kwargs 的 accelerator 同理；MT obsm 存档若 h5ad 序列化报错可改存 ndarray copy 或去掉该存档（非核心产物）。

- [ ] **Step 2: 重建镜像 + 容器内错误路径验证（sc_ref 不存在）**

stdin `{"dataset_id": "smoke_st", "sc_ref_dataset": "deadbeefdead"}` → 预期 ST_REF_INVALID rc=1。

- [ ] **Step 3: Commit**

```bash
git add sandbox/st_tools/deconvolve.py
git commit -m "feat(phase21-b3): st_deconvolve 脚本（cell2location 双参考）"
```

---

### Task 3: settings 超时 + 注册 st_deconvolve（TDD）

**Files:**
- Modify: `config/settings.py`
- Modify: `orchestrator/tools/builtin/l3_spatial.py`、`orchestrator/app.py`
- Test: `tests/unit/test_l3_spatial.py`

- [ ] **Step 1: 失败测试（追加）**

```python
def test_register_eight_st_tools(reg):
    """批③后 st_* 共 8 工具。"""
    names = sorted(t.name for t in reg.list() if t.name.startswith("st_"))
    assert names == ["st_commot", "st_deconvolve", "st_domains", "st_load",
                     "st_markers", "st_plot", "st_process", "st_qc"]


def test_st_deconvolve_sc_ref_dataset_vs_path(runner, reg, monkeypatch,
                                              tmp_path):
    """sc_ref 双来源：12hex 走 workspace（无挂载）；路径走白名单挂载 /data。
    12hex 场景 run 传独立超时。"""
    h = runner  # 复用 fixture 的 runner（resolve_data_path monkeypatched）
    h.resolve_data_path = lambda p: ("I:/bio_test_data", "ref.h5ad", "x")
    reg.get("st_deconvolve").handler(
        dataset_ref="abc123456789", sc_ref="deadbeefdead",
        deconv_timeout=3600)
    args, kw = runner.run.call_args
    assert args[0] == "deconvolve"
    assert args[1]["sc_ref_dataset"] == "deadbeefdead"
    assert kw.get("mounts") is None and kw["timeout_sec"] == 3600

    reg.get("st_deconvolve").handler(
        dataset_ref="abc123456789", sc_ref="I:/bio_test_data/ref.h5ad",
        deconv_timeout=3600)
    args, kw = runner.run.call_args
    assert args[1]["sc_ref_path"] == "ref.h5ad"
    assert kw["mounts"] == [("I:/bio_test_data", "/data")]
```

（fixture 细节以文件内既有 mock 为准调整——runner.run 是 MagicMock，resolve_data_path 需 monkeypatch 返回三元组）

- [ ] **Step 2: 红灯确认后实现**

settings.py 类属性加 `st_deconvolve_timeout_sec: int = 3600`，`load_settings()` 内 env 读取（`BIO_ST_DECONVOLVE_TIMEOUT`，参照 bio_st_image 模式）。

l3_spatial.py：`register_l3_spatial` 签名加 `st_deconvolve_timeout: int = 3600`；handler：

```python
    def st_deconvolve(*, dataset_ref: str, sc_ref: str,
                      max_epochs: int = 30000,
                      n_cells_per_location: float = 8.0,
                      detection_alpha: float = 20.0,
                      ref_label_col: str = "",
                      deconv_timeout: int = 3600) -> dict:
        """cell2location 反卷积：sc_ref 为 sc 产物 dataset_ref（12hex）或
        白名单内参考 h5ad 路径。"""
        import re as _re
        try:
            if _re.fullmatch(r"[0-9a-f]{12}", sc_ref):
                args = {"dataset_id": dataset_ref,
                        "sc_ref_dataset": sc_ref, "ref_label_col": ref_label_col,
                        "max_epochs": max_epochs,
                        "n_cells_per_location": n_cells_per_location,
                        "detection_alpha": detection_alpha}
                mounts = None
            else:
                mount_root, rel, _ = runner.resolve_data_path(sc_ref)
                args = {"dataset_id": dataset_ref,
                        "sc_ref_path": rel, "ref_label_col": ref_label_col,
                        "max_epochs": max_epochs,
                        "n_cells_per_location": n_cells_per_location,
                        "detection_alpha": detection_alpha}
                mounts = [(mount_root, "/data")]
            out = runner.run("deconvolve", args, mounts=mounts,
                             image=st_image, script_dir=_ST_SCRIPT_DIR,
                             timeout_sec=deconv_timeout)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out
```

ToolSpec（description 写明双来源与前置条件；timeout_sec=3600 由注册传值覆盖）。app.py 装配处传 `st_deconvolve_timeout=settings.st_deconvolve_timeout_sec`。docstring 7→8 工具。

- [ ] **Step 3: 绿灯 + 回归（含 l3_singlecell）→ Commit**

```bash
git add config/settings.py orchestrator/tools/builtin/l3_spatial.py orchestrator/app.py tests/unit/test_l3_spatial.py
git commit -m "feat(phase21-b3): 注册 st_deconvolve（TDD，双参考分流+独立超时 3600s）"
```

---

### Task 4: make_tiny_scrna.py（sc 参考数据）

**Files:**
- Create: `scripts/make_tiny_scrna.py`

- [ ] **Step 1: 写生成脚本（与 tiny_visium 同基因空间）**

```python
"""生成 tiny scRNA 参考 h5ad（300 细胞 × 190 基因，3 细胞类型对应
tiny_visium 3 空间域；批③ cell2location 真机用）。

基因集与 make_tiny_visium 完全一致（MARKER_D1/D2/D3 各 20 + BG 120 +
LR 6 + MT 4）：CT_A/CT_B/CT_C 分别高表达 MARKER_D1/D2/D3，LR 基因分布
与对应空间域一致（CXCL12@CT_A、CXCR4@CT_B、VEGFA@CT_B、KDR@CT_C、
CSF1@CT_C、CSF1R@CT_A）——反卷积应还原 D0 域 CT_A 占优的空间格局。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else
               "bio_test_data/tiny_scrna.h5ad")
    n_marker, n_bg = 20, 120
    rng = np.random.default_rng(7)
    genes = ([f"MARKER_D{d+1}_{i}" for d in range(3) for i in range(n_marker)]
             + [f"BG_{i}" for i in range(n_bg)]
             + ["CXCL12", "CXCR4", "VEGFA", "KDR", "CSF1", "CSF1R"]
             + [f"MT-{i}" for i in range(1, 5)])
    n_cells, n_types = 300, 3
    ctype = np.repeat([f"CT_{chr(65 + t)}" for t in range(n_types)],
                      n_cells // n_types)
    X = rng.poisson(0.3, (n_cells, len(genes))).astype(np.float32)
    # 域特异 marker（类型 t 高表达 MARKER_D{t+1} 家族）
    for t in range(n_types):
        rows = ctype == f"CT_{chr(65 + t)}"
        cols = slice(t * n_marker, (t + 1) * n_marker)
        X[np.ix_(rows, np.arange(len(genes))[cols])] += rng.poisson(
            8.0, (rows.sum(), n_marker)).astype(np.float32)
    # LR 基因（与空间域一致的方向性）
    lr_map = {"CXCL12": 0, "CXCR4": 1, "VEGFA": 1, "KDR": 2,
              "CSF1": 2, "CSF1R": 0}
    for gene, t in lr_map.items():
        col = genes.index(gene)
        rows = ctype == f"CT_{chr(65 + t)}"
        X[rows, col] += rng.poisson(6.0, int(rows.sum())).astype(np.float32)

    import anndata as ad

    a = ad.AnnData(X=X,
                   obs={"celltype": ctype.astype(object)},
                   var=pd_index(genes))
    a.obs_names = [f"cell{i}" for i in range(n_cells)]
    a.var_names = genes
    a.write_h5ad(out)
    print(f"tiny scrna written: {out} ({n_cells} x {len(genes)}, "
          f"3 types {np.bincount(ctype == ctype)[0].tolist() if False else [100, 100, 100]})")


def pd_index(genes):
    class _Idx(list):
        name = None
    return _Idx(genes)


if __name__ == "__main__":
    main()
```

（`pd_index` 是占位示意——实现时直接构造 `ad.AnnData(X=X, obs=..., var=pd.DataFrame(index=genes))` 后再设 obs_names/var_names；以 h5ad 读回验证为准，print 的类型统计用 `pd.Series(ctype).value_counts().tolist()`）

- [ ] **Step 2: 生成 + 验证读回（X 整数非负、celltype 3 类、基因集与 visium 一致）**

Run: `.venv\Scripts\python.exe scripts\make_tiny_scrna.py I:\飞书agent\bio_test_data\tiny_scrna.h5ad`
验证脚本：读回对比 `set(var_names) == tiny_visium 基因集`、`X.min() >= 0`、celltype 计数 [100,100,100]。

- [ ] **Step 3: Commit**

```bash
git add scripts/make_tiny_scrna.py
git commit -m "feat(phase21-b3): tiny scRNA 参考生成器（190 基因同空间、3 类型）"
```

---

### Task 5: 冒烟扩展（deconvolve 场景 B 路径参考）+ 容器链 8 步

**Files:**
- Modify: `scripts/smoke_st_chain.py`

- [ ] **Step 1: commot 步后追加**

```python
    out = run_script("deconvolve",
                     {"dataset_id": ds, "sc_ref_path": "tiny_scrna.h5ad",
                      "ref_label_col": "celltype", "max_epochs": 2000},
                     data_mount)
    types = out.get("cell_types")
```

产物核验列表追加 `deconv.h5ad` 与 `f"deconv_{types[0]}.png"`（top 类型图）。
结尾 print 加 `n_types={len(types or [])}`。
（max_epochs=2000 为冒烟加速；tiny 196 spots CPU 应数分钟内）

- [ ] **Step 2: 清工作区跑冒烟**

Run:
```powershell
Remove-Item -Recurse -Force I:\飞书agent\bio_workspace\smoke_st -ErrorAction SilentlyContinue
.venv\Scripts\python.exe scripts\smoke_st_chain.py I:\飞书agent\bio_test_data\tiny_visium
```
Expected: 8 步全 ok；deconvolve n_cell_types=3、mean_abundance 三类型均有量级、CT 图非空白；总时长记录（真机超时评估依据）。
调试要点：训练报错按 Task 2 风险清单修；耗时 >15 分钟则 max_epochs 降 1000 重试。

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_st_chain.py sandbox/st_tools/deconvolve.py
git commit -m "feat(phase21-b3): 冒烟扩展 deconvolve（容器链 8 步场景 B 通过）"
```

---

### Task 6: 全量回归 + 真机双场景验收（用户配合）

- [ ] **Step 1: 全量回归** `.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp` → 全 passed
- [ ] **Step 2: 查杀双实例 + 单实例重启 ws_client**
- [ ] **Step 3: 用户真机串联场景（场景 A：同任务 sc 产物参考）**

```
/research 对 I:\飞书agent\bio_test_data\tiny_visium 做空间转录组与单细胞联合分析：读取空间数据、质控、空间域聚类，再读取单细胞参考 I:\飞书agent\bio_test_data\tiny_scrna.h5ad 做质控与聚类，最后用 cell2location 做细胞类型反卷积并展示各细胞类型空间分布
```
Expected: st_load→st_qc→st_process→sc_load→sc_qc→sc_process→st_deconvolve 全 success；planner 把 sc_process 输出的 dataset_ref 传入 st_deconvolve.sc_ref（场景 A 12hex 分流验证）；n_cell_types=3；CT_A/B/C 空间图回传 IM/文档；CT_A 占优区域与 MARKER_D1 域一致。
（若 planner 未串联 sc 链只跑 st_deconvolve——观察其 sc_ref 选择；prompt 层引导属 planner 范畴，工具层两场景均已冒烟）

- [ ] **Step 4: 更新测试总结 + 收尾 Commit**

```bash
git add 测试总结*.md
git commit -m "test(phase21-b3): 批③真机验收记录（Phase 21 全量收官）"
```

---

## Self-Review 结论

- **Spec 覆盖**：§2.4 st_deconvolve 双参考（T2/T3）、ST_REF_INVALID 含列名提示（T2）、独立超时（T3）、tiny 真机分钟级（T5）、§6 批③验收"反卷积比例空间图回传 + sc 串联场景"（T5/T6）。
- **偏离说明**：ref_label_col 参数为 spec 未细化的落地补充（场景 B 注释列探测）；make_tiny_scrna 为真机串联所需新增数据件（spec §5"sc 参考串联"隐含）。
- **类型一致性**：sc_ref 12hex 判定正则与 dataset_id 格式（sha1[:12]）一致；deconv_timeout 由 app.py 注入 handler → run(timeout_sec=)。
