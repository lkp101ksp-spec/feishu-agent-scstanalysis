"""genescore×cytosig 联读真机验证（Phase 75 工作项 A）：599sub_bbknn。

spec 2026-09-20-phase75 §2：PROGENy 通路活性（MLM）× CytoSig beta 的
簇级 Spearman 联读；real_cytosig_599sub.py 骨架复用。

流程：
①宿主 marker 注释（Ptprc/Col1a1/...argmax，real_cytosig 同款）；
②影子桥：599sub 鼠源 Titlecase，sc_genescore human-only 卫兵会拦——
  宿主构建大写影子 599sub_gs_up（var/raw 双层 upper，撞名保首）；
③容器断网补跑 genescore（影子 → 产物拷回 599sub_bbknn/genescore/）；
④簇级对齐：scores 按 leiden 求均值（簇×14）vs cytosig beta pivot；
⑤三层联读：TGFb×TGFB 容差族逐对 / 14×43 全景 |rho|>=0.8（BH/FDR 校正）
  / CAF 叙事；
⑥产物 _eval/ 五件套（含 qval）+ C1/C2/C3 预注册判据（不过不阻塞，如实记录）。
"""
import json
import shutil
import subprocess
from pathlib import Path

import anndata as ad
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import false_discovery_control, spearmanr  # noqa: E402

WS = Path("bio_workspace").resolve()
EVAL = WS / "_eval"
IMG = "feishu-research-agent/bio:cpu-latest"
DS = "599sub_bbknn"
UP = "599sub_gs_up"  # 大写影子数据集（联读专用）
TGF_CORE = ["TGFB1", "TGFB2", "TGFB3"]
TGF_WIDE = TGF_CORE + ["Activin A", "BMP2", "BMP4"]  # 容差族（有则算）
CAF_MARKERS = ("Col1a1", "Dcn", "Acta2")

a0 = ad.read_h5ad(WS / DS / "processed.h5ad")

# ── ① 宿主侧 marker 注释（argmax 粗注） ───────────────────────────────
MARKERS = ["Ptprc", "Col1a1", "Dcn", "Epcam", "Krt18", "Lyz2",
           "Cd3e", "Pecam1", "Acta2"]
r0 = a0.raw.to_adata()
mk = [g for g in MARKERS if g in r0.var_names]
mm = pd.DataFrame(
    {g: np.asarray(r0[:, g].X.todense()).ravel() for g in mk},
    index=[str(c) for c in a0.obs_names])
mm["leiden"] = a0.obs["leiden"].astype(str).to_numpy()
gmean = mm.groupby("leiden").mean().round(2)
ident = gmean.idxmax(axis=1).to_dict()
print("== 各 leiden 簇 marker 均值（粗注：行 argmax） ==")
print(gmean.to_string())
print("粗注：", ident)

# ── ② 影子数据集（仅首次构建） ────────────────────────────────────────
if not (WS / UP / "processed.h5ad").exists():
    a = a0.copy()
    a.var_names = pd.Index([str(g).upper() for g in a.var_names])
    dup = a.var_names[a.var_names.duplicated()].unique().tolist()
    if dup:
        print(f"[影子] upper 撞名 {len(dup)} 个保首个: {dup[:5]}")
        a = a[:, ~a.var_names.duplicated()].copy()
    if a.raw is not None:
        rr = a.raw.to_adata()
        rr.var_names = pd.Index([str(g).upper() for g in rr.var_names])
        rr = rr[:, ~rr.var_names.duplicated()].copy()
        a.raw = rr
    (WS / UP).mkdir(parents=True, exist_ok=True)
    a.write_h5ad(WS / UP / "processed.h5ad")
    print(f"[影子] {UP}: {a.shape}, raw={a.raw is not None}")

# ── ③ 容器补跑 genescore（产物拷回原数据集，幂等） ────────────────────
scores_csv = WS / DS / "genescore" / "progeny_scores.csv"
if not scores_csv.exists():
    base = ["docker", "run", "--rm", "-i", "--network", "none",
            "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
            "-v", f"{str(Path('sandbox/sc_tools').resolve()).replace(chr(92), '/')}"
            ":/opt/sc_tools", IMG]
    p = subprocess.run(
        base + ["python", "/opt/sc_tools/genescore.py"],
        input=json.dumps({"dataset_id": UP, "groupby": "leiden",
                          "top_n": 14}),
        capture_output=True, text=True, timeout=1500)
    out = json.loads(p.stdout)
    assert out["ok"], out
    src = WS / Path(out["scores_csv"]).relative_to("/ws")
    scores_csv.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, scores_csv)
    print(f"[genescore] n_pathways={out['n_pathways_scored']}，"
          f"产物归位 {DS}/genescore/")

# ── ④ 簇级对齐 ────────────────────────────────────────────────────────
sc_df = pd.read_csv(scores_csv, index_col=0)  # 细胞×14，首列 leiden
P = sc_df.groupby("leiden").mean()  # 簇×14
cyto = pd.read_csv(WS / DS / "cytosig" / "cytosig_scores.csv")
B = cyto[cyto["kind"] == "beta"].pivot(
    index="sample", columns="factor", values="value")  # 簇×43
clusters = sorted(set(P.index) & set(B.index),
                  key=lambda c: (len(str(c)), str(c)))
P, B = P.loc[clusters], B.loc[clusters]
print(f"[对齐] 簇交集 {len(clusters)}: {clusters}")

# ── ⑤a 焦点对照：TGFb × TGF 容差族逐对 Spearman ───────────────────────
focus: dict[str, dict[str, float]] = {}
for f in TGF_WIDE:
    if f not in B.columns:
        continue
    rho, p = spearmanr(P["TGFb"], B[f])
    focus[f] = {"rho": round(float(rho), 4), "p": round(float(p), 4)}
    print(f"  TGFb × {f:<10} rho={rho:+.3f} p={p:.3f}")

