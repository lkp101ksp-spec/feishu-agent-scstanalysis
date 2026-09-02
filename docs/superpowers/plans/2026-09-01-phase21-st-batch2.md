# Phase 21 批②（st_domains + st_commot）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付批②两工具——st_domains（banksy-lite 空间域细分 + ARI 对比）与 st_commot（COMMOT 配体受体空间通讯），真机 /research 链路出空间域对比图与通讯热图/方向图。

**Architecture:** 复用批①全部基建（BioRunner 参数化镜像、st 镜像、stdin/stdout JSON 契约、图片回传 pngs 通用键）。镜像只追加 `commot` pip 层；Banksy 不引入新依赖（见下方决策）。tiny Visium 数据追加 3 对真实 CellChat LR 基因使 COMMOT 有可检出通讯。

**Tech Stack:** squidpy 1.8.3 / scanpy 1.12.4（已装）、commot（PyPI 清华源）、CellChat 内置 LR 库、sklearn（scanpy 依赖已装，ARI 用）。

## 关键设计决策（含 spec 偏离说明）

1. **Banksy 实现偏离 spec**：spec §2.3 写"squidpy 内置 banksy 实现"——实际调研（2026-09-01 容器内验证）squidpy 1.8.3 无 `sq.gr.banksy`，PyPI 无 banksy 包（官方 Banksy_py 仅 GitHub pip 安装，受限网络不可靠）。**改为手写 Banksy-lite**：邻域均值特征拼接 `X_banksy = [X_pca, λ·A_norm@X_pca]`（λ=0.25）再 Leiden——BANKSY 论文均值项的核心近似，OSTA/BioC 教程认可做法，零新依赖。工具文档诚实标注 "banksy-lite（邻域均值特征增强）"。
2. **process.py 补 raw 快照**：COMMOT 需要非负 normalized log 表达，而 processed.h5ad 的 X 已 scale（含负值）不可用。在 normalize+log1p 后落 `adata.raw = adata`。顺带解决批① T7 遗留建议第 3 条（markers use_raw 对齐 sc 行为）。
3. **commot 图片走通用 `pngs` 键**：research_runner 已收集 `pngs` 列表（research_runner.py:387），无需改 research_runner。
4. **tiny 数据坐标单位**：make_tiny_visium 的 fullres 坐标 spot 间距 100（像素），COMMOT `dis_thr` 与 obsm["spatial"] 同单位 → 默认 200（2 spot 距离）与 spec §2.3 一致。

## File Structure

- Modify: `sandbox/st.Dockerfile`（requirements 层加 commot）
- Modify: `sandbox/st_tools/process.py`（raw 快照一行）
- Create: `sandbox/st_tools/domains.py`（banksy-lite/leiden 域细分 + ARI + 图）
- Create: `sandbox/st_tools/commot.py`（LR 通讯 + 3 类图 + top LR 对）
- Modify: `orchestrator/tools/builtin/l3_spatial.py`（注册 2 工具，5→7）
- Modify: `scripts/make_tiny_visium.py`（追加 3 对 LR 基因，184→190）
- Modify: `scripts/smoke_st_chain.py`（+domains/commot 两步 + 产物核验）
- Test: `tests/unit/test_l3_spatial.py`（+3 用例）
- 根目录测试总结文件追加批②记录（T8）

---

### Task 1: st 镜像追加 commot + 重建 + API 探测

**Files:**
- Modify: `sandbox/st.Dockerfile:7-10`

- [ ] **Step 1: requirements 层加 commot**

```dockerfile
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    numpy pandas scipy matplotlib h5py \
    anndata scanpy leidenalg igraph squidpy commot
```

（只改这一层，分层缓存友好——Dockerfile 第 3 行注释即为此预留）

- [ ] **Step 2: 重建镜像**

Run: `docker build -t feishu-research-agent/bio:st-cpu-latest sandbox -f sandbox/st.Dockerfile`
Expected: 一次成功（commot 及其依赖 POT 等走清华源）

