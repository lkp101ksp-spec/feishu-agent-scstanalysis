"""Phase 6: 本地 BLAST+ 工具（subprocess.run + 解析 blast JSON）。

与 Phase 4 MVP BlastNCBITool 并存；按 mode='auto' 自动选择。"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

from orchestrator.tools.bio.rate_limiter import RateLimiter


class BlastLocalTool:
    def __init__(
        self,
        *,
        binary_path: str = "",
        db_path: str = "",
        timeout_sec: int = 60,
        rate_limiter: Optional[RateLimiter] = None,
        mode: str = "auto",
    ) -> None:
        self.binary_path = binary_path
        self.db_path = db_path
        self.timeout_sec = timeout_sec
        self.rate_limiter = rate_limiter or RateLimiter(rate=3.0, per_sec=1.0)
        self.mode = mode

    def handle(self, *, query: str, database: str = "nr",
               max_hits: int = 5) -> dict:
        if not query or not query.strip():
            return {"error_code": "BLAST_INVALID_QUERY",
                    "error_message": "query is empty"}
        actual = self._resolve_mode(database=database)
        if actual != "local":
            return {"error_code": "BLAST_LOCAL_UNAVAILABLE",
                    "error_message": "local binary not found"}
        cmd = [
            self.binary_path,
            "-query", "-",
            "-db", os.path.join(self.db_path, database),
            "-outfmt", "15",
            "-max_target_seqs", str(max_hits),
        ]
        try:
            proc = subprocess.run(
                cmd, input=query, capture_output=True, text=True,
                timeout=self.timeout_sec, shell=False,
            )
        except subprocess.TimeoutExpired:
            return {"error_code": "BLAST_LOCAL_TIMEOUT",
                    "error_message": f"timeout after {self.timeout_sec}s"}
        except FileNotFoundError:
            return {"error_code": "BLAST_LOCAL_BINARY_MISSING",
                    "error_message": self.binary_path}
        if proc.returncode != 0:
            return {"error_code": "BLAST_LOCAL_ERROR",
                    "error_message": proc.stderr[:500]}
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            return {"error_code": "BLAST_LOCAL_PARSE_ERROR",
                    "error_message": str(e)}
        records = self._parse_hits(data)
        return {
            "ids": [r["id"] for r in records],
            "records": records,
            "query": query,
            "database": database,
            "total_count": len(records),
        }

    def _resolve_mode(self, *, database: str) -> str:
        if self.mode == "web":
            return "web"
        if self.mode == "local":
            return "local"
        # auto
        if (self.binary_path
                and os.path.exists(self.binary_path)
                and self._db_exists(database)):
            return "local"
        return "web"

    def _db_exists(self, database: str) -> bool:
        for ext in (".phr", ".psq", ".nhr", ".nsq"):
            if os.path.exists(os.path.join(self.db_path, database + ext)):
                return True
        return False

    def _parse_hits(self, data: dict) -> list[dict]:
        records: list[dict] = []
        for r in data.get("BlastOutput2", []):
            search = r.get("report", {}).get("results", {}).get("search", {})
            for hit in search.get("hits", []):
                descs = hit.get("description", [])
                title = descs[0].get("title", "") if descs else ""
                hsps = hit.get("hsps", [])
                score = hsps[0].get("hsp_score", 0) if hsps else 0
                records.append({
                    "id": str(hit.get("num", "")),
                    "title": title,
                    "summary": f"score: {score}",
                    "length": 0,
                })
        return records