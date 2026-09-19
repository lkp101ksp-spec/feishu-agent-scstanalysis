"""sc_tcr 冒烟（Phase 70 免疫组库重建，spec
2026-09-19-sc-tcr-design.md §9）：探针合成数据复刻 + 容器断网跑。

场景：
①四文件全量（2 患者×2 组织 + 6 类污染行全埋）：ok + n_cells_kept=25
  + n_clonotypes=9 + size_class {n>=3:20, n=1:5} + expa/migr 探针
  真值逐位一致（expa P1|Tumor=0.1870927082、P2|Tumor=0.0793801643、
  migr P1=0.5509775004/P2=0.4）+ 三产物落盘；
②患者隔离：tcr_cells.csv 里 P2 复刻 B 序列的克隆独立（≠P1 的
  clonotype_id）且 clone_size=1；
③单组织（只喂 P1-Tumor）：ok + migr=null 降级 + note 提示；
④contig_files 空列表 → INVALID_INPUT；
⑤缺 cdr3_nt 列 → INVALID_INPUT；
⑥写回成功分支：合成 GEX processed.h5ad（obs 与 contig cell_id
  60% 重叠）→ wrote_back + align_rate≥0.5 + obs 三列落盘；
⑦写回拒收分支：重叠 10% → TCR_ALIGN_FAILED。

contig csv 写 bio_workspace/_smoke_tcr_data/（挂 /data）；
GEX h5ad 写 bio_workspace/<ds>/（挂 /ws）。
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get(
    "BIO_WORKSPACE_ROOT",
    Path(__file__).resolve().parents[1] / "bio_workspace"))
DATA = WS / "_smoke_tcr_data"
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")

CONTIG_COLS = ["barcode", "is_cell", "high_confidence", "chain", "v_gene",
               "d_gene", "j_gene", "c_gene", "productive", "cdr3", "cdr3_nt",
               "raw_clonotype_id"]

A_TRA, A_TRB = "ACGTAAAACC", "GGTTTACGAA"
B_TRA, B_TRB = "TTTACGGGCA", "CCCAAATTTG"
C_TRA, C_TRB = "GCGCATATAT", "AATTGGCGTC"
D_TRA, D_TRB = "TATATCGCGC", "GCGCGATATA"


def _pair(barcode: str, ct: str, tra_nt: str, trb_nt: str,
          **over: object) -> list[dict]:
    """一个健康双链细胞的 TRA+TRB 两行；over 覆盖字段（污染行用）。"""
    rows = []
    for chain, v, d, j, c, nt in (
            ("TRA", "TRAV1-1", "None", "TRAJ1", "TRAC", tra_nt),
            ("TRB", "TRBV1", "TRBD1", "TRBJ1-1", "TRBC1", trb_nt)):
        r = {"barcode": barcode, "is_cell": True, "high_confidence": True,
             "chain": chain, "v_gene": v, "d_gene": d, "j_gene": j,
             "c_gene": c, "productive": True, "cdr3": f"CASS{ct}F",
             "cdr3_nt": nt, "raw_clonotype_id": ct}
        r.update(over)
        rows.append(r)
    return rows


def build_files() -> None:
    """写 4 份 contig csv（探针 build_synthetic 同构）+ 污染 csv。"""
    DATA.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict]] = {
        "p1t": [], "p1p": [], "p2t": [], "p2p": []}

    def add(key: str, n: int, tra: str, trb: str, ct: str, pre: str) -> None:
        for i in range(n):
            tables[key].extend(_pair(f"{pre}{i}", ct, tra, trb))

    add("p1t", 6, A_TRA, A_TRB, "ctA", "p1t_a")   # clone_A P1-Tumor
    add("p1p", 3, A_TRA, A_TRB, "ctA", "p1p_a")   # clone_A P1-PBMC（跨组织）
    add("p1t", 4, B_TRA, B_TRB, "ctB", "p1t_b")   # clone_B P1-Tumor-only
    add("p2t", 2, C_TRA, C_TRB, "ctC", "p2t_c")   # clone_C P2 双组织
    add("p2p", 2, C_TRA, C_TRB, "ctC", "p2p_c")
    add("p2t", 3, D_TRA, D_TRB, "ctD", "p2t_d")   # clone_D P2-Tumor-only
    add("p1t", 1, "AAAAAaaaa1", "CCCCCcccc1", "ctS1", "p1t_s1")  # 单例×4
    add("p1t", 1, "AAAAAaaaa2", "CCCCCcccc2", "ctS2", "p1t_s2")
    add("p2t", 1, "GGGGGgggg1", "TTTTTtttt1", "ctS3", "p2t_s3")
    add("p2p", 1, "GGGGGgggg2", "TTTTTtttt2", "ctS4", "p2p_s4")
    # 患者隔离探针：P2 复刻 clone_B 序列
    add("p2p", 1, B_TRA, B_TRB, "ctB2", "p2p_xb")

    bad: list[dict] = []
    bad.extend(_pair("bad_single", "ctX", "NNNtra", "NNNtrb")[:1])  # 单链
    bad.extend(_pair("bad_notcell", "ctX", "NN1", "NN2",
                     is_cell=False))
    bad.extend(_pair("bad_lowconf", "ctX", "NN3", "NN4",
                     high_confidence=False))
    bad.extend(_pair("bad_unprod", "ctX", "NN5", "NN6", productive=False))
    bad.extend(_pair("bad_igk", "ctX", "NN7", "NN8", chain="IGK",
                     c_gene="IGKC"))
    amb = _pair("bad_amb", "ctAmb1", "NN9", "NN10")
    amb[1]["raw_clonotype_id"] = "ctAmb2"
    bad.extend(amb)
    tables["p1t"].extend(bad)

    for k, v in tables.items():
        pd.DataFrame(v, columns=CONTIG_COLS).to_csv(
            DATA / f"{k}.csv", index=False)
    # 缺列场景文件
    pd.DataFrame([{c: "x" for c in CONTIG_COLS if c != "cdr3_nt"}]
                 ).to_csv(DATA / "nocol.csv", index=False)


def build_gex(ds: str, n_hit: int) -> str:
    """合成 GEX processed.h5ad：obs 20 细胞 = n_hit 个真实 contig
    cell_id（全串口径命中）+ 其余无关名。返回 dataset 目录名。"""
    import anndata as ad

    rng = np.random.default_rng(7)
    hits = [f"P1__Tumor__p1t_a{i}" for i in range(6)] + \
           [f"P1__Tumor__p1t_b{i}" for i in range(4)] + \
           [f"P2__PBMC__p2p_c{i}" for i in range(2)]
    names = hits[:n_hit] + [f"unrelated{i}" for i in range(20 - n_hit)]
    a = ad.AnnData(X=rng.poisson(2, (len(names), 50)).astype(np.float32))
    a.var_names = [f"G{i:02d}" for i in range(50)]
    a.obs_names = names
    d = WS / ds
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "processed.h5ad")
    return ds


build_files()

REPO = Path(__file__).resolve().parents[1]
MOUNTS = ["-v", f"{str(DATA).replace(chr(92), '/')}:/data",
          "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
          # sc_tools 整目录挂载对齐 handler 的 script_dir 行为：
          # CI/本地镜像不必内含 tcr.py（Phase 65 先例）
          "-v", f"{str(REPO / 'sandbox' / 'sc_tools').replace(chr(92), '/')}"
          ":/opt/sc_tools"]
BASE = ["docker", "run", "--rm", "-i", "--network", "none", *MOUNTS, IMG]


def run_tcr(**kw):
    """容器内跑 tcr.py（stdin JSON），严格口径整体解析 stdout。"""
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/tcr.py"],
        input=json.dumps(kw), capture_output=True, text=True, timeout=600)
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n"
                         f"{r.stderr[-600:]}") from None


FILES4 = [{"file": f"{k}.csv", "patient": p, "tissue": t} for k, p, t in (
    ("p1t", "P1", "Tumor"), ("p1p", "P1", "PBMC"),
    ("p2t", "P2", "Tumor"), ("p2p", "P2", "PBMC"))]

# ① 四文件全量：克隆结构与指数逐位对拍
o1 = run_tcr(contig_files=FILES4)
assert o1["ok"], o1
assert o1["n_files"] == 4 and o1["n_patients"] == 2 and o1["n_tissues"] == 2
assert o1["n_cells_kept"] == 25, o1["n_cells_kept"]
assert o1["n_clonotypes"] == 9, o1["n_clonotypes"]
assert o1["size_class_counts"] == {"n>=3": 20, "n=1": 5}, \
    o1["size_class_counts"]
assert abs(o1["expa"]["P1|Tumor"] - 0.1870927082) < 1e-9, o1["expa"]
assert abs(o1["expa"]["P2|Tumor"] - 0.0793801643) < 1e-9, o1["expa"]
assert abs(o1["migr"]["P1"] - 0.5509775004) < 1e-9, o1["migr"]
assert abs(o1["migr"]["P2"] - 0.4) < 1e-9, o1["migr"]
assert o1["tissue_pair"] == "PBMC<->Tumor", o1["tissue_pair"]
assert "filter stats" in o1["note"] and "ambiguous" in o1["note"], o1["note"]
for f in (o1["tcr_cells_csv"], o1["tcr_indices_csv"], o1["overview_png"]):
    assert (WS / Path(f).relative_to("/ws")).exists(), f
print("① 四文件全量：expa/migr 真值一致 + 三产物落盘 OK")

# ② 患者隔离：P2 复刻 B 序列克隆独立且 n=1
cells = pd.read_csv(WS / Path(o1["tcr_cells_csv"]).relative_to("/ws"))
p1_b = set(cells[cells["cell_id"].str.contains("p1t_b")]["clonotype_id"])
p2_xb = cells[cells["cell_id"].str.contains("p2p_xb")]
assert len(p1_b) == 1 and len(p2_xb) == 1
assert p2_xb["clonotype_id"].iloc[0] not in p1_b
assert p2_xb["clonotype_id"].iloc[0].startswith("P2::")
assert int(p2_xb["clone_size"].iloc[0]) == 1
# clone_A 跨组织合一（9 细胞一个 id）
a_ids = set(cells[cells["cell_id"].str.contains(r"p1[tp]_a\d")]
            ["clonotype_id"])
assert len(a_ids) == 1
print("② 患者隔离 + 跨组织合一 OK")

# ③ 单组织降级：migr=null
o3 = run_tcr(contig_files=[FILES4[0]])
assert o3["ok"], o3
assert o3["migr"] is None and o3["n_tissues"] == 1, o3
assert "migr unavailable" in o3["note"], o3["note"]
assert abs(o3["expa"]["P1|Tumor"] - 0.1870927082) < 1e-9, o3["expa"]
print("③ 单组织 migr=null 降级 OK")

# ④ 空列表拒收
o4 = run_tcr(contig_files=[])
assert not o4["ok"] and o4["error_code"] == "INVALID_INPUT", o4
print("④ 空列表 INVALID_INPUT OK")

# ⑤ 缺列拒收
o5 = run_tcr(contig_files=[{"file": "nocol.csv", "patient": "PX",
                            "tissue": "Tumor"}])
assert not o5["ok"] and o5["error_code"] == "INVALID_INPUT", o5
assert "cdr3_nt" in o5["error_message"], o5
print("⑤ 缺列 INVALID_INPUT OK")

# ⑥ 写回成功分支（12/20 = 60% 命中）
ds_ok = build_gex("_smoke_tcr_gex_ok", 12)
o6 = run_tcr(contig_files=FILES4, dataset_ref=ds_ok)
assert o6["ok"], o6
assert o6["wrote_back"] is True and o6["align_rate"] >= 0.5, o6
assert o6["n_aligned"] > 0
import anndata as ad  # noqa: E402 —— 冒烟顶部已过 matplotlib 级依赖，此处就近

back = ad.read_h5ad(WS / ds_ok / "processed.h5ad")
for col in ("tcr_clonotype", "tcr_clone_size", "tcr_size_class"):
    assert col in back.obs.columns, col
hit_obs = back.obs[back.obs["tcr_clonotype"].astype(str).ne("")]
assert len(hit_obs) == o6["n_aligned"], (len(hit_obs), o6["n_aligned"])
sizes = back.obs["tcr_clone_size"]
assert sizes.max() == 9  # clone_A 全局 size 写回
print(f"⑥ 写回成功：align_rate={o6['align_rate']:.2f} "
      f"n_aligned={o6['n_aligned']} 三列落盘 OK")

# ⑦ 写回拒收分支（2/20 = 10% 命中）
ds_bad = build_gex("_smoke_tcr_gex_bad", 2)
o7 = run_tcr(contig_files=FILES4, dataset_ref=ds_bad)
assert not o7["ok"] and o7["error_code"] == "TCR_ALIGN_FAILED", o7
print("⑦ 对齐率<50% TCR_ALIGN_FAILED OK")

print("\nSMOKE OK: sc_tcr 7 场景全绿")
