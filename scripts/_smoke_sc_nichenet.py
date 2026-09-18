"""sc_nichenet 冒烟（NicheNet 配体活性优先级，八补记 q2 探针终判
落地）：合成表达库 + KB 级统计桥接，容器断网跑。

场景：
①human：A2M 自证（geneset=A2M 先验 top30 靶基因、sender 群表达
  60 个真实配体含 A2M）→ 跑通 + A2M ∈ top10 + activities/links
  csv + 两图；探针口径 aupr 0.985 rank 1/68 在合成库宽松断言 top10；
②mouse：Title-case 符号自动探测 species（不显式传）→ 跑通 +
  n_ligands_tested>0（mouse 先验链路）；
③geneset 全假名 → INVALID_INPUT（先验靶空间交集 <5）；
④sender_groups 取值不存在 → INVALID_INPUT；
⑤species 非法 → INVALID_INPUT。

基因名取镜像内先验 RDS 真实行/列名（步骤 0 导出——六补记假名
映射率崩教训同源）；A2M 靶基因集 = lt[, "A2M"] top30（探针 v3
同口径）。
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", Path(__file__).resolve().parents[1] / "bio_workspace"))
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")
BASE = ["docker", "run", "--rm", "-i", "--network", "none", "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG]

PEEK = WS / "_eval" / "_nnpeek"

# ---------- 0) 先验基因名导出（断网容器内 RDS -> csv） ----------
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


def build(ds: str, targets_pool: list, ligands_pool: list, n: int = 600, anchor: str = "") -> None:
    """合成库：500 基因（40 真实配体含 anchor + 460 靶基因），两群。"""
    ligands = [g for g in ligands_pool if g != anchor][:39] + [anchor]
    tgs = list(
        dict.fromkeys(rng.choice([g for g in targets_pool if g not in ligands], 460, replace=False).tolist())
    )
    genes = ligands + tgs
    X = rng.poisson(1.0, (n, len(genes))).astype(np.float32)  # 全基因基础表达
    # sender 群（后半）配体列高表达 → pct~100%
    X[n // 2 :, : len(ligands)] = rng.poisson(40, (n // 2, len(ligands))).astype(np.float32)
    import anndata as ad

    grp = ["R1"] * (n // 2) + ["S1"] * (n // 2)
    a = ad.AnnData(X=X)
    a.var_names = genes
    a.obs_names = [f"c{i}" for i in range(n)]
    a.obs["celltype"] = pd.Categorical(grp)
    d = WS / ds
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "processed.h5ad")


build("nnsmoke", ht, hl, anchor="A2M")
build("nnsmokem", mt, ml, anchor=ml[0])  # mouse 自证不做，仅链路


def run_nn(ds: str, **kw):
    """容器内跑 nichenet.py（stdin JSON），严格口径整体解析 stdout。"""
    payload = json.dumps({"dataset_id": ds, **kw})
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/nichenet.py"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=1500,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① human + A2M 自证
o1 = run_nn(
    "nnsmoke",
    geneset=a2m_targets,
    groupby="celltype",
    sender_groups=["S1"],
    receiver_groups=["R1"],
    species="human",
)
assert o1["ok"], o1
assert o1["species"] == "human", o1
assert o1["n_cells_sender"] == 300 and o1["n_cells_receiver"] == 300, o1
assert o1["n_geneset_used"] == len(a2m_targets), o1["n_geneset_used"]
assert o1["n_ligands_tested"] >= 30, o1["n_ligands_tested"]
assert "A2M" in list(o1["top_ligands"])[:10], o1["top_ligands"]
assert o1["n_links"] > 0 and o1["top_ligands"], o1
act = pd.read_csv(WS / "nnsmoke" / "nichenet_ligand_activities.csv")
assert list(act.columns[:2]) == ["test_ligand", "auroc"], act.columns
for f in (
    "nichenet_ligand_target_links.csv",
    "nichenet_ligand_bar.png",
    "nichenet_ligand_target_heatmap.png",
):
    assert (WS / "nnsmoke" / f).exists(), f

# ② mouse：species 自动探测（Title-case → mouse）
o2 = run_nn(
    "nnsmokem", geneset=list(rng.choice(mt, 25, replace=False)), groupby="celltype", sender_groups=["S1"]
)
assert o2["ok"] and o2["species"] == "mouse", o2
assert o2["n_ligands_tested"] > 0, o2

# ③ geneset 全假名 → INVALID_INPUT
o3 = run_nn(
    "nnsmoke",
    geneset=[f"FAKE{i:02d}" for i in range(20)],
    groupby="celltype",
    sender_groups=["S1"],
    species="human",
)
assert not o3["ok"] and o3["error_code"] == "INVALID_INPUT", o3

# ④ sender_groups 取值不存在 → INVALID_INPUT
o4 = run_nn("nnsmoke", geneset=a2m_targets, groupby="celltype", sender_groups=["NOPE"], species="human")
assert not o4["ok"] and o4["error_code"] == "INVALID_INPUT", o4

# ⑤ species 非法 → INVALID_INPUT
o5 = run_nn("nnsmoke", geneset=a2m_targets, groupby="celltype", sender_groups=["S1"], species="zebrafish")
assert not o5["ok"] and o5["error_code"] == "INVALID_INPUT", o5

print(
    f"SMOKE OK | human A2M top10 self-hit | ligands={o1['n_ligands_tested']}"
    f" links={o1['n_links']} top={list(o1['top_ligands'])[:3]}"
    f" | mouse auto ok ({o2['n_ligands_tested']} ligands)"
    f" | fake geneset rejected | bad sender rejected | bad species rejected"
)
