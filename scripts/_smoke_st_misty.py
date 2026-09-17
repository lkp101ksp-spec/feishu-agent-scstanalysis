"""st_misty 容器冒烟：合成双区组成 + 结构化 HVG 真跑（断网）。

12×12 网格：T cells 左高、Tumor 右高渐变（deconv.h5ad 模拟
st_deconvolve 产物）；processed.h5ad 含 60 个基因，g0 表达与 Tumor
组成正相关（结构信号——纯噪声 gain_R2=0 甚至为负，探针实测，断言
只查结构与存在性不查数值下界）。调用走 BioRunner 约定：
docker run --rm -i --network none -v <workspace>:/ws <img> python
/opt/st_tools/misty.py，stdin 传 args JSON。
"""
import csv as _csv
import json
import os
import subprocess
from pathlib import Path

# 宿主 workspace/镜像：env 覆写（CI 冒烟用，与 settings 同口径）
WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", "I:/飞书agent/bio_workspace"))
DS = "stmistysmoke"
DS_NODEC = "stmistynodec"
DS_PROG = "stmistyprog"
DS_TF = "stmistytf"
IMG = os.environ.get("BIO_ST_IMAGE", "feishu-research-agent/bio:st-cpu-latest")
BASE = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG]

_BUILDER = r'''
import numpy as np
import pandas as pd
import anndata as ad

rng = np.random.default_rng(42)
xs, ys = np.meshgrid(np.arange(12), np.arange(12))
coords = np.column_stack([xs.ravel(), ys.ravel()]).astype(float)
n = coords.shape[0]
x = coords[:, 0]
tumor_frac = x / 11.0  # 0→1 渐变
abund = np.column_stack([
    (1 - tumor_frac) * 8 + 0.1,  # T cells 左高
    tumor_frac * 8 + 0.1,        # Tumor 右高
    np.full(n, 1.0),             # Fibroblast 均匀
]).astype(np.float32)
barcodes = [f"s{i}" for i in range(n)]
dec = ad.AnnData(X=np.zeros((n, 3), dtype=np.float32),
                 obs=pd.DataFrame(index=barcodes),
                 var=pd.DataFrame(index=["a", "b", "c"]))
dec.obsm["q05_cell_abundance_w_sf"] = pd.DataFrame(
    abund, index=barcodes,
    columns=["q05cell_abundance_w_sf_T cells",
             "q05cell_abundance_w_sf_Tumor",
             "q05cell_abundance_w_sf_Fibroblast"])
dec.write_h5ad("/ws/stmistysmoke/deconv.h5ad")
# processed：60 基因 log 表达，g0 与 Tumor 组成正相关（结构信号）。
# 梯度须加在 log1p 之后：log 变换对泊松噪声是方差稳定化的，
# 计数空间加的梯度经 log1p 压缩后总方差反低于纯噪声基因（实测
# g0 方差仅排 35/60 而落选 HVG）；log 空间加梯度可稳居前。
genes = [f"g{i}" for i in range(60)]
X = rng.poisson(1.5, (n, 60)).astype(np.float32)
X = np.log1p(X)
X[:, 0] += (tumor_frac * 2.5).astype(np.float32)  # g0 随 Tumor 增
proc = ad.AnnData(X=X, obs=pd.DataFrame(index=barcodes),
                  var=pd.DataFrame(index=genes))
proc.obsm["spatial"] = coords
proc.write_h5ad("/ws/stmistysmoke/processed.h5ad")
proc2 = ad.AnnData(X=X.copy(), obs=pd.DataFrame(index=barcodes),
                   var=pd.DataFrame(index=genes))
proc2.obsm["spatial"] = coords
proc2.write_h5ad("/ws/stmistynodec/processed.h5ad")
# stmistyprog：真实 PROGENy 基因名，EGFR target 按 weight×tumor 梯度
# 注入（探针 B 同款，corr=0.998 实测可回收；此处无 log1p 中间变换，
# 直接加在最终表达矩阵上即有效——教训十六）
net = pd.read_csv("/opt/progeny/progeny_human_top500.tsv", sep="\t")
egfr = net[net["source"] == "EGFR"][["target", "weight"]].drop_duplicates(
    "target")
others = sorted(set(net["target"]) - set(egfr["target"]))
fill = np.random.default_rng(7).choice(others, size=150,
                                       replace=False).tolist()
genes2 = egfr["target"].tolist() + fill
g2idx = pd.Index(genes2)
X2 = np.random.default_rng(0).normal(0, 1, (n, len(genes2))).astype(
    np.float32)
cols = g2idx.get_indexer(egfr["target"])
X2[:, cols] += (tumor_frac * 3).astype(np.float32)[:, None] * \
    egfr["weight"].to_numpy(dtype=np.float32)[None, :] * 0.5
proc3 = ad.AnnData(X=X2, obs=pd.DataFrame(index=barcodes),
                   var=pd.DataFrame(index=genes2))
proc3.obsm["spatial"] = coords
proc3.write_h5ad("/ws/stmistyprog/processed.h5ad")
dec.write_h5ad("/ws/stmistyprog/deconv.h5ad")
# stmistytf：真实 CollecTRI 基因名，DDIT3 靶基因按 weight×tumor 梯度
# 注入（探针 B 同款配方，corr=0.9901 实测可回收）；MLM 秩约束要求
# 交集靶基因 > TF 协变量数——fill 取 4000 个真实靶基因兜底
cnet = pd.read_csv("/opt/collectri/collectri_human.tsv", sep="\t")
dd = cnet[cnet["source"] == "DDIT3"][["target", "weight"]].drop_duplicates(
    "target")
cothers = sorted(set(cnet["target"]) - set(dd["target"]))
cfill = np.random.default_rng(11).choice(cothers, size=4000,
                                         replace=False).tolist()
genes3 = dd["target"].tolist() + cfill
g3idx = pd.Index(genes3)
X3 = np.random.default_rng(1).normal(0, 1, (n, len(genes3))).astype(
    np.float32)
cols3 = g3idx.get_indexer(dd["target"])
X3[:, cols3] += (tumor_frac * 3).astype(np.float32)[:, None] * \
    dd["weight"].to_numpy(dtype=np.float32)[None, :] * 0.5
proc4 = ad.AnnData(X=X3, obs=pd.DataFrame(index=barcodes),
                   var=pd.DataFrame(index=genes3))
proc4.obsm["spatial"] = coords
proc4.write_h5ad("/ws/stmistytf/processed.h5ad")
dec.write_h5ad("/ws/stmistytf/deconv.h5ad")
print("built", n)
'''


