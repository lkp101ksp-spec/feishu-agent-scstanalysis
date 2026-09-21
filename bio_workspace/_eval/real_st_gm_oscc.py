"""st_genescore / st_metabolism 真机验收（Phase 75 工作项 B）：oscc。

spec §4：groupby=spatial_domain、species=human 双工具跑 oscc——
①st_genescore：spot 级 14 通路 + 域均值热图 + 空间着色图；与 sc 版
  （cluster_annotations 口径）TGFb/JAK-STAT 排名方向对照（一致即
  通过，不一致如实记录进测试总结 #20）；
②st_metabolism：域级代谢热图 + top1 通路空间图。
产物断言齐 + 两口径对照打印 = 验收通过。
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


def run_tool(script: str, payload: dict) -> dict:
    """容器断网跑单个 st 工具，断言 ok 并返回 emit dict。"""
    p = subprocess.run(
        base + ["python", f"/opt/sc_tools/{script}"],
        input=json.dumps(payload), capture_output=True, text=True,
        timeout=1800)
    out = json.loads(p.stdout)
    assert out["ok"], out
    return out


def host(p: str) -> Path:
    """容器 /ws/<rel> → 宿主路径并断言存在。"""
    q = WS / Path(p).relative_to("/ws")
    assert q.exists(), p
    return q


# ① st_genescore（spatial_domain 口径）
o1 = run_tool("st_genescore.py",
              {"dataset_id": DS, "groupby": "spatial_domain", "top_n": 14})
print(f"[st_genescore] spots={o1['n_spots']} domains={o1['n_domains']} "
      f"pathways={o1['n_pathways_scored']}")
for v in o1["products"].values():
    if v:
        host(v)
gm = pd.read_csv(host(o1["products"]["group_mean_csv"]), index_col=0)
print("[st_genescore] 各域 top3 通路：")
for d, tops in o1["top_by_group"].items():
    print(f"  {d}: {tops}")

# 两口径对照：sc 版 groupby=cluster_annotations（Phase 72 产物）
sc_gm = pd.read_csv(WS / DS / "genescore" / "progeny_group_mean.csv",
                    index_col=0)
for pw in ("TGFb", "JAK-STAT"):
    st_top = gm[pw].idxmax() if pw in gm.columns else "?"
    sc_top = sc_gm[pw].idxmax() if pw in sc_gm.columns else "?"
    print(f"[对照] {pw}: st(spatial_domain) top={st_top} "
          f"vs sc(cluster_annotations) top={sc_top}")

# ② st_metabolism（AUCell 默认口径）
o2 = run_tool("st_metabolism.py",
              {"dataset_id": DS, "method": "aucell",
               "groupby": "spatial_domain", "species": "human"})
print(f"[st_metabolism] spots={o2['n_spots']} "
      f"pathways={o2['n_pathways_scored']} domains={o2['n_domains']}")
for v in o2["products"].values():
    if v:
        host(v)
print("[st_metabolism] 各域 top3 通路（截断 50 字）：")
for d, tops in o2["top_by_group"].items():
    print(f"  {d}: {[t[:50] for t in tops]}")

print("\nREAL OK: st_genescore/st_metabolism oscc 真机验收全绿"
      "（两口径对照结论记录于测试总结 #20）")
