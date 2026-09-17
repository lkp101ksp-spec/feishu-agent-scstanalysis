"""st_trajectory 容器冒烟：表达梯度合成数据真跑 DPT（断网）。

12×12 网格 144 spot，60 基因中 g0..g9 沿 x 线性渐变（g0 左高=root
端，g1..g9 右高），builder 容器内跑 mini st_process（raw 快照/HVG/
scale/PCA/neighbors/leiden）造真 processed.h5ad。DPT root=g0 最高
spot（左端）→ pseudotime 应沿 x 单调恢复（Spearman>0.9）；obs 注入
合成 vicinity 层（右 4 列=tumor / 中 4 列=L1 / 左 4 列=distal，
有序 tumor→L1→distal）→ vicinity 模式 root=tumor 跑通 + ρ 符号=负
（pt 左低右高、层编码 tumor=0→distal=2 沿 x 递减）。DS_NOVIC 无
vicinity 列 → NO_VICINITY。梯度加在 log1p 后的 log 空间（教训十六）。
"""
import json
import os
import subprocess
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

# 宿主 workspace/镜像：env 覆写（CI 冒烟用，与 settings 同口径）
WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", "I:/飞书agent/bio_workspace"))
DS = "sttrajsmoke"
DS_NOVIC = "sttrajnovic"
IMG = os.environ.get("BIO_ST_IMAGE", "feishu-research-agent/bio:st-cpu-latest")
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
# 轨迹流形：g0 左端高（root 端），g1..g9 沿 x 渐强（log 空间加梯度）
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
    # 脚本级 fail() 也是 exit 1 + stdout JSON（BioRunner 同款口径）；
    # 仅当 stdout 无法解析为 JSON 才算执行崩溃
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
col_idx = pd.Index([f"s{i}" for i in range(144)])
xs, ys = np.meshgrid(np.arange(12), np.arange(12))
col = pd.Series(xs.ravel(), index=col_idx)  # coords[:,1]=xs=轨迹轴（builder 同口径）
rho_x, _ = spearmanr(df["dpt_pseudotime"], col)
assert rho_x > 0.9, f"pseudotime~x Spearman={rho_x:.3f} (<0.9)"
# 写回核验：processed.h5ad obs 有 dpt_pseudotime
back = ad.read_h5ad(ds_dir / "processed.h5ad")
assert "dpt_pseudotime" in back.obs, "write-back missing"

# vicinity 模式：root=tumor 层（右端）→ pt 自肿瘤向外递增，ρ(pt,
# 层编码 tumor→distal)>0。root=层内度中位 spot（细条层内 x 位置不定
# → 层内方差大、L1/tumor 可倒置），故断言方向+首尾层均值对比，
# 不断言全序/强阈值（ρ 实测 0.58，方向正确）
v = run_traj(DS, root_mode="vicinity", root_layer="tumor")
assert v["ok"], v
assert v["root_mode"] == "vicinity", v
assert v["spearman_rho"] > 0.4, f"rho={v['spearman_rho']}"
back_v = ad.read_h5ad(ds_dir / "processed.h5ad")
gm = back_v.obs.groupby("vicinity", observed=True)[
    "dpt_pseudotime"].mean()
assert gm["distal"] > gm["tumor"], gm.to_dict()

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
      "| distal>tumor layer mean",
      "| NO_VICINITY/INVALID_INPUT rejected")
