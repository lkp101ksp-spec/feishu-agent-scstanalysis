"""BLAST NCBI Entrez API 客户端。

支持 esearch（取 ID）→ efetch（取记录）两步流程。
rate-limit 复用 RateLimiter（3 req/s）。
"""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from orchestrator.tools.bio.rate_limiter import RateLimiter


def _load_json_stream(raw: str) -> list[dict[str, Any]]:
    """解析 NCBI 拼接式多段 JSON 响应，返回逐段 dict 列表。

    真机 2026-08-31：efetch 大响应（实测 ~82KB 起）会被 NCBI 按
    分段切成多个完整 JSON 文档无分隔符直接拼接，resp.json() 抛
    "Extra data"。用 raw_decode 逐段消费即可。
    """
    decoder = json.JSONDecoder()
    objs: list[dict[str, Any]] = []
    idx, n = 0, len(raw)
    while idx < n:
        while idx < n and raw[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        obj, idx = decoder.raw_decode(raw, idx)
        if isinstance(obj, dict):
            objs.append(obj)
    return objs

# BLAST 习惯库名 → Entrez esearch/efetch 真实库名
_ENTREZ_DB_ALIAS = {
    "nr": "protein",
    "nt": "nucleotide",
    "refseq_protein": "protein",
    "refseq_rna": "nucleotide",
}


class BlastNCBITool:
    def __init__(
        self,
        *,
        rate_limiter: Optional[RateLimiter] = None,
        timeout_sec: int = 30,
        base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
    ) -> None:
        self.rate_limiter = rate_limiter or RateLimiter(rate=3.0, per_sec=1.0)
        self.timeout_sec = timeout_sec
        self.base_url = base_url.rstrip("/")

    def handle(self, *, query: str, database: str = "protein",
               max_hits: int = 5) -> dict[str, Any]:
        if not query or not query.strip():
            return {
                "error_code": "BLAST_INVALID_QUERY",
                "error_message": "query is empty",
            }
        # BLAST 库名 → Entrez 检索库名映射（真机 2026-08-30：db=nr
        # esearch 静默返回 0 命中——nr 是 BLAST 库不是 Entrez 库）
        database = _ENTREZ_DB_ALIAS.get(database, database)
        try:
            ids, total_count = self._esearch(
                query=query, database=database, max_hits=max_hits
            )
        except httpx.HTTPError as e:
            return {"error_code": "BLAST_API_ERROR", "error_message": str(e)}
        if not ids:
            return {
                "ids": [], "records": [],
                "total_count": total_count,
                "query": query, "database": database,
            }
        try:
            records = self._efetch(ids=ids, database=database)
        except httpx.HTTPError as e:
            return {"error_code": "BLAST_API_ERROR", "error_message": str(e)}
        # docsum 不返回 protein 库序列长度（真机 2026-08-31 全 0）；
        # fasta 批量取真实长度，失败降级保留 0（长度缺失不炸检索）
        try:
            lengths = self._fetch_lengths(ids=ids, database=database)
            for r in records:
                if r["id"] in lengths:
                    r["length"] = lengths[r["id"]]
        except httpx.HTTPError:
            pass
        return {
            "ids": ids, "records": records,
            "total_count": total_count,
            "query": query, "database": database,
        }

    def _esearch(self, *, query: str, database: str,
                 max_hits: int) -> tuple[list[str], int]:
        self.rate_limiter.wait()
        with httpx.Client(timeout=self.timeout_sec) as client:
            resp = client.post(
                f"{self.base_url}/esearch.fcgi",
                data={
                    "db": database,
                    "term": query,
                    "retmax": str(max_hits),
                    "retmode": "json",
                },
            )
            resp.raise_for_status()
        # 防御性走多段解析：esearch 响应小，但同源问题可能复现
        for data in _load_json_stream(resp.text):
            esr = data.get("esearchresult", {})
            if esr.get("idlist"):
                return esr.get("idlist", []), int(esr.get("count", 0))
        return [], 0

    def _efetch(self, *, ids: list[str], database: str) -> list[dict[str, Any]]:
        self.rate_limiter.wait()
        with httpx.Client(timeout=self.timeout_sec) as client:
            resp = client.get(
                f"{self.base_url}/efetch.fcgi",
                params={
                    "db": database,
                    "id": ",".join(ids),
                    "rettype": "docsum",
                    "retmode": "json",
                },
            )
            resp.raise_for_status()
        # 多段 JSON 逐段解析合并（真机 2026-08-31：大响应分段拼接，
        # resp.json() 抛 "Extra data: char 99176" 导致整节点失败）
        records: list[dict[str, Any]] = []
        for data in _load_json_stream(resp.text):
            result = data.get("result")
            if not isinstance(result, dict):
                continue
            for key, value in result.items():
                if key == "uids":
                    continue
                if isinstance(value, dict):
                    records.append({
                        "id": str(value.get("uid", key)),
                        "title": value.get("title", ""),
                        "summary": value.get("summary", ""),
                        "length": value.get("length", 0),
                    })
        return records

    def _fetch_lengths(self, *, ids: list[str], database: str) -> dict[str, int]:
        """efetch fasta 批量取每条序列真实长度（去空白计数）。

        fasta 按 '>' 分条；NCBI uid 段可能非纯数字（如 pdb|9SQY|B），
        与请求顺序对齐回填——按第 N 条序列对应 ids[N]。
        """
        self.rate_limiter.wait()
        with httpx.Client(timeout=self.timeout_sec) as client:
            resp = client.get(
                f"{self.base_url}/efetch.fcgi",
                params={
                    "db": database,
                    "id": ",".join(ids),
                    "rettype": "fasta",
                    "retmode": "text",
                },
            )
            resp.raise_for_status()
        lengths: dict[str, int] = {}
        seqs = resp.text.split(">")
        for uid, seq in zip(ids, [s for s in seqs if s.strip()]):
            lines = seq.splitlines()
            # 首行是 header（如 "111 title"），其余为序列行
            n = sum(len(line.strip()) for line in lines[1:])
            lengths[uid] = n
        return lengths