- [ ] **Step 3: 容器内 API 探测（关键：确认 CellChat 库可用）**

Run:
```
docker run --rm feishu-research-agent/bio:st-cpu-latest python -c "import commot as ct; df = ct.pp.ligand_receptor_database(database='CellChat', species='human', signaling_type=None); print('LR pairs:', len(df)); print(df.columns.tolist()); print(df[df['ligand'].isin(['CXCL12','VEGFA','CSF1']) & df['receptor'].isin(['CXCR4','KDR','CSF1R'])])"
```
Expected: `LR pairs: ~1900+`；三行输出确认 CXCL12-CXCR4 / VEGFA-KDR / CSF1-CSF1R 三对存在（Task 6 造数据的依据；若某对缺失，Task 6 换用 df 中实际存在的对）

- [ ] **Step 4: 确认 spatial_communication 签名**

Run: `docker run --rm feishu-research-agent/bio:st-cpu-latest python -c "import commot as ct, inspect; print(inspect.signature(ct.tl.spatial_communication))"`
Expected: 含 `database_name, df_ligrec, dis_thr, heteromeric, pathway_sum` 参数

- [ ] **Step 5: Commit**

```bash
git add sandbox/st.Dockerfile
git commit -m "feat(phase21-b2): st 镜像追加 commot（CellChat 库容器内验证）"
```

---

### Task 2: process.py 落 raw 快照（COMMOT 前置）

**Files:**
- Modify: `sandbox/st_tools/process.py:28-32`

- [ ] **Step 1: 在 log1p 之后、HVG 之前插入 raw 快照**

```python
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    # raw 快照：normalized log 表达（非负）——COMMOT 等下游需非 scale 数据
    # （scale 后 X 含负值不可用；批① T7 遗留建议第 3 条一并解决）
    adata.raw = adata
    sc.pp.highly_variable_genes(adata, n_top_genes=min(2000, adata.n_vars),
                                flavor="seurat")
```

- [ ] **Step 2: 容器内重跑 process 验证（用 smoke 工作区已有数据）**

Run:
```
docker run --rm -i --network none --cpus 4 --memory 16g -v I:/飞书agent/bio_workspace/smoke_st:/ws -v I:/飞书agent/bio_test_data:/data:ro feishu-research-agent/bio:st-cpu-latest python /opt/st_tools/process.py
```
stdin: `{"dataset_id": "smoke_st", "resolution": 1.0}`
然后：
```
docker run --rm --network none -v I:/飞书agent/bio_workspace/smoke_st:/ws feishu-research-agent/bio:st-cpu-latest python -c "import anndata as ad; a = ad.read_h5ad('/ws/smoke_st/processed.h5ad'); print('raw:', a.raw is not None, a.raw.shape); print('X min (scale后可负):', a.X.min())"
```
Expected: `raw: True (196, 184)` + X min 为负数（证明 raw 与 scale X 确实不同）

- [ ] **Step 3: markers 回归不破坏（use_raw 语义未变，仅多存一层）**

Run（容器串行 markers）：
```
docker run --rm -i --network none --cpus 4 --memory 16g -v I:/飞书agent/bio_workspace/smoke_st:/ws feishu-research-agent/bio:st-cpu-latest python /opt/st_tools/markers.py
```
stdin: `{"dataset_id": "smoke_st", "top_n": 5}`
Expected: `{"ok": true, ...}`（markers 在 scale X 上跑，行为不变）

- [ ] **Step 4: Commit**

```bash
git add sandbox/st_tools/process.py
git commit -m "feat(phase21-b2): process 落 raw 快照（normalized log，COMMOT/下游用）"
```

---

### Task 3: domains.py 脚本（banksy-lite 空间域细分）

**Files:**
- Create: `sandbox/st_tools/domains.py`

- [ ] **Step 1: 写完整脚本**

