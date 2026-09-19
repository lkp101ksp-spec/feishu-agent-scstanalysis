"""sc_cytosig 冒烟（Phase 71 细胞因子信号预测，spec
2026-09-19-sc-cytosig-design.md §10）：探针地面真值复刻 + 容器断网跑。

场景：
①diff 模式（探针判据①③复刻）：A 群注入 2.0×signature[TGFB1]、B 群
  纯噪声 → ok + n_factors=43 + TGFB1 在 top_by_beta["A"] top3 且
  断层（beta ≥ 其余因子最大值×2）+ zscore 口径同向 top3 + 三产物落盘；
②per_cell 模式：ok + scores csv 含 beta_by_group/zscore_by_group 段 +
  群汇总 TGFB1@A 领跑 + n_samples=40（逐细胞）；
③无信号对照（探针判据②复刻，读 ② 的 csv）：B 群纯噪声，p<0.01 的
  因子（任一 B 细胞）≤ 4/43；
④mode 非法 → INVALID_INPUT；
⑤groupby 缺列 → INVALID_INPUT + 候选列提示；
⑥非签名物种基因（600 个 FAKE 基因）→ CYTOSIG_LOW_OVERLAP。

合成 h5ad：800 签名基因 + 200 噪声基因 × 40 细胞（A/B 各 20），
N(6,0.8) log 形态基底（与探针同分布同种子）；签名基因列表从镜像
venv 内 CytoSig.find_signature_path() 动态导出（不硬编码路径）。
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
DATA = WS / "_smoke_cytosig_data"
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")

REPO = Path(__file__).resolve().parents[1]
MOUNTS = ["-v", f"{str(WS).replace(chr(92), '/')}:/ws",
          # sc_tools 整目录挂载对齐 handler 的 script_dir 行为（Phase 65 先例）
          "-v", f"{str(REPO / 'sandbox' / 'sc_tools').replace(chr(92), '/')}"
          ":/opt/sc_tools"]
BASE = ["docker", "run", "--rm", "-i", "--network", "none", *MOUNTS, IMG]

DUMP_CMD = (
    "import pandas as pd, CytoSig; "
    "sig = pd.read_csv(CytoSig.find_signature_path(), sep='\\t', "
    "index_col=0); "
    "sig['TGFB1'].head(800).to_csv("
    "'/ws/_smoke_cytosig_data/tgfb1_sig.csv', index_label='gene')")

DATA.mkdir(parents=True, exist_ok=True)

# 签名基因 + TGFB1 列从镜像 venv 动态导出（主环境无 CytoSig 包）
r0 = subprocess.run(
    BASE + ["/opt/cytosig_env/bin/python", "-c", DUMP_CMD],
    capture_output=True, text=True, timeout=300)
if r0.returncode != 0:
    raise SystemExit(f"sig dump rc={r0.returncode}\n{r0.stderr[-600:]}")
sig_tgfb1 = pd.read_csv(DATA / "tgfb1_sig.csv", index_col="gene")["TGFB1"]
assert len(sig_tgfb1) == 800, len(sig_tgfb1)


def build_gex(ds: str, genes: list[str], inject_a: bool) -> str:
    """合成 processed.h5ad：genes × 40 细胞（A/B 各 20）；inject_a 时
    A 群加 2.0×TGFB1 签名向量（探针同构，seed=7）。"""
    import anndata as ad

    rng = np.random.default_rng(7)
    n_a, n_b = 20, 20
    x = rng.normal(6.0, 0.8, (n_a + n_b, len(genes))).astype(np.float32)
    if inject_a:
        sig_vec = np.array([sig_tgfb1.get(g, 0.0) for g in genes],
                           dtype=np.float32)
        x[:n_a] += 2.0 * sig_vec
    a = ad.AnnData(X=x)
    a.var_names = genes
    a.obs_names = [f"A{i:02d}" for i in range(n_a)] + \
                  [f"B{i:02d}" for i in range(n_b)]
    a.obs["celltype"] = ["A"] * n_a + ["B"] * n_b
    d = WS / ds
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "processed.h5ad")
    return ds


SIG_GENES = list(sig_tgfb1.index)
DS_MAIN = build_gex("_smoke_cytosig_gex", SIG_GENES +
                    [f"NOISE{i:04d}" for i in range(200)], True)
DS_LOWOV = build_gex("_smoke_cytosig_lowov",
                     [f"FAKE{i:05d}" for i in range(600)], False)


def run_cytosig(**kw):
    """容器内跑 cytosig.py（stdin JSON），整体解析 stdout。"""
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/cytosig.py"],
        input=json.dumps(kw), capture_output=True, text=True, timeout=1200)
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n"
                         f"{r.stderr[-600:]}") from None


# ① diff 模式：TGFB1@A 双口径 top3 + 断层 ≥2×（探针判据①③）
o1 = run_cytosig(dataset_ref=DS_MAIN, groupby="celltype", nrand=1000)
assert o1["ok"], o1
assert o1["mode"] == "diff" and o1["n_factors"] == 43, o1
assert o1["n_genes_used"] == 800, o1["n_genes_used"]
assert o1["n_samples"] == 2, o1["n_samples"]
top_b = o1["top_by_beta"]["A"]
rank = [f for f, _ in top_b].index("TGFB1")
others = [v for f, v in top_b if f != "TGFB1"]
assert rank <= 2, top_b  # top3（0 基）
assert top_b[rank][1] >= max(others) * 2.0, top_b  # 断层 ≥2×
assert "TGFB1" in [f for f, _ in o1["top_by_zscore"]["A"][:3]], \
    o1["top_by_zscore"]["A"]
assert "adata.X" in o1["note"], o1["note"]
for key in ("scores_csv", "heatmap_png", "top_png"):
    assert (WS / Path(o1[key]).relative_to("/ws")).exists(), o1[key]
print(f"① diff：TGFB1 rank={rank + 1}/43 断层 {top_b[rank][1]:.4f} "
      f"vs {max(others):.4f} + zscore 同向 + 三产物落盘 OK")

# ② per_cell 模式：逐细胞 + 群汇总段
o2 = run_cytosig(dataset_ref=DS_MAIN, groupby="celltype", mode="per_cell",
                 nrand=500)
assert o2["ok"], o2
assert o2["n_samples"] == 40, o2["n_samples"]
sc = pd.read_csv(WS / Path(o2["scores_csv"]).relative_to("/ws"))
bg = sc[sc["kind"] == "beta_by_group"].pivot(
    index="factor", columns="sample", values="value")
assert set(bg.columns) == {"A", "B"}, list(bg.columns)
assert bg["A"].sort_values(ascending=False).index[0] == "TGFB1", \
    bg["A"].sort_values(ascending=False).head(3)
assert "by_group" in o2["note"], o2["note"]
print("② per_cell：40 细胞 + 群汇总 TGFB1@A 领跑 OK")

# ③ 无信号对照（探针判据②口径调整）：B 群纯噪声。20 细胞×43 因子
# =860 次检验，"≥1 击"因子数纯二项期望≈8（浅阈值无判别力）→ 改判
# 深度：无因子在 ≥5/20 个 B 细胞 p<0.01（真信号≈20/20，噪声 P≈1e-5）
pv = sc[sc["kind"] == "pvalue"].pivot(
    index="factor", columns="sample", values="value")
b_cells = [c for c in pv.columns if str(c).startswith("B")]
depth = (pv[b_cells] < 0.01).sum(axis=1)
assert depth.max() <= 4, f"B 群最深假阳性 {int(depth.max())}/20"
print(f"③ 无信号对照：B 群最深 {int(depth.max())}/20 细胞显著（≤4）"
      f"，≥1 击因子 {int((depth > 0).sum())}/43 OK")

# ④ mode 非法拒收
o4 = run_cytosig(dataset_ref=DS_MAIN, groupby="celltype", mode="bogus")
assert not o4["ok"] and o4["error_code"] == "INVALID_INPUT", o4
assert "unknown mode" in o4["error_message"], o4
print("④ mode 非法 INVALID_INPUT OK")

# ⑤ groupby 缺列拒收（带候选列提示）
o5 = run_cytosig(dataset_ref=DS_MAIN, groupby="not_a_col")
assert not o5["ok"] and o5["error_code"] == "INVALID_INPUT", o5
assert "candidate" in o5["error_message"] and "celltype" in o5["error_message"]
print("⑤ groupby 缺列 INVALID_INPUT + 候选提示 OK")

# ⑥ 签名交集 <500 拒收
o6 = run_cytosig(dataset_ref=DS_LOWOV, groupby="celltype")
assert not o6["ok"] and o6["error_code"] == "CYTOSIG_LOW_OVERLAP", o6
print("⑥ 非签名物种基因 CYTOSIG_LOW_OVERLAP OK")

print("\nSMOKE OK: sc_cytosig 6 场景全绿")
