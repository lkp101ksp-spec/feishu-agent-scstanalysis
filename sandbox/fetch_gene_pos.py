"""构建期脚本：Ensembl GRCh38 GTF.gz → 基因坐标 TSV（bio 镜像层内运行）。

下载 Homo_sapiens.GRCh38.114.gtf.gz，抽 feature=="gene" 行的
gene_name / chromosome / start / end（chr1-22/X/Y），按基因符号去重
（同名保留首个，PAR_Y 重复随去重消解），基因组序排序后写
/opt/cnv/gene_pos_grch38.tsv（~2MB，运行期断网可用）。
下载失败非零退出 → docker build 报错重试（fetch_gene_sets.py 同语义）。
"""
from __future__ import annotations

import gzip
import io
import os
import re
import sys
import urllib.request

GTF_URL = ("https://ftp.ensembl.org/pub/release-114/gtf/homo_sapiens/"
           "Homo_sapiens.GRCh38.114.gtf.gz")
OUT_DIR = "/opt/cnv"
OUT_PATH = os.path.join(OUT_DIR, "gene_pos_grch38.tsv")
KEEP = {str(i) for i in range(1, 23)} | {"X", "Y"}
CHROM_ORDER = [str(i) for i in range(1, 23)] + ["X", "Y"]
GENE_NAME = re.compile(r'gene_name "([^"]+)"')


def main() -> None:
    """下载 GTF、抽 gene 级坐标、去重排序写 TSV。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"downloading {GTF_URL}", file=sys.stderr)
    req = urllib.request.Request(GTF_URL, headers={"User-Agent": "bio-build"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        raw = io.BytesIO(resp.read())
    seen: set[str] = set()
    rows: list[tuple[str, str, int, int]] = []
    with gzip.open(raw, "rt") as fh:
        for line in fh:
            if "\tgene\t" not in line:
                continue
            f = line.split("\t")
            if f[0] not in KEEP:
                continue
            m = GENE_NAME.search(f[8])
            if m is None:
                continue
            name = m.group(1)
            if not name or name.startswith("ENSG") or name in seen:
                continue
            seen.add(name)
            rows.append((name, f[0], int(f[3]), int(f[4])))
    rank = {c: i for i, c in enumerate(CHROM_ORDER)}
    rows.sort(key=lambda r: (rank[r[1]], r[2]))
    with open(OUT_PATH, "w", encoding="utf-8") as out:
        out.write("gene_name\tchromosome\tstart\tend\n")
        for name, chrom, start, end in rows:
            out.write(f"{name}\tchr{chrom}\t{start}\t{end}\n")
    print(f"wrote {len(rows)} genes to {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
