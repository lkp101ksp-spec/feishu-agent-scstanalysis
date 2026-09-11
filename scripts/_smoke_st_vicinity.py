"""st_vicinity 容器冒烟：合成恶性种子 BFS 分层真跑（断网）。

12×12 网格左半（x<6）is_malignant=True 作种子（72 个），coord_type
走 generic（delaunay——grid/n_neighs=6 是 Visium 六边网格语义，整数
meshgrid 上行为不定）。配套 deconv.h5ad：Tumor 组成随 x 递减、
T cells 随 x 递增（层×细胞型组成梯度断言用）。调用走 BioRunner 约定：
docker run --rm -i --network none -v <workspace>:/ws <img> python
/opt/st_tools/vicinity.py，stdin 传 args JSON。
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
