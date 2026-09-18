"""st_niche_scan 冒烟（Phase 68 全 niche NicheNet 批量扫描）：复用
st_nichenet 冒烟的合成空间网格三区 + _nnpeek 先验导出，容器断网跑。

场景：
①human 三 niche（F 200/R 160/S 40 spot，计数降序枚举）批量扫描：
  n_niches_ok==3 + R niche A2M ∈ top10（空间自证）+ 汇总三件
  （matrix/heatmap/summary）落盘 + failures 空 + 每 niche slug 产物；
②min_spots=1000 → INVALID_INPUT（枚举空）；
③groupby 列不存在 → ST_NICHESCAN_NO_GROUP；
④无 obsm.spatial → ST_FORMAT_INVALID。

基因名取镜像内先验 RDS 真实行/列名（与 _smoke_sc_nichenet /
_smoke_st_nichenet 共用 _nnpeek 目录，已存在即跳过）；A2M 靶基因集
= lt[, "A2M"] top30（同 st_nichenet 冒烟口径，作诱饵与真值锚）。
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", Path(__file__).resolve().parents[1] / "bio_workspace"))
REPO = Path(__file__).resolve().parents[1]
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")
# sc_tools 整目录挂载对齐 handler 的 script_dir 行为（编排层 subprocess
# 自调同目录 st_nichenet.py，故挂载内两脚本都必须可见）
BASE = [
    "docker",
    "run",
    "--rm",
    "-i",
    "--network",
    "none",
    "-v",
    f"{str(WS).replace(chr(92), '/')}:/ws",
    "-v",
    f"{str(REPO / 'sandbox' / 'sc_tools').replace(chr(92), '/')}:/opt/sc_tools",
    IMG,
]

PEEK = WS / "_eval" / "_nnpeek"

# ---------- 0) 先验基因名导出（断网容器内 RDS -> csv；sc/st 冒烟已建则跳过） ----------
if not (PEEK / "human_ligands.csv").exists():
    PEEK.mkdir(parents=True, exist_ok=True)
    r0 = r"""
lt <- readRDS('/opt/nichenet_prior/ligand_target_matrix_nsga2r_final.rds')
lt <- lt[, colSums(lt) > 0, drop = FALSE]
dir.create('/ws/_eval/_nnpeek', showWarnings = FALSE, recursive = TRUE)
write.csv(rownames(lt), '/ws/_eval/_nnpeek/human_targets.csv',
          row.names = FALSE, quote = FALSE)
write.csv(colnames(lt), '/ws/_eval/_nnpeek/human_ligands.csv',
          row.names = FALSE, quote = FALSE)
a2 <- names(sort(lt[, 'A2M'], decreasing = TRUE))[1:30]
write.csv(a2, '/ws/_eval/_nnpeek/human_a2m_targets.csv',
          row.names = FALSE, quote = FALSE)
