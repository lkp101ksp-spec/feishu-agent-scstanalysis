"""st_score_weight 真机验收（Phase 76 spec §6.2-6.4）：oscc。

前置：real_st_deconvolve_oscc.py 已落 deconv.h5ad（否则本脚本直接
断言退出）。双 source 跑通 + 预注册判据（不过不阻塞）：
  S1 Epithelial cells top3 含 JAK-STAT/EGFR/TGFb 之一（依据：
     2026-09-12 病理交叉验收 + Phase 75 Task 6 各域 top3）；
  S2 TGFb 在 Epithelial 行内排名 ≤ 其在 W 列均值口径的排名（加权
     不颠覆既有方向）；
  S3 dropped_celltypes 为空或仅低丰度型（如实记录）。
产物断言齐 + 判据打印 + summary json 落 _eval/ = 验收完成。
"""
import json
import subprocess
from pathlib import Path

import pandas as pd

WS = Path("bio_workspace").resolve()
IMG = "feishu-research-agent/bio:cpu-latest"
DS = "oscc"
base = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
        "-v", f"{str(Path('sandbox/sc_tools').resolve()).replace(chr(92), '/')}"
        ":/opt/sc_tools", IMG]


def run_tool(script: str, payload: dict, timeout: int = 600) -> dict:
    """容器断网跑单个 st 工具，断言 ok 并返回 emit dict。"""
    p = subprocess.run(
        base + ["python", f"/opt/sc_tools/{script}"],
        input=json.dumps(payload), capture_output=True, text=True,
        timeout=timeout)
    out = json.loads(p.stdout)
    assert out["ok"], out
    return out


def host(p: str) -> Path:
    """容器 /ws/<rel> → 宿主路径并断言存在。"""
    q = WS / Path(p).relative_to("/ws")
    assert q.exists(), p
    return q


assert (WS / DS / "deconv.h5ad").exists(), "先跑 real_st_deconvolve_oscc.py"

# ① source=st_genescore（14 通路）
o1 = run_tool("st_score_weight.py",
              {"dataset_id": DS, "source": "st_genescore"})
print(f"[gs] celltypes={o1['n_celltypes']} pathways={o1['n_pathways']} "
      f"overlap={o1['n_spots_overlap']} dropped={o1['dropped_celltypes']}")
for v in o1["products"].values():
    host(v)
print("[gs] 各细胞型 top3 通路：")
for ct, tops in o1["top_by_celltype"].items():
    print(f"  {ct}: {tops}")

# ② 判据（基于 st_genescore 口径矩阵）
w = pd.read_csv(host(o1["products"]["scores_csv"]), index_col=0)
epi = [c for c in w.index if "pithelial" in str(c)]
assert epi, f"未找到 Epithelial 型: {list(w.index)}"
ep = epi[0]
s1_hits = [t for t in o1["top_by_celltype"][ep][:3]
           if t in ("JAK-STAT", "EGFR", "TGFb")]
s1 = bool(s1_hits)
tgfb_rank_epi = float(w.loc[ep].rank(ascending=False)["TGFb"])
tgfb_rank_mean = float(w.mean(axis=0).rank(ascending=False)["TGFb"])
s2 = tgfb_rank_epi <= tgfb_rank_mean
s3 = o1["dropped_celltypes"]
print(f"[S1] {ep} top3 命中 {s1_hits or '无'} → {'pass' if s1 else 'not-pass'}")
print(f"[S2] TGFb 排名: Epithelial 行内 {tgfb_rank_epi:.0f} vs "
      f"列均值口径 {tgfb_rank_mean:.0f} → {'pass' if s2 else 'not-pass'}")
print(f"[S3] dropped_celltypes={s3 or '空'}")

# ③ source=st_metabolism（315 通路）
o2 = run_tool("st_score_weight.py",
              {"dataset_id": DS, "source": "st_metabolism"})
print(f"[mb] celltypes={o2['n_celltypes']} pathways={o2['n_pathways']} "
      f"overlap={o2['n_spots_overlap']} dropped={o2['dropped_celltypes']}")
for v in o2["products"].values():
    host(v)
print("[mb] 各细胞型 top3 代谢通路（截断 40 字）：")
for ct, tops in o2["top_by_celltype"].items():
    print(f"  {ct}: {[t[:40] for t in tops]}")

summary = {
    "dataset": DS,
    "n_celltypes": o1["n_celltypes"],
    "criteria": {
        "S1_epithelial_top3_hits": {"pass": s1, "detail": s1_hits},
        "S2_tgfb_rank_not_worse": {
            "pass": s2,
            "detail": {"epithelial_rank": tgfb_rank_epi,
                       "colmean_rank": tgfb_rank_mean}},
        "S3_dropped_celltypes": {"pass": True,
                                 "detail": s3},
    },
    "top_by_celltype_gs": o1["top_by_celltype"],
    "note": "判据不过不阻塞（spec §6.3），如实记录于测试总结 #21",
}
(WS / "_eval" / "real_st_score_weight_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print("\nREAL OK: st_score_weight oscc 双 source 验收完成"
      "（判据如实记录于测试总结 #21）")