def build_dataset() -> None:
    """容器内构造合成数据集（含无 deconv 对照）。"""
    bdir = WS / "_builder_stmisty"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "build_data.py").write_text(_BUILDER, encoding="utf-8")
    (WS / DS).mkdir(parents=True, exist_ok=True)
    (WS / DS_NODEC).mkdir(parents=True, exist_ok=True)
    (WS / DS_PROG).mkdir(parents=True, exist_ok=True)
    (WS / DS_TF).mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", "none",
         "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
         IMG, "python", "/ws/_builder_stmisty/build_data.py"],
        capture_output=True, timeout=600)
    assert out.returncode == 0, out.stderr.decode()[-2000:]


def run_misty(ds: str, **kw):
    """容器内跑 misty.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": ds, **kw})
    r = subprocess.run(BASE + ["python", "/opt/st_tools/misty.py"],
                       input=payload, capture_output=True, text=True,
                       timeout=1800)
    # 脚本级 fail() 也是 exit 1 + stdout JSON（BioRunner 同款口径）；
    # 仅当 stdout 无法解析为 JSON 才算执行崩溃
    try:
        return json.loads(r.stdout)  # BioRunner 严格口径：整体解析
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n"
                         f"{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


build_dataset()

o = run_misty(DS, n_hvg=30, bandwidth=2.0)
assert o["ok"], o
assert o["n_targets"] == 3, o
assert o["n_predictors"] == 30, o
assert o["bandwidth"] == 2.0, o
assert set(o["top_interactions"]) == {"T cells", "Tumor", "Fibroblast"}, o
assert len(o["pngs"]) == 2
ds_dir = WS / DS
for f in ("misty/misty_contributions.png", "misty/misty_interactions_para.png",
          "misty/misty_target_metrics.csv", "misty/misty_interactions.csv"):
    assert (ds_dir / f).exists(), f

# 结构信号：g0 应进入 Tumor 的 para top3 预测子（csv 回读）
with open(ds_dir / "misty/misty_interactions.csv", newline="") as fh:
    rows = list(_csv.DictReader(fh))
para_tumor = [r for r in rows
              if r["view"] == "para" and r["target"] == "Tumor"]
para_tumor.sort(key=lambda r: float(r["importances"]), reverse=True)
top3 = {r["predictor"] for r in para_tumor[:3]}
assert "g0" in top3, f"g0 not in para top3 for Tumor: {top3}"

# n_hvg 越界 → INVALID_INPUT
bad = run_misty(DS, n_hvg=5)
assert not bad["ok"] and bad["error_code"] == "INVALID_INPUT", bad

# 无 deconv.h5ad → ST_MISTY_NO_DECONV
no_dec = run_misty(DS_NODEC, n_hvg=30)
assert not no_dec["ok"] and no_dec["error_code"] == "ST_MISTY_NO_DECONV", no_dec

# progeny 主跑：extra=PROGENy 14 通路活性，EGFR 信号应回收
p = run_misty(DS_PROG, extra_mode="progeny", bandwidth=2.0)
assert p["ok"], p
assert p["extra_mode"] == "progeny", p
assert p["n_predictors"] == 14, p
assert p["n_targets"] == 3, p
with open(WS / DS_PROG / "misty/misty_interactions.csv",
          newline="") as fh:
    prows = list(_csv.DictReader(fh))
pt = [r for r in prows
      if r["view"] == "para" and r["target"] == "Tumor"]
pt.sort(key=lambda r: float(r["importances"]), reverse=True)
ptop3 = {r["predictor"] for r in pt[:3]}
assert "EGFR" in ptop3, f"EGFR not in para top3 for Tumor: {ptop3}"

# 基因名零交集（stmistysmoke 的 g0..g59 非 symbol）→ INVALID_INPUT
no_ov = run_misty(DS, extra_mode="progeny")
assert not no_ov["ok"] and no_ov["error_code"] == "INVALID_INPUT", no_ov

# tf 主跑：extra=CollecTRI TF 活性，DDIT3 信号应回收；热图 top30 截断
t = run_misty(DS_TF, extra_mode="tf", bandwidth=2.0)
assert t["ok"], t
assert t["extra_mode"] == "tf", t
assert t["n_predictors_total"] > 500, t
assert t["n_predictors_shown"] == 30, t
with open(WS / DS_TF / "misty/misty_interactions.csv", newline="") as fh:
    trows = list(_csv.DictReader(fh))
tt = [r for r in trows
      if r["view"] == "para" and r["target"] == "Tumor"]
tt.sort(key=lambda r: float(r["importances"]), reverse=True)
ttop30 = {r["predictor"] for r in tt[:30]}
assert "DDIT3" in ttop30, \
    f"DDIT3 not in para top30 for Tumor: {[r['predictor'] for r in tt[:5]]}"

# tf 模式基因名零交集（g0..g59 非 symbol）→ INVALID_INPUT
no_ov_tf = run_misty(DS, extra_mode="tf")
assert not no_ov_tf["ok"] and no_ov_tf["error_code"] == "INVALID_INPUT", \
    no_ov_tf

# 非法 extra_mode → INVALID_INPUT
bad_mode = run_misty(DS, extra_mode="bogus")
assert not bad_mode["ok"] and bad_mode["error_code"] == "INVALID_INPUT", \
    bad_mode

print("SMOKE OK | targets:", o["n_targets"],
      "| mean_gain_R2:", o["mean_gain_R2"],
      "| g0 recovered in Tumor para top3",
      "| EGFR recovered in progeny para top3 (n_predictors=%d)"
      % p["n_predictors"],
      "| DDIT3 recovered in tf para top30 (total=%d)"
      % t["n_predictors_total"],
      "| INVALID_INPUT/NO_DECONV/bad-mode/no-overlap rejected")
