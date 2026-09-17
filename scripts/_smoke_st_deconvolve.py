"""st_deconvolve 容器冒烟：CPU 参数化 + 斜杠标签净化真跑（断网）。

2026-09-12 真实 Visium 验收两项修复的 E2E 钉子：
① deconvolve CPU 参数化（ref_epochs/num_samples/ref_max_cells_per_type
   暴露前为硬编码）——小轮数参数使容器冒烟从小时级降到分钟级，
   ref 3 类型×80 细胞限帽 50 应得 n_ref_cells=150（限帽逻辑真跑）；
② _clean_label（h5py 禁 obs 键含 "/"）——参考类型名 "T/NK cells"
   应在输出 cell_types 中净化为 "T_NK cells"（真实数据 "Tem/Effector
   helper T cells" 曾致训练 2h 后写出才炸）。

合成数据：12×12 网格 144 spots×300 genes Poisson counts；sc 参考
240 细胞×300 genes（同基因名，表达量足够过 filter_genes）。
"""
import json
import os
import subprocess
from pathlib import Path

# 宿主 workspace/镜像：env 覆写（CI 冒烟用，与 settings 同口径）
WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", "I:/飞书agent/bio_workspace"))
DS = "stdeconvsmoke"
IMG = os.environ.get("BIO_ST_IMAGE", "feishu-research-agent/bio:st-cpu-latest")
BUILDER_DIR = "_builder_stdeconv"
BASE = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
        "-v", f"{str(WS / BUILDER_DIR).replace(chr(92), '/')}:/data:ro",
        IMG]

_BUILDER = r'''
import numpy as np
import pandas as pd
import anndata as ad

rng = np.random.default_rng(42)
xs, ys = np.meshgrid(np.arange(12), np.arange(12))
coords = np.column_stack([xs.ravel(), ys.ravel()]).astype(float)
n = coords.shape[0]
genes = [f"g{i}" for i in range(300)]

# 空间侧：三区域各有主导类型表达签名（g0-99/100-199/200-299）
sp_counts = rng.poisson(0.5, size=(n, 300)).astype(np.float32)
x = coords[:, 0]
sp_counts[x < 4, 0:100] += rng.poisson(
    3, size=(int((x < 4).sum()), 100)).astype(np.float32)
sp_counts[(x >= 4) & (x < 8), 100:200] += rng.poisson(
    3, size=(int(((x >= 4) & (x < 8)).sum()), 100)).astype(np.float32)
sp_counts[x >= 8, 200:300] += rng.poisson(
    3, size=(int((x >= 8).sum()), 100)).astype(np.float32)
barcodes = [f"s{i}" for i in range(n)]
sp = ad.AnnData(X=sp_counts,
                obs=pd.DataFrame(index=barcodes),
                var=pd.DataFrame(index=genes))
sp.obsm["spatial"] = coords
# spatial_scatter 需 uns['spatial'] 壳（stats.py 占位壳模式同款）
sp.uns["spatial"] = {"_placeholder": {
    "images": {"hires": np.zeros((8, 8, 3))},
    "scalefactors": {"tissue_hires_scalef": 1.0,
                     "spot_diameter_fullres": 1.0}}}
sp.write_h5ad("/ws/stdeconvsmoke/filtered.h5ad")

# sc 参考：3 类型×80 细胞，签名基因块高表达；类型名含 "/" 钉子
types = (["T/NK cells"] * 80 + ["Tumor"] * 80 + ["Fibroblast"] * 80)
ref_counts = rng.poisson(0.5, size=(240, 300)).astype(np.float32)
ref_counts[0:80, 0:100] += 3.0
ref_counts[80:160, 100:200] += 3.0
ref_counts[160:240, 200:300] += 3.0
ref = ad.AnnData(X=ref_counts,
                 obs=pd.DataFrame({"cell_type": types},
                                  index=[f"c{i}" for i in range(240)]),
                 var=pd.DataFrame(index=genes))
ref.write_h5ad("/ws/_builder_stdeconv/ref.h5ad")
print("built", sp_counts.shape, ref_counts.shape)
'''


def build_dataset() -> None:
    """容器内构造合成空间数据与 sc 参考（含斜杠标签）。"""
    bdir = WS / BUILDER_DIR
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "build_data.py").write_text(_BUILDER, encoding="utf-8")
    (WS / DS).mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", "none",
         "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
         IMG, "python", f"/ws/{BUILDER_DIR}/build_data.py"],
        capture_output=True, timeout=600)
    assert out.returncode == 0, out.stderr.decode()[-2000:]


def run_deconvolve(**kw):
    """容器内跑 deconvolve.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": DS, **kw})
    r = subprocess.run(BASE + ["python", "/opt/st_tools/deconvolve.py"],
                       input=payload, capture_output=True, text=True,
                       timeout=1800)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n"
                         f"{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


def main() -> None:
    """冒烟主流程：小轮数反卷积 + 限帽/斜杠标签双断言。"""
    build_dataset()
    out = run_deconvolve(
        sc_ref_path="ref.h5ad", ref_label_col="cell_type",
        max_epochs=5, ref_epochs=3, num_samples=10,
        ref_max_cells_per_type=50)
    assert out.get("ok") is True, out
    assert out["n_cell_types"] == 3, out
    assert out["n_ref_cells"] == 150, (
        f"ref_max_cells_per_type=50 应限帽 3×50=150，实得 {out}")
    assert "T_NK cells" in out["cell_types"], (
        f"斜杠标签应净化为 T_NK cells，实得 {out['cell_types']}")
    assert (WS / DS / "deconv.h5ad").exists(), "deconv.h5ad 未落盘"
    print("SMOKE OK: st_deconvolve cpu 参数化 + 斜杠标签净化 | "
          f"n_ref_cells={out['n_ref_cells']} "
          f"cell_types={out['cell_types']}")


if __name__ == "__main__":
    main()