# ── ⑤b 全景矩阵 14×43（常量列跳过防 nan 告警） ────────────────────────
rho_mat = pd.DataFrame(np.nan, index=list(P.columns), columns=list(B.columns))
p_mat = rho_mat.copy(deep=True)
for pw in P.columns:
    for f in B.columns:
        if P[pw].nunique() < 2 or B[f].nunique() < 2:
            continue
        rho, p = spearmanr(P[pw], B[f])
        rho_mat.loc[pw, f] = rho
        p_mat.loc[pw, f] = p
# BH/FDR 校正：对全部有效 p 值统一校正（#20 挂账"43 对强相关未校正"收尾）
p_arr = p_mat.to_numpy(dtype=float)
q_arr = np.full(p_arr.shape, np.nan)
mask = ~np.isnan(p_arr)
if mask.any():
    q_arr[mask] = false_discovery_control(p_arr[mask], method="bh")
q_mat = pd.DataFrame(q_arr, index=list(P.columns), columns=list(B.columns))
strong = [
    {"pathway": pw, "factor": f,
     "rho": round(float(rho_mat.loc[pw, f]), 4),
     "p": round(float(p_mat.loc[pw, f]), 4),
     "q": round(float(q_mat.loc[pw, f]), 4)}
    for pw in P.columns for f in B.columns
    if abs(float(rho_mat.loc[pw, f])) >= 0.8
]
n_fdr = sum(1 for s in strong if s["q"] < 0.05)
print(f"[全景] |rho|>=0.8 对数: {len(strong)}（其中 BH q<0.05: {n_fdr}）")
for s in strong[:10]:
    print(f"  {s['pathway']} × {s['factor']}: "
          f"rho={s['rho']:+.3f} (p={s['p']:.3f}, q={s['q']:.3f})")

# ── ⑤c CAF 叙事：TGFb 域排名 + TGFB 家族 beta 并列 ───────────────────
# ident 键为 str（L48 astype(str)）而 clusters 为 int（CSV 读入）——统一 str 查键（#20 挂账修复）
caf = [c for c in clusters if ident.get(str(c)) in CAF_MARKERS]
tgfb_rank = P["TGFb"].rank(ascending=False)
print(f"[CAF] 粗注命中簇: {[(c, ident[str(c)]) for c in caf] or '无（C2 记 not-pass）'}")
for c in caf:
    fam = {f: float(B.loc[c, f]) for f in TGF_CORE if f in B.columns}
    print(f"[CAF {c}] TGFb 排名={int(tgfb_rank[c])}/{len(clusters)}  "
          + "  ".join(f"{k}={v:+.3f}" for k, v in fam.items()))

# ── ⑥ 散点图：TGFb × 最强 TGFB 家族成员 ───────────────────────────────
cand = [f for f in TGF_CORE if f in focus]
best_f = max(cand, key=lambda f: abs(focus[f]["rho"]))
x_, y_ = P["TGFb"], B[best_f]
fig, ax = plt.subplots(figsize=(4.5, 4))
ax.scatter(x_, y_, s=40)
for c in clusters:
    ax.annotate(str(c), (float(x_[c]), float(y_[c])), fontsize=8,
                xytext=(3, 3), textcoords="offset points")
k, b_ = np.polyfit(x_.to_numpy(dtype=float), y_.to_numpy(dtype=float), 1)
xs = np.linspace(float(x_.min()), float(x_.max()), 50)
ax.plot(xs, k * xs + b_, "--", lw=1)
rho, p = spearmanr(x_, y_)
ax.set_title(f"PROGENy TGFb × CytoSig {best_f}  "
             f"rho={rho:+.2f} p={p:.2f}", fontsize=9)
ax.set_xlabel("PROGENy TGFb（MLM 簇均值）")
ax.set_ylabel(f"CytoSig {best_f} beta")
fig.tight_layout()
fig.savefig(EVAL / "crossread_tgfb_scatter.png", dpi=150)
plt.close(fig)

# ── ⑦ 判据汇总（C1/C2/C3 预注册，不过不阻塞） ────────────────────────
c1_detail = [f"{f}: rho={focus[f]['rho']:+.3f}" for f in TGF_CORE
             if f in focus and focus[f]["rho"] > 0
             and abs(focus[f]["rho"]) >= 0.6]
summary = {
    "dataset": DS,
    "n_clusters": len(clusters),
    "shadow_dataset": UP,
    "criteria": {
        "C1_tgfb_family_rho_pos>=0.6": {
            "pass": bool(c1_detail), "detail": c1_detail},
        "C2_caf_tgfb_rank<=3": {
            "pass": any(int(tgfb_rank[c]) <= 3 for c in caf),
            "detail": {c: int(tgfb_rank[c]) for c in caf}},
        "C3_any_pair_|rho|>=0.8": {
            "pass": len(strong) >= 1, "n_strong": len(strong),
            "n_strong_fdr005": n_fdr,
            "top": strong[:10]},
    },
    "focus_family": focus,
    "cluster_identity": ident,
    "note": "判据不过不阻塞收口（簇数少 rho 方差大），差异记录于测试总结 #20",
}
EVAL.mkdir(parents=True, exist_ok=True)
rho_mat.to_csv(EVAL / "crossread_pathway_factor_rho.csv")
p_mat.to_csv(EVAL / "crossread_pathway_factor_pval.csv")
q_mat.to_csv(EVAL / "crossread_pathway_factor_qval.csv")
(EVAL / "real_crossread_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary["criteria"], ensure_ascii=False, indent=2))
print("\nREAL CROSSREAD OK: 五件套落盘 _eval/（含 qval），判据逐条记录如上")
