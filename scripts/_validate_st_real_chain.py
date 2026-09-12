"""真实 Visium 全链验证（OSCC_sample2，1749 spots，病理标注作金标准）。

链路（真实 BioRunner + st 镜像，与生产同路径）：
st_load → st_qc → st_process → st_stats → st_domains → st_markers →
st_deconvolve → st_cnv → st_niche → st_vicinity →
st_misty(hvg/progeny/tf 三模式) → st_trajectory(vicinity root)。

逐步断言无 error_code 并计时；末尾容器内交叉对照：
- spatial_domain vs pathologist_anno ARI（ domains 生物合理性）
- deconv Epithelial cells 丰度在 SCC vs 非 SCC 的富集（MWU + fold）
- st_cnv is_malignant 在 SCC 的富集（Fisher OR）
- trajectory dpt vs vicinity 层序 spearman
报告写 bio_workspace/_validate_real/report.json。

用法：.venv\\Scripts\\python.exe scripts\\_validate_st_real_chain.py
  [--epochs N]（deconv max_epochs，默认 10000；需 Docker + st 镜像）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_settings
from orchestrator.tools.bio.bio_runner import BioRunner
from orchestrator.tools.builtin.l3_spatial import register_l3_spatial
from orchestrator.tools.tool_registry import ToolRegistry

WS = Path("bio_workspace")
REPORT_DIR = WS / "_validate_real"

_CROSS = r'''
import json
import anndata as ad
import numpy as np
from scipy.stats import fisher_exact, mannwhitneyu, spearmanr
from sklearn.metrics import adjusted_rand_score

ds = open("/ws/_validate_real/ds_id.txt").read().strip()
proc = ad.read_h5ad(f"/ws/{ds}/processed.h5ad")
dec = ad.read_h5ad(f"/ws/{ds}/deconv.h5ad")
out = {}

pa = proc.obs["pathologist_anno.x"].astype(str)
keep = pa != "NA"
scc = (pa == "SCC").to_numpy()

out["ari_domains_vs_pathologist"] = round(float(adjusted_rand_score(
    pa[keep], proc.obs.loc[keep, "spatial_domain"].astype(str))), 4)

epi = dec.obs["Epithelial cells"].to_numpy()
s, p = mannwhitneyu(epi[scc], epi[~scc], alternative="greater")
out["epi_scc_fold"] = round(float(epi[scc].mean()
                                  / max(epi[~scc].mean(), 1e-9)), 3)
out["epi_scc_mwu_p"] = float(p)

mal = proc.obs["is_malignant"].astype(bool).to_numpy()
tab = [[int((mal & scc).sum()), int((mal & ~scc).sum())],
       [int((~mal & scc).sum()), int((~mal & ~scc).sum())]]
odds, p_f = fisher_exact(tab)
out["malignant_frac_scc"] = round(float(mal[scc].mean()), 4)
out["malignant_frac_non_scc"] = round(float(mal[~scc].mean()), 4)
out["malignant_scc_fisher_or"] = round(float(odds), 3)
out["malignant_scc_fisher_p"] = float(p_f)

vic = proc.obs["vicinity"].astype(str)
order = {"tumor": 0}
vic_ord = vic.map(lambda v: order.get(v, int(v[1:]) if v.startswith("L")
                                          else float("nan")))
valid = vic_ord.notna().to_numpy()
rho, p_s = spearmanr(proc.obs["dpt_pseudotime"].to_numpy()[valid],
                     vic_ord.to_numpy()[valid])
out["dpt_vs_vicinity_spearman"] = round(float(rho), 4)
out["dpt_vs_vicinity_p"] = float(p_s)

print(json.dumps(out, ensure_ascii=False))
'''


def _step(name: str, results: dict, fn, **kw):
    """跑一步链路：断言无 error_code，计时并记录关键输出。"""
    t0 = time.time()
    out = fn(**kw)
    dt = time.time() - t0
    if "error_code" in out:
        print(f"[FAIL] {name} ({dt:.0f}s): {out['error_code']} | "
              f"{out['error_message'][:300]}")
        print(json.dumps(results, ensure_ascii=False, indent=1))
        raise SystemExit(1)
    slim = {k: v for k, v in out.items()
            if k in ("dataset_ref", "n_spots", "n_genes", "n_domains",
                     "n_cell_types", "n_types", "n_malignant",
                     "malignant_frac", "n_niches", "n_predictors",
                     "n_predictors_total", "n_targets", "mean_gain_R2",
                     "spearman_rho", "n_disconnected", "ref_source",
                     "n_kept", "n_removed")}
    results[name] = {"sec": round(dt, 1), **slim}
    print(f"[ok] {name} ({dt:.0f}s) {slim}")
    return out


def main() -> None:
    """真实数据全链驱动：逐步跑 14 工具并落交叉对照报告。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=10000)
    ap.add_argument("--deconv-timeout", type=int, default=7200)
    ap.add_argument("--start-from", default="st_load",
                    help="从指定步骤续跑（产物已在盘上时跳过前序）")
    args = ap.parse_args()

    settings = load_settings()
    roots = [r.strip() for r in (settings.bio_data_roots or "").split(",")
             if r.strip()]
    runner = BioRunner(
        image=settings.bio_image,
        workspace_root=settings.bio_workspace_root,
        data_roots=roots,
        timeout_sec=settings.bio_script_timeout_sec,
        cpus=settings.bio_cpus,
        memory=settings.bio_memory,
    )
    reg = ToolRegistry()
    register_l3_spatial(
        reg, runner, st_image=settings.bio_st_image,
        st_deconvolve_timeout=args.deconv_timeout)
    h = lambda n: reg.get(n).handler  # noqa: E731

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    res: dict = {}

    seq: list[tuple[str, str, dict]] = [
        ("st_load", "st_load",
         {"path": r"I:\飞书agent\bio_test_data\oscc_visium.h5ad"}),
        ("st_qc", "st_qc", {"max_genes": 10000}),
        ("st_process", "st_process", {}),
        ("st_stats", "st_stats",
         {"analysis": "autocorr", "mode": "moran",
          "genes": ["EPCAM", "PTPRC", "COL1A1"]}),
        ("st_domains", "st_domains", {"method": "banksy"}),
        ("st_markers", "st_markers", {}),
        ("st_deconvolve", "st_deconvolve",
         {"sc_ref": r"I:\飞书agent\bio_test_data\oscc_sc_ref_sub.h5ad",
          "ref_label_col": "cell_type", "max_epochs": args.epochs}),
        ("st_cnv", "st_cnv",
         {"annotation_key": "pathologist_anno.x",
          "ref_groups": ["Non-cancerous Mucosa"]}),
        ("st_niche", "st_niche", {}),
        ("st_vicinity", "st_vicinity", {}),
        ("st_misty_hvg", "st_misty", {"extra_mode": "hvg"}),
        ("st_misty_progeny", "st_misty", {"extra_mode": "progeny"}),
        ("st_misty_tf", "st_misty", {"extra_mode": "tf"}),
        ("st_trajectory", "st_trajectory",
         {"root_mode": "vicinity", "root_layer": "tumor"}),
    ]
    names = [s[0] for s in seq]
    if args.start_from not in names:
        print(f"--start-from 需为 {names} 之一")
        raise SystemExit(2)
    ds = ((REPORT_DIR / "ds_id.txt").read_text().strip()
          if args.start_from != "st_load" else "")
    for name, tool, kw in seq[names.index(args.start_from):]:
        if name != "st_load":
            kw = {"dataset_ref": ds, **kw}
        out = _step(name, res, h(tool), **kw)
        if name == "st_load":
            ds = out["dataset_ref"]
            (REPORT_DIR / "ds_id.txt").write_text(ds)
            print(f"dataset_ref = {ds}")

    (REPORT_DIR / "_cross.py").write_text(_CROSS, encoding="utf-8")
    cp = subprocess.run(
        ["docker", "run", "--rm", "--network", "none",
         "-v", f"{WS.resolve()}:/ws",
         "feishu-research-agent/bio:st-cpu-latest",
         "python", "/ws/_validate_real/_cross.py"],
        capture_output=True, text=True, timeout=600)
    if cp.returncode != 0:
        print(f"[FAIL] cross-validation: {cp.stderr[-2000:]}")
        raise SystemExit(1)
    cross = json.loads(cp.stdout.strip().splitlines()[-1])
    res["cross_validation"] = cross
    (REPORT_DIR / "report.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(cross, ensure_ascii=False, indent=1))
    print(f"VALIDATE OK | report → {REPORT_DIR}/report.json")


if __name__ == "__main__":
    main()
