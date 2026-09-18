"""st_nichenet 冒烟（Phase 65 空间版 NicheNet）：合成空间网格三区 +
KB 级统计桥接，容器断网跑。

场景：
①human 空间自证：20×20 网格（x<8=core "R"｜x∈[8,10)=sender 带｜
  x≥10=远端），sender 带高表达 60 真实配体（含 A2M），5 诱饵配体只在
  远端表达 → 跑通 + A2M ∈ top10 + 诱饵 0/5 入 tested（空间约束地面
  真值——max_rings=1 下远端 r≥2 不入 sender 母体，sender_pct=0 被
  min_expr 过滤）+ rings_{niche}.png 产物 + pngs 键 + ring_counts 含 core=160；
②mouse：Title-case 自动探测 species（不显式传）→ 跑通链路；
③geneset 全假名 → INVALID_INPUT（先验靶空间交集 <5）；
④receiver_niche 取值不存在 → ST_NICHENET_NO_GROUP；
⑤groupby 列不存在 → ST_NICHENET_NO_GROUP；
⑥无 obsm.spatial → ST_FORMAT_INVALID。

基因名取镜像内先验 RDS 真实行/列名（步骤 0 导出——六补记假名
映射率崩教训同源；与 _smoke_sc_nichenet 共用 _nnpeek 目录，已存在
即跳过）；A2M 靶基因集 = lt[, "A2M"] top30（探针 v3 同口径）。
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
# sc_tools 整目录挂载对齐 handler 的 script_dir 行为：CI/本地镜像不必内含 st_nichenet.py
BASE = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
        "-v", f"{str(REPO / 'sandbox' / 'sc_tools').replace(chr(92), '/')}:/opt/sc_tools",
        IMG]

PEEK = WS / "_eval" / "_nnpeek"

# ---------- 0) 先验基因名导出（断网容器内 RDS -> csv；sc 冒烟已建则跳过） ----------
if not (PEEK / "human_a2m_targets.csv").exists():
    PEEK.mkdir(parents=True, exist_ok=True)
    r0 = r"""
lt <- readRDS('/opt/nichenet_prior/ligand_target_matrix_nsga2r_final.rds')
lt <- lt[, colSums(lt) > 0, drop = FALSE]
lm <- readRDS('/opt/nichenet_prior/ligand_target_matrix_nsga2r_final_mouse.rds')
lm <- lm[, colSums(lm) > 0, drop = FALSE]
dir.create('/ws/_eval/_nnpeek', showWarnings = FALSE, recursive = TRUE)
write.csv(rownames(lt), '/ws/_eval/_nnpeek/human_targets.csv',
          row.names = FALSE, quote = FALSE)
write.csv(colnames(lt), '/ws/_eval/_nnpeek/human_ligands.csv',
          row.names = FALSE, quote = FALSE)
a2 <- names(sort(lt[, 'A2M'], decreasing = TRUE))[1:30]
write.csv(a2, '/ws/_eval/_nnpeek/human_a2m_targets.csv',
          row.names = FALSE, quote = FALSE)
write.csv(rownames(lm), '/ws/_eval/_nnpeek/mouse_targets.csv',
          row.names = FALSE, quote = FALSE)
write.csv(colnames(lm), '/ws/_eval/_nnpeek/mouse_ligands.csv',
          row.names = FALSE, quote = FALSE)