```python
"""st_domains：空间域细分（banksy-lite / leiden）→ domains.h5ad + 对比图。

stdin: {"dataset_id": "...", "method": "banksy"|"leiden", "resolution": 1.0}
banksy-lite = 邻域均值特征拼接（BANKSY 论文均值项近似，λ=0.25）：
  X_banksy = [X_pca, λ · A_norm @ X_pca]，再标准 Leiden。
  （squidpy 1.8.3 无内置 banksy、PyPI 无该包，故手写均值项拼接——
  OSTA/BioC 教程认可的 Banksy 核心近似，零新依赖）
ARI = 与批① process 的 spatial_domain 的 adjusted_rand_score。
产出：banksy_domains.png（新域着色）+ compare_leiden.png（旧域对照）。
"""
from __future__ import annotations

from common import emit, run


def main() -> None:
    from common import WS_ROOT, ensure_spatial, fail, load_adata, read_args

    args = read_args()
    method = str(args.get("method", "banksy")).lower()
    resolution = float(args.get("resolution", 1.0))
    if method not in ("banksy", "leiden"):
        fail("INVALID_INPUT", f"method must be banksy|leiden, got {method!r}")
        return

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    ensure_spatial(adata)

    import matplotlib.pyplot as plt
    import numpy as np
    import scanpy as sc
    import squidpy as sq
    from sklearn.metrics import adjusted_rand_score

    n_neighbors = 15
    if method == "banksy":
        lam = 0.25
        # 行归一化空间邻接矩阵 → 邻域均值 PCA 特征
        A = adata.obsp["spatial_connectivities"].astype(np.float64)
        A = A.multiply(1.0 / np.maximum(A.sum(axis=1), 1e-9)).tocsr()
        neigh_mean = np.asarray(A @ adata.obsm["X_pca"])
        adata.obsm["X_banksy"] = np.hstack(
            [adata.obsm["X_pca"], lam * neigh_mean])
        use_rep = "X_banksy"
    else:
        use_rep = "X_pca"
    sc.pp.neighbors(adata, n_neighbors=n_neighbors, use_rep=use_rep)
    sc.tl.leiden(adata, resolution=resolution, key_added="banksy_domain")

    ari = float(adjusted_rand_score(
        adata.obs["spatial_domain"].astype(str).values,
        adata.obs["banksy_domain"].astype(str).values))
    sizes = adata.obs["banksy_domain"].value_counts().to_dict()

    ds_dir = WS_ROOT / args["dataset_id"]
    # squidpy>=1.8 spatial_scatter 需 return_ax=True（批① T7 教训）
    ax = sq.pl.spatial_scatter(adata, color="banksy_domain", return_ax=True)
    ax.figure.savefig(ds_dir / "banksy_domains.png", dpi=150,
                      bbox_inches="tight")
    plt.close("all")
    ax = sq.pl.spatial_scatter(adata, color="spatial_domain", return_ax=True)
    ax.figure.savefig(ds_dir / "compare_leiden.png", dpi=150,
                      bbox_inches="tight")
    plt.close("all")

    adata.write_h5ad(ds_dir / "domains.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": method,
        "n_domains": int(len(sizes)),
        "cluster_sizes": {str(k): int(v) for k, v in sizes.items()},
        "ari_vs_leiden": round(ari, 4),
        "spatial_png": f"/ws/{args['dataset_id']}/banksy_domains.png",
        "pngs": [f"/ws/{args['dataset_id']}/compare_leiden.png"],
    })


if __name__ == "__main__":
    run(main)
```

- [ ] **Step 2: 容器内验证（smoke 工作区已有 processed）**

