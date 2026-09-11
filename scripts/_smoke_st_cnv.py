"""st_cnv 容器冒烟：合成网格 spot 注入 CNV 信号真跑（断网）。

合成 12×12 网格：左半 "T cells"（参考）右半 "Epithelial tumor"（注入
chr7×1.8 gain / chr10×0.6 loss，恶性应集中右半）。基因坐标用容器内
真实 TSV（/opt/cnv/gene_pos_grch38.tsv）挑 chr1/2/3/7/10 各 220 个。
数据构造在容器内跑（宿主无坐标 TSV），CNV 调用走 BioRunner 约定：
docker run --rm -i --network none -v <workspace>:/ws <img> python
/opt/st_tools/cnv.py，stdin 传 args JSON。
"""
import json
import subprocess
from pathlib import Path

WS = Path("I:/飞书agent/bio_workspace")
DS = "stcnvsmoke"
IMG = "feishu-research-agent/bio:st-cpu-latest"
BASE = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG]

_BUILDER = r'''
import numpy as np
import pandas as pd
import anndata as ad

rng = np.random.default_rng(42)
pos = pd.read_csv("/opt/cnv/gene_pos_grch38.tsv", sep="\t")
sel = pd.concat([pos[pos["chromosome"] == f"chr{c}"].head(220)
                 for c in (1, 2, 3, 7, 10)]).drop_duplicates("gene_name")
genes = sel["gene_name"].tolist()
n_g = len(genes)
xs, ys = np.meshgrid(np.arange(12), np.arange(12))
coords = np.column_stack([xs.ravel(), ys.ravel()]).astype(float)
n = coords.shape[0]
left = coords[:, 0] < 6
X = rng.poisson(2.0, (n, n_g)).astype(np.float32)
gain = sel["chromosome"].eq("chr7").to_numpy()
loss = sel["chromosome"].eq("chr10").to_numpy()
for idx in np.where(~left)[0]:
    X[idx, gain] = rng.poisson(3.6, int(gain.sum()))
    X[idx, loss] = rng.poisson(0.72, int(loss.sum()))
barcodes = [f"s{i}" for i in range(n)]
ct = np.where(left, "T cells", "Epithelial tumor")
obs = pd.DataFrame({"celltype": ct,
                    "leiden": pd.Categorical(np.where(left, "0", "1"))},
                   index=barcodes)
filtered = ad.AnnData(X=X, obs=obs.copy(),
                      var=pd.DataFrame(index=pd.Index(genes)))
filtered.obsm["spatial"] = coords
filtered.write_h5ad("/ws/stcnvsmoke/filtered.h5ad")
proc = ad.AnnData(X=np.zeros((n, 10), dtype=np.float32), obs=obs,
                  var=pd.DataFrame(index=[f"g{i}" for i in range(10)]))
proc.obsm["spatial"] = coords
proc.write_h5ad("/ws/stcnvsmoke/processed.h5ad")
print("built", X.shape)
'''


def build_dataset() -> None:
    """容器内构造合成数据集（需镜像内坐标 TSV）。"""
    bdir = WS / "_builder_stcnv"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "build_data.py").write_text(_BUILDER, encoding="utf-8")
    (WS / DS).mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", "none",
         "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
         IMG, "python", "/ws/_builder_stcnv/build_data.py"],
        capture_output=True, timeout=600)
    assert out.returncode == 0, out.stderr.decode()[-2000:]


def run_cnv(**kw):
    """容器内跑 cnv.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": DS, **kw})
    r = subprocess.run(BASE + ["python", "/opt/st_tools/cnv.py"],
                       input=payload, capture_output=True, text=True,
                       timeout=1800)
    # 脚本级 fail() 也是 exit 1 + stdout JSON（BioRunner 同款口径）；
    # 仅当 stdout 无法解析为 JSON 才算执行崩溃
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n"
                         f"{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


build_dataset()

o = run_cnv(annotation_key="celltype")
assert o["ok"], o
assert set(o["matched_references"]) >= {"T cells"}, o["matched_references"]
assert o["genes"]["positioned"] >= 1000, o["genes"]
epi_mal = o["malignant_by_label"].get("Epithelial tumor", 0)
assert epi_mal > 0 and epi_mal / o["n_malignant"] >= 0.8, o
assert len(o["pngs"]) == 3
ds_dir = WS / DS
for f in ("cnv/cnv_chromosome_heatmap.png", "cnv/cnv_score_spatial.png",
          "cnv/cnv_subclone_spatial.png", "cnv/cnv_label_summary.csv",
          "cnv/cnv_subclone_by_chromosome.csv"):
    assert (ds_dir / f).exists(), f

# 数字簇注释零匹配内置清单 → 不静默降级
no_ref = run_cnv(annotation_key="leiden")
assert not no_ref["ok"] and no_ref["error_code"] == "ST_CNV_NO_REFERENCE", no_ref

# deconv 特殊值无产物 → 引导先跑 st_deconvolve
no_dec = run_cnv(annotation_key="deconv")
assert not no_dec["ok"] and no_dec["error_code"] == "ST_CNV_NO_DECONV", no_dec

# 不存在注释列 → INVALID_INPUT 列可用列
bad_key = run_cnv(annotation_key="nope")
assert not bad_key["ok"] and bad_key["error_code"] == "INVALID_INPUT", bad_key

# obs 写回验证（容器内回读）
check = subprocess.run(
    ["docker", "run", "--rm", "--network", "none",
     "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG,
     "python", "-c",
     "import anndata as ad; a = ad.read_h5ad('/ws/stcnvsmoke/processed.h5ad')"
     "; assert 'cnv_score' in a.obs and 'is_malignant' in a.obs and "
     "'cnv_subclone' in a.obs; print('obs-ok')"],
    capture_output=True, timeout=300)
assert b"obs-ok" in check.stdout, check.stderr.decode()[-2000:]

print("SMOKE OK | malignant:", o["n_malignant"], "/", o["n_spots"],
      "| epi precision:", round(epi_mal / o["n_malignant"], 3),
      "| refs:", o["matched_references"],
      "| NO_REFERENCE/NO_DECONV/INVALID_INPUT rejected | obs-ok")
