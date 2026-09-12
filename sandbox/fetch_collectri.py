"""构建期脚本：decoupler CollecTRI human → TSV（st 镜像层内运行）。

dc.op.collectri 联网拉取 TF-靶基因权重表（source/target/weight/
resources/references/sign_decision），写 /opt/collectri/
collectri_human.tsv（~42990 行 / 1185 TFs，运行期断网可用）。
下载失败非零退出 → docker build 报错重试（fetch_progeny.py 同语义）。
"""
from __future__ import annotations

import os

OUT_DIR = "/opt/collectri"
OUT_PATH = os.path.join(OUT_DIR, "collectri_human.tsv")


def main() -> None:
    """decoupler 拉 CollecTRI 模型并快照为 TSV（TF 数断言兜底）。"""
    import decoupler as dc
    net = dc.op.collectri(organism="human")
    os.makedirs(OUT_DIR, exist_ok=True)
    net.to_csv(OUT_PATH, sep="\t", index=False)
    n_tf = int(net["source"].nunique())
    print(f"wrote {len(net)} rows / {n_tf} TFs to {OUT_PATH}")
    assert n_tf > 1000, f"TF 数异常: {n_tf}"


if __name__ == "__main__":
    main()