Run:
```
docker run --rm -i --network none --cpus 4 --memory 16g -v I:/飞书agent/bio_workspace/smoke_st:/ws feishu-research-agent/bio:st-cpu-latest python /opt/st_tools/domains.py
```
stdin: `{"dataset_id": "smoke_st", "method": "banksy"}`
Expected: `{"ok": true, "method": "banksy", "n_domains": 3, "ari_vs_leiden": ~0.8+, ...}`（tiny 数据 3 域结构清晰，ARI 应高；若 n_domains≠3 调 resolution 0.5~1.5 试）
再验证 method=leiden 一轮 + 宿主核验 `banksy_domains.png`/`compare_leiden.png`/`domains.h5ad` 非空。

- [ ] **Step 3: Commit**

```bash
git add sandbox/st_tools/domains.py
git commit -m "feat(phase21-b2): st_domains 脚本（banksy-lite 邻域均值特征 + ARI 对比）"
```

---

### Task 4: commot.py 脚本（COMMOT 配体受体通讯）

**Files:**
- Create: `sandbox/st_tools/commot.py`

- [ ] **Step 1: 写完整脚本**

```python
"""st_commot：配体受体空间通讯（COMMOT + CellChat 库）→ 图 + top LR 对。

stdin: {"dataset_id": "...", "species": "human"|"mouse", "dis_thr": 200}
- 表达取 process 落的 adata.raw 快照（normalized log 非负；scale 后的
  processed.X 含负值不可用）
- dis_thr 单位与 obsm["spatial"] 坐标一致（visium fullres 像素，
  spot 中心距 100 → 默认 200 = 2 spot 距离，spec §2.3）
- 无匹配 LR 对 → fail ST_COMMOT_EMPTY（附物种/基因命名提示）
产出：top 通路 sender/receiver 方向图 + domain×pathway 热图（走 pngs）。
"""
from __future__ import annotations

from common import emit, run


def main() -> None:
    from common import WS_ROOT, ensure_spatial, fail, read_args

    args = read_args()
    species = str(args.get("species", "human")).lower()
    dis_thr = float(args.get("dis_thr", 200))
    if species not in ("human", "mouse"):
        fail("INVALID_INPUT", f"species must be human|mouse, got {species!r}")
        return

    import anndata as ad

    adata = ad.read_h5ad(WS_ROOT / args["dataset_id"] / "processed.h5ad")
    ensure_spatial(adata)
    if adata.raw is None:
        fail("ST_STATE_INVALID",
             "processed.h5ad has no raw snapshot; rerun st_process first")
        return
    # raw 快照 + 空间信息重建 COMMOT 输入（raw.to_adata 不带 obsm）
    expr = adata.raw.to_adata()
    expr.obsm["spatial"] = adata.obsm["spatial"].copy()
    expr.uns["spatial"] = adata.uns.get("spatial", {})
    if "spatial_domain" in adata.obs:
        expr.obs["spatial_domain"] = \
            adata.obs["spatial_domain"].astype(str).values

    import commot as ct
    import matplotlib.pyplot as plt
    import numpy as np

    df = ct.pp.ligand_receptor_database(
        database="CellChat", species=species, signaling_type=None)
    df_f = ct.pp.filter_lr_database(df, expr, min_cell_pct=0.05)
    if len(df_f) == 0:
        fail("ST_COMMOT_EMPTY",
             f"no CellChat LR pair matched (species={species}); check gene "
             "naming (human uppercase symbols, e.g. VEGFA/KDR) and "
             f"expression level; dis_thr={dis_thr}")
        return

    ct.tl.spatial_communication(
        expr, database_name="cellchat", df_ligrec=df_f,
        dis_thr=dis_thr, heteromeric=True, pathway_sum=True)

    # top LR 对（transport 矩阵总量）与 top pathway（sender 总量）
    scores = []
    for key in list(expr.obsp.keys()):
        if not key.startswith("commot-cellchat-") or key.count("-") < 3:
            continue
        lr = key[len("commot-cellchat-"):]
        lig, _, rec = lr.rpartition("-")
        scores.append((lig, rec, float(expr.obsp[key].sum())))
    scores.sort(key=lambda t: -t[2])
    top_lr = [{"ligand": l, "receptor": r, "score": round(s, 4)}
              for l, r, s in scores[:10]]
    df_sender = expr.uns["commot-cellchat-sum-sender"]
    top_path = str(df_sender.sum(axis=0).idxmax())

    ds_dir = WS_ROOT / args["dataset_id"]
    pngs = []
    # top 通路方向图（sender / receiver）
    ct.tl.communication_direction(
        expr, database_name="cellchat", pathway_name=top_path, k=5)
    for summary in ("sender", "receiver"):
        ct.pl.plot_cell_communication(
            expr, database_name="cellchat", pathway_name=top_path,
            plot_method="grid", summary=summary, background="summary",
            clustering="spatial_domain" if "spatial_domain" in expr.obs
            else None,
            ndsize=8, grid_density=0.4, scale=0.00003,
            normalize_v=True, normalize_v_quantile=0.995)
        p = ds_dir / f"commot_{top_path}_{summary}.png"
        plt.savefig(p, dpi=150, bbox_inches="tight")
        plt.close("all")
        pngs.append(f"/ws/{args['dataset_id']}/{p.name}")

    # domain × pathway 平均通讯强度热图（sender/receiver）
    if "spatial_domain" in expr.obs:
        grp = expr.obs["spatial_domain"].values
        top_paths = df_sender.sum(axis=0).sort_values(
            ascending=False).index[:12]
        send = df_sender.groupby(grp).mean()[top_paths]
        recv = expr.uns["commot-cellchat-sum-receiver"].groupby(
            grp).mean()[top_paths]
        mat = np.vstack([send.values, recv.values])
        fig, ax = plt.subplots(figsize=(1 + 0.5 * len(top_paths), 4))
        im = ax.imshow(mat, aspect="auto", cmap="viridis")
        ax.set_yticks(range(mat.shape[0]))
        ax.set_yticklabels(
            [f"{d}-send" for d in send.index]
            + [f"{d}-recv" for d in recv.index], fontsize=8)
        ax.set_xticks(range(len(top_paths)))
        ax.set_xticklabels(top_paths, rotation=45, ha="right", fontsize=8)
        fig.colorbar(im, shrink=0.7)
        fig.tight_layout()
        fig.savefig(ds_dir / "commot_heatmap.png", dpi=150)
        plt.close(fig)
        pngs.append(f"/ws/{args['dataset_id']}/commot_heatmap.png")

    expr.write_h5ad(ds_dir / "commot.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "species": species,
        "dis_thr": dis_thr,
        "n_lr_pairs": int(len(df_f)),
        "top_pathway": top_path,
        "top_lr_pairs": top_lr,
        "pngs": pngs,
    })


if __name__ == "__main__":
    run(main)
```