cat('PEEK_OK\n')
"""
    p = subprocess.run(BASE + ["Rscript", "-e", r0], capture_output=True, text=True, timeout=600)
    assert "PEEK_OK" in p.stdout, f"peek failed:\n{p.stdout[-400:]}\n{p.stderr[-600:]}"
    print("prior peek done")

hl = pd.read_csv(PEEK / "human_ligands.csv")["x"].astype(str).tolist()
ht = pd.read_csv(PEEK / "human_targets.csv")["x"].astype(str).tolist()
a2m_targets = pd.read_csv(PEEK / "human_a2m_targets.csv")["x"].astype(str).tolist()

rng = np.random.default_rng(7)


def build(ds: str) -> None:
    """合成空间库：20×20 网格三区（R x<8=160 ｜ S 8≤x<10=40 ｜ F x≥10=200）。

    60 真实配体（含 A2M）在 S 带高表达；5 诱饵（真实先验配体名）只在
    F 高表达——F 作为 receiver 时其上调 geneset 恰含 5 诱饵（全在先验
    靶空间，used≥5 阈值地面真值）；R 上调 A2M 先验靶 top20——R 的自动
    geneset 以真值锚为主，sender=S 带（A2M 高表达过 min_expr 门控）
    → A2M aupr 空间自证进 top10。
    """
    n_side = 20
    n = n_side * n_side
    xs = np.repeat(np.arange(n_side), n_side).astype(float)
    ys = np.tile(np.arange(n_side), n_side).astype(float)
    grp = np.where(xs < 8, "R", np.where(xs < 10, "S", "F"))
    ligands = [g for g in hl if g != "A2M"][:59] + ["A2M"]
    dcs = [g for g in hl if g not in ligands][-5:]
    # R 区真值锚：A2M 先验靶 top20 特意在 R 上调 → R 的 niche_up_genes
    # 以其为主，配体侧 A2M 在 S 带高表达 → aupr 空间自证
    a2m_hits = [g for g in a2m_targets if g not in ligands and g not in dcs][:20]
    tgs = list(
        dict.fromkeys(
            rng.choice(
                [g for g in ht if g not in ligands and g not in dcs and g not in a2m_hits],
                460,
                replace=False,
            ).tolist()
        )
    )
    genes = ligands + dcs + a2m_hits + tgs
    X = rng.poisson(1.0, (n, len(genes))).astype(np.float32)
    band = grp == "S"
    X[band, : len(ligands)] = rng.poisson(40, (int(band.sum()), len(ligands))).astype(np.float32)
    d0 = len(ligands) + len(dcs)
    far = grp == "F"
    X[far, len(ligands) : d0] = rng.poisson(40, (int(far.sum()), len(dcs))).astype(np.float32)
    X[~far, len(ligands) : d0] = 0.0  # 非远端严格零
    rgn = grp == "R"
    X[rgn, d0 : d0 + len(a2m_hits)] = rng.poisson(40, (int(rgn.sum()), len(a2m_hits))).astype(np.float32)
    import anndata as ad

    a = ad.AnnData(X=X)
    a.var_names = genes
    a.obs_names = [f"s{i}" for i in range(n)]
    a.obs["spatial_domain"] = pd.Categorical(grp)
    a.obsm["spatial"] = np.c_[xs, ys] * 100.0
    d = WS / ds
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "processed.h5ad")


build("stnnscan")
# 无 spatial 库（ST_FORMAT_INVALID 路径用）
(WS / "stnnscan_nosp").mkdir(parents=True, exist_ok=True)


def run_scan(ds: str, **kw):
    """容器内跑 st_niche_scan.py（stdin JSON），严格口径整体解析 stdout。"""
    payload = json.dumps({"dataset_id": ds, **kw})
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/st_niche_scan.py"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=900,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 三 niche 批量：枚举计数降序 F/R/S，R 空间自证，汇总三件落盘
# （n_geneset=20 与 a2m_hits 等宽：R 的自动 geneset 100% 为 A2M 先验靶，
#  复刻单 niche 冒烟纯度——geneset 混入噪声基因会稀释 aupr 区分度）
o1 = run_scan("stnnscan", species="human", n_geneset=20)
assert o1["ok"], o1
assert o1["n_niches_total"] == 3 and o1["n_niches_ok"] == 3, o1["n_niches_total"]
assert o1["n_niches_failed"] == 0 and not o1["failures"], o1["failures"]
assert set(o1["results"]) == {"F", "R", "S"}, o1["results"].keys()
assert "A2M" in list(o1["results"]["R"]["top_ligands"])[:10], o1["results"]["R"]["top_ligands"]
for niche in ("F", "R", "S"):
    csvp = o1["results"][niche]["ligand_activities_csv"]
    assert f"_{niche.lower()}.csv" in str(csvp), f"{niche} 产物缺 slug 后缀: {csvp}"
mat_p = WS / "stnnscan" / "nichenet_allniche_matrix.csv"
hm_p = WS / "stnnscan" / "nichenet_allniche_heatmap.png"
sm_p = WS / "stnnscan" / "nichenet_allniche_summary.json"
assert mat_p.exists() and hm_p.exists() and sm_p.exists()
mat = pd.read_csv(mat_p, index_col=0)
assert list(mat.columns) == ["F", "R", "S"], mat.columns
assert mat.notna().any().all(), mat
sm = json.loads(sm_p.read_text(encoding="utf-8"))
assert sm["n_niches_ok"] == 3 and not sm["failures"], sm["failures"]
assert sm["niches"]["R"]["n_spots"] == 160 and sm["niches"]["S"]["n_spots"] == 40, sm["niches"]
assert "A2M" in sm["niches"]["R"]["top_ligands"], sm["niches"]["R"]["top_ligands"]

# ② min_spots 过严 → 枚举空 → INVALID_INPUT
o2 = run_scan("stnnscan", min_spots=1000)
assert not o2["ok"] and o2["error_code"] == "INVALID_INPUT", o2

# ③ groupby 列不存在 → ST_NICHESCAN_NO_GROUP
o3 = run_scan("stnnscan", groupby="nope")
assert not o3["ok"] and o3["error_code"] == "ST_NICHESCAN_NO_GROUP", o3

# ④ 无 obsm.spatial → ST_FORMAT_INVALID
(WS / "stnnscan_nosp" / "processed.h5ad").unlink(missing_ok=True)
import anndata as ad  # noqa: E402

_a = ad.read_h5ad(WS / "stnnscan" / "processed.h5ad")
del _a.obsm["spatial"]
_a.write_h5ad(WS / "stnnscan_nosp" / "processed.h5ad")
o4 = run_scan("stnnscan_nosp", species="human")
assert not o4["ok"] and o4["error_code"] == "ST_FORMAT_INVALID", o4

print(
    f"SMOKE OK | 3-niche scan all green (F/R/S) R self-hit A2M top10"
    f" slug artifacts ok matrix={mat.shape}"
    f" | enum-empty rejected | bad groupby rejected | no-spatial rejected"
)
