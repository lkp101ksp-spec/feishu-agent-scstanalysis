"""Phase 31：bio 镜像构建期 Enrichr 基因集预取（运行期离线富集用）。

容器运行时 --network none（沙箱断网），gseapy 无法在线取库；
构建期（有网）把基因集库存成 JSON 到 /opt/gene_sets/，
sc_tools/enrichment.py 运行期直接读文件。
人源三库 + 小鼠两库（宿主实测可用：KEGG_2019_Mouse /
WikiPathways_2019_Mouse，验收数据为小鼠+人源双物种）。

仅 docker build 时执行；本机调试可用 GENE_SET_DIR 重定向输出目录。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import gseapy as gp

OUT = Path(os.environ.get("GENE_SET_DIR", "/opt/gene_sets"))
# (Enrichr 库名, 落盘文件名, organism)；与 sc_tools/enrichment.py GS_KEYS 对应
LIBS = [
    ("MSigDB_Hallmark_2020", "hallmark.json", "human"),
    ("GO_Biological_Process_2023", "go_bp.json", "human"),
    ("KEGG_2021_Human", "kegg.json", "human"),
    ("KEGG_2019_Mouse", "kegg_mouse.json", "Mouse"),
    ("WikiPathways_2019_Mouse", "wikipathways_mouse.json", "Mouse"),
]


def main() -> None:
    """预取基因集并落盘（已存在且非空则跳过，便于缓存层复用）。"""
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fname, organism in LIBS:
        target = OUT / fname
        if target.exists() and target.stat().st_size > 0:
            print(f"skip cached {fname}")
            continue
        lib = gp.get_library(name=name, organism=organism)
        target.write_text(json.dumps(lib), encoding="utf-8")
        print(f"{name}: {len(lib)} terms -> {target}")


if __name__ == "__main__":
    main()
