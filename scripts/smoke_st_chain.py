"""st 链容器内冒烟：load→qc→process→markers→plot→domains→commot→
deconvolve 串行（Phase 21 真机，8 步）。

用法：python scripts/smoke_st_chain.py <visium目录绝对路径>
等价 BioRunner 行为：--rm -i --network none + 资源限额 + /data /ws 挂载。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

IMAGE = "feishu-research-agent/bio:st-cpu-latest"
WS = Path("I:/飞书agent/bio_workspace/smoke_st")


def run_script(script: str, args: dict, data_mount: str) -> dict:
    """跑单个 st 脚本，返回 stdout JSON（打印耗时供真机超时评估）。"""
    t0 = time.monotonic()
    cmd = ["docker", "run", "--rm", "-i", "--network", "none",
           "--cpus", "4", "--memory", "16g",
           "-v", f"{WS}:/ws", "-v", f"{data_mount}:/data:ro",
           IMAGE, "python", f"/opt/st_tools/{script}.py"]
    proc = subprocess.run(cmd, input=json.dumps(args), capture_output=True,
                          text=True, encoding="utf-8", timeout=1800)
    elapsed = time.monotonic() - t0
    if proc.returncode != 0:
        print(f"[{script}] FAILED rc={proc.returncode} elapsed={elapsed:.0f}s")
        print(proc.stderr[-2000:])
        sys.exit(1)
    out = json.loads(proc.stdout)
    brief = {k: v for k, v in out.items()
             if k not in ("markers",) and not isinstance(v, dict)}
    print(f"[{script}] ok={out.get('ok')} elapsed={elapsed:.0f}s", brief)
    if out.get("ok") is False:
        print(out.get("error_code"), out.get("error_message"))
        sys.exit(1)
    return out


def main() -> None:
    visium = sys.argv[1]
    data_mount = str(Path(visium).parent)
    rel = Path(visium).name
    WS.mkdir(parents=True, exist_ok=True)

    out = run_script("load", {"path": rel, "dataset_id": "smoke_st"},
                     data_mount)
    ds = out["dataset_ref"]
    run_script("qc", {"dataset_id": ds, "min_genes": 20, "max_mt_pct": 50},
               data_mount)
    out = run_script("process", {"dataset_id": ds, "resolution": 1.0},
                     data_mount)
    domains = out.get("n_domains")
    run_script("markers", {"dataset_id": ds, "top_n": 5}, data_mount)
    run_script("plot", {"dataset_id": ds, "genes": ["MARKER_D1_0"],
                        "color_by": "spatial_domain"}, data_mount)
    out = run_script("domains", {"dataset_id": ds, "method": "banksy"},
                     data_mount)
    ari = out.get("ari_vs_leiden")
    out = run_script("commot", {"dataset_id": ds, "species": "human",
                                "dis_thr": 200}, data_mount)
    top_path = out.get("top_pathway")
    n_lr = out.get("n_lr_pairs")
    out = run_script("deconvolve",
                     {"dataset_id": ds, "sc_ref_path": "tiny_scrna.h5ad",
                      "ref_label_col": "celltype", "max_epochs": 2000},
                     data_mount)
    types = out.get("cell_types")
    print(f"[deconvolve] mean_abundance={out.get('mean_abundance')}")

    for name in ("raw.h5ad", "filtered.h5ad", "processed.h5ad",
                 "umap.png", "spatial_domains.png", "dotplot.png",
                 "MARKER_D1_0_spatial.png", "spatial_domain_spatial.png",
                 "domains.h5ad", "banksy_domains.png", "compare_leiden.png",
                 "commot.h5ad", "commot_heatmap.png",
                 f"commot_{top_path}_sender.png",
                 f"commot_{top_path}_receiver.png",
                 "deconv.h5ad", f"deconv_{types[0]}.png"):
        p = WS / ds / name  # 工具约定产物落 /ws/<dataset_id>/ 子目录
        ok = p.exists() and p.stat().st_size > 0
        size = p.stat().st_size if p.exists() else 0
        print(f"{'OK' if ok else 'MISSING'} {name} ({size} B)")
        if not ok:
            sys.exit(1)
    print(f"SMOKE PASS (n_domains={domains}, ari={ari}, "
          f"top_path={top_path}, n_lr={n_lr}, "
          f"n_types={len(types or [])})")


if __name__ == "__main__":
    main()