cat('PEEK_OK\n')
"""
    p = subprocess.run(BASE + ["Rscript", "-e", r0], capture_output=True, text=True, timeout=600)
    assert "PEEK_OK" in p.stdout, f"peek failed:\n{p.stdout[-400:]}\n{p.stderr[-600:]}"
    print("prior peek done")

a2m_targets = pd.read_csv(PEEK / "human_a2m_targets.csv")["x"].astype(str).tolist()
ht = pd.read_csv(PEEK / "human_targets.csv")["x"].astype(str).tolist()
hl = pd.read_csv(PEEK / "human_ligands.csv")["x"].astype(str).tolist()
mt = pd.read_csv(PEEK / "mouse_targets.csv")["x"].astype(str).tolist()
ml = pd.read_csv(PEEK / "mouse_ligands.csv")["x"].astype(str).tolist()

rng = np.random.default_rng(7)


def build(ds: str, targets_pool: list, ligands_pool: list,
          anchor: str = "", decoys: list | None = None,
          spatial: bool = True) -> list:
    """合成空间库：20×20 网格三区（core x<8 ｜ sender 带 8≤x<10 ｜ 远端 x≥10）。

    60 个真实配体（含 anchor）在 sender 带高表达；decoys（真实先验
    配体名）只在远端高表达——max_rings=1 下 sender=r1，远端 r≥2 不入
    sender 母体，诱饵 sender_pct=0 被 min_expr 过滤（空间约束地面真值）。
    spatial=False 时不写 obsm['spatial']（ST_FORMAT_INVALID 路径用）。
    返回实际生效的诱饵名列表。
    """
    n_side = 20
    n = n_side * n_side
    xs = np.repeat(np.arange(n_side), n_side).astype(float)
    ys = np.tile(np.arange(n_side), n_side).astype(float)
    grp = np.where(xs < 8, "R", np.where(xs < 10, "S", "F"))
    ligands = [g for g in ligands_pool if g != anchor][:59] + [anchor]
    dcs = [g for g in (decoys or []) if g not in ligands][:5]
    tgs = list(
        dict.fromkeys(
            rng.choice(
                [g for g in targets_pool if g not in ligands and g not in dcs],
                460, replace=False).tolist()
        )
    )
    genes = ligands + dcs + tgs
    X = rng.poisson(1.0, (n, len(genes))).astype(np.float32)  # 全基因基础表达
    band = grp == "S"
    X[band, : len(ligands)] = rng.poisson(40, (int(band.sum()), len(ligands))).astype(np.float32)
    far = grp == "F"
    if dcs:
        X[far, len(ligands): len(ligands) + len(dcs)] = rng.poisson(
            40, (int(far.sum()), len(dcs))).astype(np.float32)
        # 非远端区严格零表达：否则基础 poisson(1.0) 下诱饵 sender_pct≈0.63
        # ≥ min_expr，空间过滤地面真值失效（真实场景诱饵只是低表达）
        X[~far, len(ligands): len(ligands) + len(dcs)] = 0.0
    import anndata as ad

    a = ad.AnnData(X=X)
    a.var_names = genes
    a.obs_names = [f"s{i}" for i in range(n)]
    a.obs["spatial_domain"] = pd.Categorical(grp)
    if spatial:
        a.obsm["spatial"] = np.c_[xs, ys] * 100.0
    d = WS / ds
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "processed.h5ad")
    return dcs


decoys = build("stnnsmoke", ht, hl, anchor="A2M",
               decoys=[g for g in hl if g != "A2M"][:80])
build("stnnsmokem", mt, ml, anchor=ml[0])
build("stnnsmoke_nosp", ht, hl, anchor="A2M", spatial=False)


def run_stnn(ds: str, **kw):
    """容器内跑 st_nichenet.py（stdin JSON），严格口径整体解析 stdout。"""
    payload = json.dumps({"dataset_id": ds, **kw})
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/st_nichenet.py"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=1500,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① human 空间自证（A2M rank 靠前 + 诱饵空间过滤 + rings 产物）
o1 = run_stnn("stnnsmoke", geneset=a2m_targets, receiver_niche="R", species="human")
assert o1["ok"], o1
assert o1["species"] == "human", o1
assert o1["receiver_niche"] == "R", o1
assert o1["n_receiver"] == 160, o1["n_receiver"]
assert 0 < o1["n_sender"] < 160, o1["n_sender"]
assert o1["n_geneset_used"] == len(a2m_targets), o1["n_geneset_used"]
assert o1["n_ligands_tested"] >= 30, o1["n_ligands_tested"]
assert "A2M" in list(o1["top_ligands"])[:10], o1["top_ligands"]
assert o1["ring_counts"].get("0") == 160 and o1["ring_counts"].get("1", 0) > 0, o1["ring_counts"]
act = pd.read_csv(WS / "stnnsmoke" / "nichenet_ligand_activities_r.csv")
assert list(act.columns[:2]) == ["test_ligand", "auroc"], act.columns
assert not act["test_ligand"].isin(decoys).any(), "decoys must be spatially filtered"
for f in (
    "nichenet_ligand_target_links_r.csv",
    "nichenet_ligand_bar_r.png",
    "nichenet_ligand_target_heatmap_r.png",
    "rings_r.png",
):
    assert (WS / "stnnsmoke" / f).exists(), f
assert len(o1["pngs"]) == 3, o1["pngs"]

# ② mouse：species 自动探测（Title-case → mouse）
o2 = run_stnn("stnnsmokem", geneset=list(rng.choice(mt, 25, replace=False)), receiver_niche="R")
assert o2["ok"] and o2["species"] == "mouse", o2
assert o2["n_ligands_tested"] > 0, o2

# ③ geneset 全假名 → INVALID_INPUT
o3 = run_stnn("stnnsmoke", geneset=[f"FAKE{i:02d}" for i in range(20)],
              receiver_niche="R", species="human")
assert not o3["ok"] and o3["error_code"] == "INVALID_INPUT", o3

# ④ receiver_niche 取值不存在 → ST_NICHENET_NO_GROUP
o4 = run_stnn("stnnsmoke", geneset=a2m_targets, receiver_niche="NOPE", species="human")
assert not o4["ok"] and o4["error_code"] == "ST_NICHENET_NO_GROUP", o4

# ⑤ groupby 列不存在 → ST_NICHENET_NO_GROUP
o5 = run_stnn("stnnsmoke", geneset=a2m_targets, receiver_niche="R",
              groupby="nope", species="human")
assert not o5["ok"] and o5["error_code"] == "ST_NICHENET_NO_GROUP", o5

# ⑥ 无 obsm.spatial → ST_FORMAT_INVALID
o6 = run_stnn("stnnsmoke_nosp", geneset=a2m_targets, receiver_niche="R", species="human")
assert not o6["ok"] and o6["error_code"] == "ST_FORMAT_INVALID", o6

print(
    f"SMOKE OK | human spatial A2M top10 self-hit"
    f" decoys {int(act['test_ligand'].isin(decoys).sum())}/5 tested"
    f" ligands={o1['n_ligands_tested']} sender={o1['n_sender']}/{o1['n_spots']}"
    f" | mouse auto ok ({o2['n_ligands_tested']} ligands)"
    f" | fake geneset rejected | bad niche rejected | bad groupby rejected"
    f" | no-spatial rejected"
)