注意：此时 tiny 数据还没有 LR 基因（Task 6 才加），本步验证用 smoke 工作区旧数据**预期 ST_COMMOT_EMPTY**（错误路径验证），空数据错误链先跑通。

- [ ] **Step 2: 容器内验证错误路径（旧数据无 LR 基因）**

Run:
```
docker run --rm -i --network none --cpus 4 --memory 16g -v I:/飞书agent/bio_workspace/smoke_st:/ws feishu-research-agent/bio:st-cpu-latest python /opt/st_tools/commot.py
```
stdin: `{"dataset_id": "smoke_st", "species": "human"}`
Expected: `{"ok": false, "error_code": "ST_COMMOT_EMPTY", ...}`（rc=1，BioRunner 透传链路已有单测覆盖）
若报 SCRIPT_ERROR，按 traceback 修 API 调用（COMMOT 版本差异风险点：`sum-sender` 键名 / plot 参数）。

- [ ] **Step 3: Commit**

```bash
git add sandbox/st_tools/commot.py
git commit -m "feat(phase21-b2): st_commot 脚本（COMMOT+CellChat，方向图+热图+top LR）"
```

---

### Task 5: l3_spatial.py 注册 2 工具 + 单测（TDD）

**Files:**
- Modify: `orchestrator/tools/builtin/l3_spatial.py`
- Test: `tests/unit/test_l3_spatial.py`

