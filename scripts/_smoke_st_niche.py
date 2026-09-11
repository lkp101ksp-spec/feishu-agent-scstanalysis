"""st_niche 容器冒烟：合成三区组成数据真跑（断网）。

12×12 网格按 x 分三区：左（x<4）T cells 主导、中（4≤x<8）Tumor/Fibro
混合、右（x≥8）Tumor 主导。deconv.h5ad 直接构造 obsm 组成矩阵（模拟
st_deconvolve 产物结构），processed.h5ad 供坐标。调用走 BioRunner 约定：
docker run --rm -i --network none -v <workspace>:/ws <img> python
/opt/st_tools/niche.py，stdin 传 args JSON。k=3 应恢复三区结构。
"""
import json
import subprocess
from pathlib import Path

WS = Path("I:/飞书agent/bio_workspace")
DS = "stnichesmoke"
DS_NODEC = "stnichenodec"
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
abund = np.zeros((n, 3), dtype=np.float32)
abund[x < 4] = [8.0, 0.2, 0.5]               # 左：T cells 主导
abund[(x >= 4) & (x < 8)] = [0.5, 4.0, 4.0]  # 中：Tumor/Fibro 混合
abund[x >= 8] = [0.2, 8.0, 0.5]              # 右：Tumor 主导
cols = ["q05cell_abundance_w_sf_T cells", "q05cell_abundance_w_sf_Tumor",
        "q05cell_abundance_w_sf_Fibroblast"]
barcodes = [f"s{i}" for i in range(n)]
dec = ad.AnnData(X=np.zeros((n, 3), dtype=np.float32),
                 obs=pd.DataFrame(index=barcodes),
                 var=pd.DataFrame(index=["g0", "g1", "g2"]))
dec.obsm["q05_cell_abundance_w_sf"] = pd.DataFrame(
    abund, index=barcodes, columns=cols)
dec.write_h5ad("/ws/stnichesmoke/deconv.h5ad")
proc = ad.AnnData(X=np.zeros((n, 10), dtype=np.float32),
                  obs=pd.DataFrame(index=barcodes),
                  var=pd.DataFrame(index=[f"g{i}" for i in range(10)]))
proc.obsm["spatial"] = coords
proc.write_h5ad("/ws/stnichesmoke/processed.h5ad")
# 无 deconv 数据集（ST_NICHE_NO_DECONV 路径）
proc.write_h5ad("/ws/stnichenodec/processed.h5ad")
print("built", abund.shape)
'''


def build_dataset() -> None:
    """容器内构造合成数据集（含无 deconv 对照）。"""
    bdir = WS / "_builder_stniche"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "build_data.py").write_text(_BUILDER, encoding="utf-8")
    (WS / DS).mkdir(parents=True, exist_ok=True)
    (WS / DS_NODEC).mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", "none",
         "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
         IMG, "python", "/ws/_builder_stniche/build_data.py"],
        capture_output=True, timeout=600)
    assert out.returncode == 0, out.stderr.decode()[-2000:]


def run_niche(ds: str, **kw):
    """容器内跑 niche.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": ds, **kw})
    r = subprocess.run(BASE + ["python", "/opt/st_tools/niche.py"],
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

o = run_niche(DS, k=3)
assert o["ok"], o
assert o["n_niches"] == 3, o
assert sorted(o["niche_sizes"].values()) == [48, 48, 48], o["niche_sizes"]
doms = set(o["dominant_by_niche"].values())
assert doms == {"T cells", "Tumor"}, o["dominant_by_niche"]  # 混合带 Tumor/Fibro 并列取其一
assert len(o["pngs"]) == 2
ds_dir = WS / DS
for f in ("niche/niche_spatial.png", "niche/niche_composition_heatmap.png",
          "niche/niche_composition.csv"):
    assert (ds_dir / f).exists(), f

# niche 标签与三区对应（容器内回读：每区内标签唯一、跨区互异）
check = subprocess.run(
    ["docker", "run", "--rm", "--network", "none",
     "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG,
     "python", "-c",
     "import anndata as ad; a = ad.read_h5ad('/ws/stnichesmoke/processed.h5ad');"
     "x = a.obsm['spatial'][:, 0]; n = a.obs['niche'];"
     "g = [set(n[x < 4]), set(n[(x >= 4) & (x < 8)]), set(n[x >= 8])];"
     "assert all(len(s) == 1 for s in g), g;"
     "assert len(set().union(*g)) == 3, g; print('region-ok')"],
    capture_output=True, timeout=300)
assert b"region-ok" in check.stdout, check.stderr.decode()[-2000:]

# k 越界 → INVALID_INPUT
bad_k = run_niche(DS, k=200)
assert not bad_k["ok"] and bad_k["error_code"] == "INVALID_INPUT", bad_k

# 无 deconv.h5ad → ST_NICHE_NO_DECONV
no_dec = run_niche(DS_NODEC, k=3)
assert not no_dec["ok"] and no_dec["error_code"] == "ST_NICHE_NO_DECONV", no_dec

print("SMOKE OK | niches:", o["n_niches"],
      "| sizes:", o["niche_sizes"],
      "| dominant:", o["dominant_by_niche"],
      "| region-ok | INVALID_INPUT/NO_DECONV rejected")
