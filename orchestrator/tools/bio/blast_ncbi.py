"""BLAST NCBI Entrez API 客户端。

支持 esearch（取 ID）→ efetch（取记录）两步流程。
rate-limit 复用 RateLimiter（3 req/s）。
"""
from __future__ import annotations

from typing import Optional

import httpx

from orchestrator.tools.bio.rate_limiter import RateLimiter


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

    def handle(self, *, query: str, database: str = "nr",
               max_hits: int = 5) -> dict:
        if not query or not query.strip():
            return {
                "error_code": "BLAST_INVALID_QUERY",
                "error_message": "query is empty",
            }
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
        data = resp.json()
        esr = data.get("esearchresult", {})
        ids = esr.get("idlist", [])
        total_count = int(esr.get("count", 0))
        return ids, total_count

    def _efetch(self, *, ids: list[str], database: str) -> list[dict]:
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
        data = resp.json()
        result = data.get("result")
        records: list[dict] = []
        if isinstance(result, dict):
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