- [ ] **Step 1: 写失败测试（追加到 test_l3_spatial.py）**

```python
def test_register_seven_st_tools(reg):
    """批②后 st_* 共 7 工具（5 基础 + domains + commot）。"""
    names = [t.name for t in reg.list_tools() if t.name.startswith("st_")]
    assert sorted(names) == [
        "st_commot", "st_domains", "st_load", "st_markers",
        "st_plot", "st_process", "st_qc"]


def test_st_domains_forwards_params(runner, reg):
    """method/resolution 透传 + st 镜像 + /opt/st_tools。"""
    reg.get("st_domains").handler(dataset_ref="abc123",
                                  method="banksy", resolution=0.8)
    args, kw = runner.run.call_args
    assert args[0] == "domains"
    assert args[1] == {"dataset_id": "abc123", "method": "banksy",
                       "resolution": 0.8}
    assert kw["image"].endswith("st-cpu-latest")
    assert kw["script_dir"] == "/opt/st_tools"


def test_st_commot_forwards_params(runner, reg):
    """species/dis_thr 透传（默认 human/200）。"""
    reg.get("st_commot").handler(dataset_ref="abc123")
    args, kw = runner.run.call_args
    assert args[0] == "commot"
    assert args[1]["species"] == "human"
    assert args[1]["dis_thr"] == 200
    assert kw["script_dir"] == "/opt/st_tools"
```

