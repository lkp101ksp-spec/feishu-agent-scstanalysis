"""构建期脚本：decoupler OmniPath PROGENy human top500 → TSV（st 镜像层内运行）。

dc.op.progeny 联网拉取 14 通路权重表（source/target/weight/padj），写
/opt/progeny/progeny_human_top500.tsv（~6463 行，运行期断网可用）。
下载失败非零退出 → docker build 报错重试（fetch_gene_pos.py 同语义）。
"""
from __future__ import annotations

import os

OUT_DIR = "/opt/progeny"
OUT_PATH = os.path.join(OUT_DIR, "progeny_human_top500.tsv")


def main() -> None:
    """decoupler 拉 PROGENy 模型并快照为 TSV（14 通路断言兜底）。"""
    import decoupler as dc
    net = dc.op.progeny(organism="human", top=500)
    os.makedirs(OUT_DIR, exist_ok=True)
    net.to_csv(OUT_PATH, sep="\t", index=False)
    n_pw = int(net["source"].nunique())
    print(f"wrote {len(net)} rows / {n_pw} pathways to {OUT_PATH}")
    assert n_pw == 14, f"通路数异常: {n_pw}"


if __name__ == "__main__":
    main()
