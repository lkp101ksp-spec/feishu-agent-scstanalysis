"""oscc 反卷积前置（Phase 76 spec §6.1）：st_deconvolve 真机长任务。

参数定型（_validate_st_real_chain.py L146-148 + 2026-09-12 验收口径）：
sc_ref=bio_test_data/oscc_sc_ref_sub.h5ad（/data 挂载）、
ref_label_col=cell_type、max_epochs=2000、ref_max_cells_per_type=150。
CPU 量级：ref 训练 ~15min + 空间映射 ~2h（deconvolve.py L8-15 钉注），
driver 侧 subprocess 超时放宽到 9000s。幂等：deconv.h5ad 已存在则跳过。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

WS = Path("bio_workspace").resolve()
REF = Path("bio_test_data").resolve()
IMG = "feishu-research-agent/bio:st-cpu-latest"
DS = "oscc"

out_h5ad = WS / DS / "deconv.h5ad"
if out_h5ad.exists():
    print(f"[skip] {out_h5ad} 已存在，幂等跳过")
    sys.exit(0)
assert (REF / "oscc_sc_ref_sub.h5ad").exists(), "缺 sc 参考 h5ad"
assert (WS / DS / "processed.h5ad").exists(), "缺 oscc processed.h5ad"

base = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws",
        "-v", f"{str(REF).replace(chr(92), '/')}:/data",
        "-v", f"{str(Path('sandbox/st_tools').resolve()).replace(chr(92), '/')}"
        ":/opt/st_tools", IMG]
payload = {"dataset_id": DS, "sc_ref_path": "oscc_sc_ref_sub.h5ad",
           "ref_label_col": "cell_type", "max_epochs": 2000,
           "ref_epochs": 250, "num_samples": 1000,
           "ref_max_cells_per_type": 150,
           "n_cells_per_location": 8.0, "detection_alpha": 20.0}
t0 = time.time()
p = subprocess.run(base + ["python", "/opt/st_tools/deconvolve.py"],
                   input=json.dumps(payload), capture_output=True,
                   text=True, timeout=9000)
out = json.loads(p.stdout)
assert out["ok"], out
print(f"[deconvolve] 耗时 {time.time() - t0:.0f}s, "
      f"n_cell_types={out['n_cell_types']}, n_ref_cells={out['n_ref_cells']}")
print(f"[deconvolve] cell_types: {out['cell_types']}")
print(f"[deconvolve] mean_abundance: {out['mean_abundance']}")
assert out_h5ad.exists()
print("REAL DECONV OK: oscc deconv.h5ad 落盘")