（fixture `runner`/`reg` 已在批①建立；`reg.list_tools()` 若无此方法，改用现有 5 工具测试同款遍历方式——参照文件内既有 `test_register_five_sc_tools` 模式）

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_spatial.py -q --basetemp=.pytest_tmp`
Expected: 3 failed（st_domains/st_commot 未注册）

- [ ] **Step 3: 实现（l3_spatial.py 追加）**

在 `st_plot` handler 之后、`registry.register` 块之前追加两个 handler：

```python
    def st_domains(*, dataset_ref: str, method: str = "banksy",
                   resolution: float = 1.0) -> dict:
        """空间域细分（banksy-lite 邻域均值特征 / leiden）→ 新域 + ARI 对比。"""
        try:
            out = runner.run(
                "domains", {
                    "dataset_id": dataset_ref, "method": method,
                    "resolution": resolution,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_commot(*, dataset_ref: str, species: str = "human",
                  dis_thr: float = 200.0) -> dict:
        """配体受体空间通讯（COMMOT + CellChat 库）→ 通讯图 + top LR 对。"""
        try:
            out = runner.run(
                "commot", {
                    "dataset_id": dataset_ref, "species": species,
                    "dis_thr": dis_thr,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out
```

文件末尾追加两个 ToolSpec（照现有 5 工具模式）：

```python
    registry.register(ToolSpec(
        name="st_domains",
        description=(
            "空间域细分：banksy 方法（邻域均值特征增强的空间感知 Leiden，"
            "Banksy-lite）或 leiden 重聚类，输出新空间域着色图、与既有 "
            "leiden 域的 ARI 一致性与对比图。需先跑 st_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "method": {"type": "string", "enum": ["banksy", "leiden"],
                           "default": "banksy"},
                "resolution": {"type": "number", "default": 1.0},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_domains,
        timeout_sec=1200,
    ))
    registry.register(ToolSpec(
        name="st_commot",
        description=(
            "配体受体空间通讯分析（COMMOT 最优传输 + CellChat 库）：输出 "
            "top 通讯通路的方向图（sender/receiver）、空间域×通路通讯强度"
            "热图与 top 配体受体对。species 选 human/mouse；dis_thr 通讯"
            "距离阈值（单位同空间坐标，visium 默认 200）。需先跑 st_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "species": {"type": "string", "enum": ["human", "mouse"],
                            "default": "human"},
                "dis_thr": {"type": "number", "default": 200},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_commot,
        timeout_sec=1800,
    ))
```

模块 docstring 的"5 个"改"7 个"；`register_l3_spatial` docstring 同步。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_spatial.py tests/unit/test_l3_singlecell.py -q --basetemp=.pytest_tmp`
Expected: 全 passed（含批①既有用例无回归）

- [ ] **Step 5: Commit**

```bash
git add orchestrator/tools/builtin/l3_spatial.py tests/unit/test_l3_spatial.py
git commit -m "feat(phase21-b2): 注册 st_domains/st_commot（TDD，7 工具）"
```

---

### Task 6: make_tiny_visium.py 追加 3 对 CellChat LR 基因

**Files:**
- Modify: `scripts/make_tiny_visium.py:28-42`

- [ ] **Step 1: 追加 LR 基因（环形三域通讯：D1→D2、D2→D3、D3→D1）**

在 MT 基因追加逻辑（第 39-42 行）之前插入：

```python
    # 3 对 CellChat human LR 基因（批② COMMOT 用；环形方向性通讯：
    # 配体高表达域 → 受体高表达域：D1→D2、D2→D3、D3→D1）
    lr_pairs = [("CXCL12", 0, "CXCR4", 1),
                ("VEGFA", 1, "KDR", 2),
                ("CSF1", 2, "CSF1R", 0)]
    lig_cols, rec_cols = [], []
    for lig, ld, rec, rd in lr_pairs:
        lig_cols.append((len(genes), ld))
        genes.append(lig)
        rec_cols.append((len(genes), rd))
        genes.append(rec)
    lr_mat = rng.poisson(0.3, (len(spots), len(lr_pairs) * 2)).astype(np.float32)
    for col, dom in lig_cols + rec_cols:
        lr_mat[domains == dom, col] += rng.poisson(
            6.0, ((domains == dom)).sum()).astype(np.float32)
    X = np.hstack([X, lr_mat])
```

（放在背景基因 X 构造完之后、MT hstack 之前——`genes`/`X` 追加方式与 MT 一致；`n_genes` 输出取自 `len(genes)` 自动变 190）

- [ ] **Step 2: 重新生成（务必先删旧目录——目录聚合 hash 混新旧会得到脏数据）**

Run:
```powershell
Remove-Item -Recurse -Force I:\飞书agent\bio_test_data\tiny_visium
.venv\Scripts\python.exe scripts\make_tiny_visium.py I:\飞书agent\bio_test_data\tiny_visium
```
Expected: `tiny visium written: ... (196 spots x 190 genes, 3 domains [70, 70, 56])`

- [ ] **Step 3: 验证 h5 读回**

Run: `.venv\Scripts\python.exe -c "import h5py; f = h5py.File('I:/飞书agent/bio_test_data/tiny_visium/filtered_feature_bc_matrix.h5'); print(f['matrix/shape'][()])"`
Expected: `[190 196]`（genes×spots）

- [ ] **Step 4: Commit**

```bash
git add scripts/make_tiny_visium.py
git commit -m "feat(phase21-b2): tiny visium 追加 3 对 CellChat LR 基因（190 基因）"
```

---

### Task 7: smoke_st_chain.py 扩展 + 容器冒烟

**Files:**
- Modify: `scripts/smoke_st_chain.py:50-66`

- [ ] **Step 1: 追加两步与产物核验**

`plot` 步之后追加：

```python
    out = run_script("domains", {"dataset_id": ds, "method": "banksy"},
                     data_mount)
    ari = out.get("ari_vs_leiden")
    out = run_script("commot", {"dataset_id": ds, "species": "human",
                                "dis_thr": 200}, data_mount)
    top_path = out.get("top_pathway")
    n_lr = out.get("n_lr_pairs")
```

产物核验列表替换为：

```python
    for name in ("raw.h5ad", "filtered.h5ad", "processed.h5ad",
                 "umap.png", "spatial_domains.png", "dotplot.png",
                 "MARKER_D1_0_spatial.png", "spatial_domain_spatial.png",
                 "domains.h5ad", "banksy_domains.png", "compare_leiden.png",
                 "commot.h5ad", "commot_heatmap.png",
                 f"commot_{top_path}_sender.png",
                 f"commot_{top_path}_receiver.png"):
```

结尾 print 改：
```python
    print(f"SMOKE PASS (n_domains={domains}, ari={ari}, "
          f"top_path={top_path}, n_lr={n_lr})")
```

- [ ] **Step 2: 清旧工作区跑冒烟**

Run:
```powershell
Remove-Item -Recurse -Force I:\飞书agent\bio_workspace\smoke_st -ErrorAction SilentlyContinue
.venv\Scripts\python.exe scripts\smoke_st_chain.py I:\飞书agent\bio_test_data\tiny_visium
```
Expected: 7 步全 ok + 产物核验 15/15 + `SMOKE PASS`；commot 的 `n_lr_pairs >= 3`、top_lr_pairs 含 CXCL12/VEGFA/CSF1 家族
风险点（真机可能要调）：grid 方向图 `scale=0.00003` 对 tiny 数据流量偏小可能近乎空白 → 调大 scale 或 `plot_method="cell"`；`commot_heatmap.png` 的 y 轴 6 行（3 域 × send/recv）。

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_st_chain.py sandbox/st_tools/
git commit -m "feat(phase21-b2): 冒烟扩展 domains/commot（容器链 7 步真机通过）"
```

---

### Task 8: 全量回归 + 真机 /research 验收

**Files:**
- 根目录测试总结文件追加批②记录

- [ ] **Step 1: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp`
Expected: 全 passed（799 + 批②新增 ≥3；无回归）

- [ ] **Step 2: 重启 ws_client（先查杀双实例——已两次复发）**

Run:
```powershell
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like '*ws_client*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
再后台启动：`.venv\Scripts\python.exe -m gateway.ws_client`
Expected: 日志出现 `Phase 21 st_* tools registered`（7 工具）+ Lark connected

- [ ] **Step 3: 用户真机验收（卡片确认写回）**

用户发送：
```
/research 对 I:\飞书agent\bio_test_data\tiny_visium 做空间转录组深度分析：读取数据、质控、空间域聚类、域标记基因后，用 banksy 方法细分空间域并与 leiden 对比，再做人类配体受体空间通讯分析
```
Expected: 全节点 success；n_domains/ari/top_pathway/n_lr_pairs/top_lr_pairs 关键输出回传；IM 收到 banksy 域图 + commot 方向图/热图；文档写回含全部插图

- [ ] **Step 4: 更新测试总结（含与计划的差异、后续建议）+ Commit**

```bash
git add 测试总结_*.md
git commit -m "test(phase21-b2): 批②真机验收记录（domains+commot 全通过）"
```

---

## Self-Review 结论

- **Spec 覆盖**：§2.3 st_domains（T3/T5）+ st_commot（T4/T5）；错误码 ST_COMMOT_EMPTY（T4）；§6 批②验收标准"空间域对比图 + 通讯热图/空间图回传"（T7/T8）；镜像 requirements 层（T1，spec §2.1 注释预留）。
- **偏离说明**：Banksy 由"squidpy 内置"改手写 lite（顶部决策 1，已给理由）；st_domains 输出用 `spatial_png`+`pngs` 键而非 spec 未细化的键名（与批①链路一致）。
- **类型一致性**：脚本输入 `dataset_id`（容器内）vs 工具层 `dataset_ref`（handler 参数名）与批①一致；`fail()` 后 `return` 防 fallthrough；`load_adata(file="processed")` 已有。
