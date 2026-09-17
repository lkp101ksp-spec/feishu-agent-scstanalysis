"""sc_cellchat 容器冒烟（Phase 47）：合成两群×两组真跑（断网）。

合成 480 细胞：ct(sender/receiver) × grp(A/B) 各 120；仅 A 组注入
强 LR 对（sender 高 CCL5、receiver 高 CCR5，consensus 库内），B 组
纯背景。liana assert_covered 要求资源 L/R 基因在 var_names 缺失比例
<=0.98——背景必须并入资源基因低表达（宿主无 liana，建库在容器内做）。
BioRunner 调用约定：docker run --rm --network none -v <workspace>:/ws
<img> python /opt/sc_tools/cellchat.py，stdin 传 args JSON。

断言：①rank_aggregate 单组检出 CCL5|CCR5 入 top；②group_col=grp
差异模式该对 up_in=A；③method 非法 / group_col 三值 → INVALID_INPUT。
"""
import json
import os
import subprocess
from pathlib import Path

import pandas as pd

# 宿主 workspace：默认本机路径，CI 冒烟（ci.yml bio-image-smoke job）
# 用 BIO_WORKSPACE_ROOT 指到 runner.temp——与 settings.bio_workspace_root
# 的 env 覆写口径一致
WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", "I:/飞书agent/bio_workspace"))
DS = "sccellchatsmoke"
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")
BASE = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG]

# 容器内建库（宿主无 liana，资源基因清单只能在容器里取）
BUILD = r"""
import os
import numpy as np
import pandas as pd
import anndata as ad
import liana as li

res = li.resource.select_resource("consensus")


def _split(s):
    genes = set()
    for v in s.astype(str):
        genes.update(v.split("_"))
    return genes


res_genes = sorted(_split(res["ligand"]) | _split(res["receptor"]))
genes = sorted(set(res_genes) | {"CCL5", "CCR5"})
gi = {g: i for i, g in enumerate(genes)}
rng = np.random.default_rng(0)
n = 120
blocks, rows = [], []
for grp in ("A", "B"):
    for ct in ("sender", "receiver"):
        X = rng.poisson(0.3, (n, len(genes))).astype(np.float32)
        if grp == "A" and ct == "sender":
            X[:, gi["CCL5"]] += rng.poisson(12, n)
        if grp == "A" and ct == "receiver":
            X[:, gi["CCR5"]] += rng.poisson(12, n)
        blocks.append(X)
        for i in range(n):
            rows.append({"ct": ct, "grp": grp,
                         "tri": f"t{i % 3}"})  # 三值列：非法 group_col 用
X = np.vstack(blocks)
obs = pd.DataFrame(rows, index=[f"c{i}" for i in range(X.shape[0])])
obs["ct"] = pd.Categorical(obs["ct"])
obs["grp"] = pd.Categorical(obs["grp"])
adata = ad.AnnData(X=X, obs=obs, var=pd.DataFrame(index=genes))
adata.raw = adata
ds = os.environ["DS_NAME"]
os.makedirs(f"/ws/{ds}", exist_ok=True)
adata.write_h5ad(f"/ws/{ds}/processed.h5ad")
print("built", X.shape)
"""


def run_cellchat(**kw):
    """容器内跑 cellchat.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": DS, **kw})
    r = subprocess.run(BASE + ["python", "/opt/sc_tools/cellchat.py"],
                       input=payload, capture_output=True, text=True,
                       timeout=1800)
    # 脚本级 fail() 也是 stdout JSON（BioRunner 同款口径）；
    # 仅当 stdout 无法解析为 JSON 才算执行崩溃
    try:
        return json.loads(r.stdout)  # BioRunner 严格口径：整体解析
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n"
                         f"{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# 建库（docker -e 传数据集名，stdin 喂 builder 源码）
r = subprocess.run(
    BASE[:6] + ["-e", f"DS_NAME={DS}"] + BASE[6:]
    + ["python", "-"],
    input=BUILD, capture_output=True, text=True, timeout=600)
assert r.returncode == 0 and "built" in r.stdout, (
    f"build failed\n{r.stdout[-400:]}\n{r.stderr[-800:]}")
ds_dir = WS / DS

# ① rank_aggregate 单组：注入对 CCL5|CCR5（sender->receiver）入 top
o1 = run_cellchat(celltype_col="ct", method="rank_aggregate")
assert o1["ok"] and o1["method"] == "rank_aggregate", o1
hit = [t for t in o1["top"]
       if t["ligand"] == "CCL5" and t["receptor"] == "CCR5"
       and t["source"] == "sender" and t["target"] == "receiver"]
assert hit, f"CCL5|CCR5 未入 top: {o1['top'][:3]}"
assert o1["n_sig"] > 0, o1

# ② group_col=grp 差异模式：该对 up_in=A
o2 = run_cellchat(celltype_col="ct", method="rank_aggregate",
                  group_col="grp")
assert o2["ok"] and o2["mode"] == "diff" and o2["groups"] == ["A", "B"], o2
diff = pd.read_csv(ds_dir / "cellchat_diff/diff_lr.csv")
row = diff[(diff["lr"] == "CCL5|CCR5") & (diff["pair"] == "sender->receiver")]
assert len(row) == 1, f"diff 缺注入对: {diff.head(3)}"
assert row.iloc[0]["up_in"] == "A" and row.iloc[0]["delta_score"] > 0, row
assert bool(row.iloc[0]["sig_g1"]), row

# ③ 非法输入：method 非枚举 / group_col 三值 → INVALID_INPUT
bad1 = run_cellchat(celltype_col="ct", method="nope")
assert not bad1["ok"] and bad1["error_code"] == "INVALID_INPUT", bad1
bad2 = run_cellchat(celltype_col="ct", group_col="tri")
assert not bad2["ok"] and bad2["error_code"] == "INVALID_INPUT", bad2

for f in ("cellchat/cellchat_lr.csv",
          "cellchat/cellchat_dotplot.png",
          "cellchat/cellchat_heatmap.png",
          "cellchat/A/cellchat_lr.csv",
          "cellchat/B/cellchat_lr.csv",
          "cellchat_diff/diff_lr.csv",
          "cellchat_diff/diff_heatmap.png"):
    assert (ds_dir / f).exists(), f
print("SMOKE OK",
      "| single top:", f"{hit[0]['ligand']}|{hit[0]['receptor']}",
      f"{hit[0]['source']}->{hit[0]['target']}",
      f"sig={hit[0]['sig_metric']:.1e}",
      "| diff up_in:", row.iloc[0]["up_in"],
      f"delta={row.iloc[0]['delta_score']:.2f}",
      f"n_sig A/B: {o2['n_sig_g1']}/{o2['n_sig_g2']}",
      "| invalid rejected")
