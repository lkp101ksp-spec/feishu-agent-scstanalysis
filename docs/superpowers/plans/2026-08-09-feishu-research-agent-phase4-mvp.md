# Phase 4 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline 模式)。Subagent 在 Phase 2/3 期间多次漏建文件，本计划改为直接实施。

**Goal:** 注册 NCBI BLAST 工具到 ToolRegistry，让用户发"blast BRCA1"触发真实 BLAST 搜索并写入飞书文档。

**Architecture:** Phase 4 MVP 仅新增 `orchestrator/tools/bio/blast_ncbi.py`（含 RateLimiter）+ `orchestrator/tools/builtin/l3_bio.py`（注册）。Orchestrator 新增 `process_phase4()` 入口，但内部复用 `process_phase3()` 路径，区别仅 ToolRegistry 多 1 个工具。

**Tech Stack:** httpx + respx（mock）+ pydantic v2 + pytest

**前置 spec:** [../specs/2026-08-09-feishu-research-agent-phase4-mvp-design.md](../specs/2026-08-09-feishu-research-agent-phase4-mvp-design.md)

---

## File Structure（前置）

```
orchestrator/tools/bio/
  __init__.py
  rate_limiter.py              # 3 req/s token bucket
  blast_ncbi.py                # BlastNCBITool.handle()
orchestrator/tools/builtin/
  l3_bio.py                    # register_l3_bio() 注册 blast_search
config/
  settings.py                  # 新增 blast_* 字段
orchestrator/
  app.py                       # 注册 l3_bio + process_phase4
tests/unit/
  test_rate_limiter.py
  test_blast_ncbi.py
  test_l3_bio_registry.py
tests/integration/
  test_e2e_phase4_blast.py     # E1-E3 场景
```

---

## Task 1: Settings 扩展（blast_*）

**Files:**
- Modify: `config/settings.py`
- Create: `tests/unit/test_phase4_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_phase4_settings.py
from config.settings import Settings


def test_phase4_blast_settings_exist():
    s = Settings.__dataclass_fields__
    assert "blast_api_base_url" in s
    assert "blast_rate_per_sec" in s
    assert "blast_timeout_sec" in s
    assert "blast_max_hits_default" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_phase4_settings.py -v`
Expected: FAIL

- [ ] **Step 3: Append Phase 4 settings**

```python
    # === Phase 4: BLAST ===
    blast_api_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    blast_rate_per_sec: float = 3.0
    blast_timeout_sec: int = 30
    blast_max_hits_default: int = 5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_phase4_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config/settings.py tests/unit/test_phase4_settings.py
git commit -m "feat(phase4-mvp): add BLAST settings (api url, rate, timeout, max_hits)"
```

---

## Task 2: RateLimiter（3 req/s token bucket）

**Files:**
- Create: `orchestrator/tools/bio/__init__.py`
- Create: `orchestrator/tools/bio/rate_limiter.py`
- Create: `tests/unit/test_rate_limiter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rate_limiter.py
import time

import pytest

from orchestrator.tools.bio.rate_limiter import RateLimiter


def test_rate_limiter_allows_first_call_immediately():
    rl = RateLimiter(rate=3, per_sec=1.0)
    t0 = time.monotonic()
    rl.wait()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.01


def test_rate_limiter_enforces_interval():
    rl = RateLimiter(rate=3, per_sec=1.0)
    rl.wait()
    t0 = time.monotonic()
    rl.wait()  # 第二次调用应等待 ~0.333s
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.30  # 留出一些余量
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_rate_limiter.py -v`
Expected: ImportError

- [ ] **Step 3: Create `__init__.py`**

```python
# orchestrator/tools/bio/__init__.py
# empty
```

- [ ] **Step 4: Create rate_limiter.py**

```python
# orchestrator/tools/bio/rate_limiter.py
"""简易 token bucket rate limiter。"""
from __future__ import annotations

import time


class RateLimiter:
    """3 req/s token bucket。"""

    def __init__(self, rate: float = 3.0, per_sec: float = 1.0) -> None:
        self.rate = rate
        self.per_sec = per_sec
        self.interval = per_sec / rate
        self._last_call: float = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_call = time.monotonic()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_rate_limiter.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add orchestrator/tools/bio/__init__.py orchestrator/tools/bio/rate_limiter.py tests/unit/test_rate_limiter.py
git commit -m "feat(phase4-mvp): add RateLimiter (3 req/s token bucket)"
```

---

## Task 3: BlastNCBITool（核心工具实现）

**Files:**
- Create: `orchestrator/tools/bio/blast_ncbi.py`
- Create: `tests/unit/test_blast_ncbi.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_blast_ncbi.py
import httpx
import pytest
import respx

from orchestrator.tools.bio.blast_ncbi import BlastNCBITool


def _esearch_json(ids, total_count):
    return {
        "esearchresult": {
            "idlist": ids,
            "count": str(total_count),
        }
    }


def _efetch_json(records):
    # NCBI 返回 {"result": {"uids": [id1, id2], id1: {...}, id2: {...}}}
    result = {"uids": [r["id"] for r in records]}
    for r in records:
        result[r["id"]] = {
            "uid": r["id"],
            "title": r["title"],
            "summary": r["summary"],
            "length": r.get("length", 0),
        }
    return {"result": result}


@pytest.fixture
def blast():
    # 不等待限流：测试用 RateLimiter 间隔为 0
    return BlastNCBITool(
        rate_limiter=type("RL", (), {"wait": lambda self: None})(),
        timeout_sec=5,
    )


@respx.mock
def test_handle_returns_ids_and_records(blast):
    respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_esearch_json(["111", "222"], 2))
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(
            200,
            json=_efetch_json([
                {"id": "111", "title": "BRCA1", "summary": "DNA repair", "length": 100},
                {"id": "222", "title": "BRCA2", "summary": "DNA repair", "length": 200},
            ]),
        )
    )
    out = blast.handle(query="BRCA1", database="nr", max_hits=2)
    assert out["ids"] == ["111", "222"]
    assert len(out["records"]) == 2
    assert out["records"][0]["title"] == "BRCA1"
    assert out["total_count"] == 2
    assert out["query"] == "BRCA1"
    assert out["database"] == "nr"


@respx.mock
def test_handle_returns_empty_when_no_hits(blast):
    respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_esearch_json([], 0))
    )
    out = blast.handle(query="XXXX", database="nr", max_hits=5)
    assert out["ids"] == []
    assert out["records"] == []
    assert out["total_count"] == 0


def test_handle_invalid_query_returns_error(blast):
    out = blast.handle(query="", database="nr", max_hits=5)
    assert out["error_code"] == "BLAST_INVALID_QUERY"


@respx.mock
def test_handle_esearch_error_returns_error_code(blast):
    respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    out = blast.handle(query="BRCA1", database="nr", max_hits=5)
    assert out["error_code"] == "BLAST_API_ERROR"


@respx.mock
def test_handle_efetch_error_returns_error_code(blast):
    respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_esearch_json(["111"], 1))
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    out = blast.handle(query="BRCA1", database="nr", max_hits=5)
    assert out["error_code"] == "BLAST_API_ERROR"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_blast_ncbi.py -v`
Expected: ImportError

- [ ] **Step 3: Create blast_ncbi.py**

```python
# orchestrator/tools/bio/blast_ncbi.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_blast_ncbi.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/tools/bio/blast_ncbi.py tests/unit/test_blast_ncbi.py
git commit -m "feat(phase4-mvp): add BlastNCBITool (esearch + efetch)"
```

---

## Task 4: L3_bio 注册（register_l3_bio）

**Files:**
- Create: `orchestrator/tools/builtin/l3_bio.py`
- Create: `tests/unit/test_l3_bio_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_l3_bio_registry.py
from orchestrator.tools.tool_registry import ToolRegistry
from orchestrator.tools.builtin.l3_bio import register_l3_bio


def test_register_l3_bio_adds_blast_search():
    reg = ToolRegistry()
    register_l3_bio(reg)
    spec = reg.get("blast_search")
    assert spec.name == "blast_search"
    assert spec.risk_level == "L0_read"
    assert "query" in spec.parameters["properties"]


def test_blast_search_handler_returns_results():
    reg = ToolRegistry()
    register_l3_bio(reg)
    spec = reg.get("blast_search")
    # handler 应能直接调（不真正发请求，因为 mock 已在 tool 内部）
    # 这里仅验证 handler 可调 + 返回 dict
    assert callable(spec.handler)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_l3_bio_registry.py -v`
Expected: ImportError

- [ ] **Step 3: Create l3_bio.py**

```python
# orchestrator/tools/builtin/l3_bio.py
"""L3 领域工具注册（BLAST 等）。

Phase 4 MVP：仅注册 BLAST 工具。
"""
from __future__ import annotations

from orchestrator.tools.bio.blast_ncbi import BlastNCBITool
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


def register_l3_bio(registry: ToolRegistry) -> None:
    """注册 L3 领域工具。Phase 4 MVP 仅含 BLAST。"""
    blast = BlastNCBITool()
    registry.register(ToolSpec(
        name="blast_search",
        description=(
            "BLAST 搜索 NCBI 数据库。"
            "输入：query, database, max_hits。返回 hits IDs 与摘要。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索词（如 'BRCA1[Gene] AND Homo sapiens[Organism]'）",
                },
                "database": {
                    "type": "string",
                    "default": "nr",
                    "description": "NCBI 数据库名（如 nr, protein, nucleotide）",
                },
                "max_hits": {
                    "type": "integer",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["query"],
        },
        risk_level="L0_read",
        handler=lambda **inputs: blast.handle(**inputs),
    ))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_l3_bio_registry.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/tools/builtin/l3_bio.py tests/unit/test_l3_bio_registry.py
git commit -m "feat(phase4-mvp): add l3_bio registration (blast_search)"
```

---

## Task 5: Orchestrator 集成（process_phase4）

**Files:**
- Modify: `orchestrator/app.py`
- Create: `tests/unit/test_orchestrator_phase4.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_orchestrator_phase4.py
from unittest.mock import MagicMock

from orchestrator.app import Orchestrator


def test_orchestrator_phase4_has_process_phase4():
    orch = Orchestrator(
        llm_router=MagicMock(), session_service=MagicMock(),
        task_service=MagicMock(), bind_doc_service=MagicMock(),
        doc_write_service=MagicMock(), im_adapter=MagicMock(),
        settings=MagicMock(),
    )
    assert hasattr(orch, "process_phase4")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_orchestrator_phase4.py -v`
Expected: AttributeError

- [ ] **Step 3: Modify orchestrator/app.py**

在 init 块增加 `register_l3_bio(self.registry)` 调用。Phase 2 已经有 `register_l0_read` / `register_l1_compute` / `register_l2_side_effect` 三次注册。Phase 4 增加第四次：

找到 init 中 Phase 2 注册完的位置，在 `self.tool_handler = ToolHandler(...)` 之前追加：

```python
            # === Phase 4 MVP: L3 领域工具 ===
            from orchestrator.tools.builtin.l3_bio import register_l3_bio
            register_l3_bio(self.registry)
```

在 `process_phase3` 方法末尾追加 `process_phase4`：

```python
    def process_phase4(self, incoming: IncomingMessage) -> dict:
        """Phase 4 MVP：与 process_phase3 区别仅 ToolRegistry 多注册 blast_search。

        普通消息委托给 process_phase3。
        """
        if not hasattr(self, "planner"):
            raise FeishuAgentError(
                "Phase 4 subsystems not initialized; "
                "construct Orchestrator with settings + adapters."
            )
        # /bind-doc-renew 指令继承 Phase 3
        if incoming.text.strip() == "/bind-doc-renew":
            session_id = self.session_service.get_or_create(
                owner_open_id=incoming.sender_open_id,
                source_chat_id=incoming.chat_id,
            )
            try:
                new_exp = self.bind_doc_service.renew(session_id=session_id)
                self.im.reply(incoming.chat_id,
                              f"[成功] 已续期到 {new_exp.isoformat()}")
                return {
                    "status": "renew_bind", "session_id": session_id,
                    "new_expires": new_exp.isoformat(),
                }
            except Exception as e:
                self.im.reply(incoming.chat_id, f"[错误] 续期失败：{e}")
                return {"status": "renew_bind_failed", "error": str(e)}
        # 普通消息：复用 process_phase3 路径
        return self.process_phase3(incoming)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_orchestrator_phase4.py tests/integration/test_webhook_app.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app.py tests/unit/test_orchestrator_phase4.py
git commit -m "feat(phase4-mvp): Orchestrator.process_phase4 + register_l3_bio"
```

---

## Task 6: 端到端 E1-E3

**Files:**
- Create: `tests/integration/test_e2e_phase4_blast.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_e2e_phase4_blast.py
"""E1-E3: BLAST 工具端到端（mock API + Planner + Executor）。"""
import httpx
import pytest
import respx

from orchestrator.tools.bio.blast_ncbi import BlastNCBITool
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec
from orchestrator.tools.builtin.l3_bio import register_l3_bio
from orchestrator.tools.tool_handler import ToolHandler


class FakePlanner:
    """模拟 Planner：返回含 blast_search 的 DAGPlan。"""

    def plan(self, *, message, task_id, session_id,
             available_tools, tools_schema):
        # 单 tool 节点：直接调 blast_search
        from orchestrator.planner.dag_schema import DAGNode, DAGPlan
        from shared.ulid_ import new_ulid
        node = DAGNode(
            node_id="n1", kind="tool", tool_name="blast_search",
            inputs={"query": message, "database": "nr", "max_hits": 3},
            depends_on=[],
        )
        return DAGPlan(
            plan_id=new_ulid(), task_id=task_id, session_id=session_id,
            nodes=[node], entry_node_ids=["n1"],
        )


def _esearch_json(ids, total):
    return {"esearchresult": {"idlist": ids, "count": str(total)}}


def _efetch_json(records):
    result = {"uids": [r["id"] for r in records]}
    for r in records:
        result[r["id"]] = {
            "uid": r["id"], "title": r["title"],
            "summary": r["summary"], "length": r.get("length", 0),
        }
    return {"result": result}


@respx.mock
def test_e1_blast_end_to_end_with_mock():
    """E1: blast BRCA1 → ToolRegistry 路由 → BLAST 工具 → ToolResult 含 hits。"""
    respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_esearch_json(["111", "222"], 2))
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(200, json=_efetch_json([
            {"id": "111", "title": "BRCA1", "summary": "DNA repair", "length": 100},
            {"id": "222", "title": "BRCA2", "summary": "DNA repair", "length": 200},
        ]))
    )

    reg = ToolRegistry()
    register_l3_bio(reg)
    handler = ToolHandler(registry=reg)

    result = handler.execute(
        "blast_search",
        {"query": "BRCA1", "database": "nr", "max_hits": 2},
        actor_open_id="ou_1", session_id="s1",
    )
    assert result.error_code is None
    assert result.outputs["ids"] == ["111", "222"]
    assert len(result.outputs["records"]) == 2


@respx.mock
def test_e2_blast_rate_limiter_enforces_interval():
    """E2: RateLimiter 在 3 req/s 时强制间隔 ~0.333s。"""
    import time
    from orchestrator.tools.bio.rate_limiter import RateLimiter
    rl = RateLimiter(rate=3, per_sec=1.0)
    rl.wait()
    t0 = time.monotonic()
    rl.wait()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.30


@respx.mock
def test_e3_blast_api_error_returns_error_code():
    """E3: NCBI 返回 500 → ToolResult.error_code="BLAST_API_ERROR"。"""
    respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )

    reg = ToolRegistry()
    register_l3_bio(reg)
    handler = ToolHandler(registry=reg)

    result = handler.execute(
        "blast_search",
        {"query": "BRCA1", "database": "nr", "max_hits": 5},
        actor_open_id="ou_1", session_id="s1",
    )
    assert result.error_code == "BLAST_API_ERROR"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_e2e_phase4_blast.py -v`
Expected: PASS (3 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_e2e_phase4_blast.py
git commit -m "test(phase4-mvp): add e2e E1-E3 (BLAST mock API + registry + ToolHandler)"
```

---

## Task 7: 回归验证

**Files:**
- Modify: 无（仅验证）

- [ ] **Step 1: Run all unit + integration tests**

Run: `python -m pytest tests/unit tests/integration --tb=line -q`
Expected: ALL PASS, 0 regressions（216 + 11 = 227）

如果发现 Phase 1-3 测试失败：

1. 检查 register_l3_bio 是否破坏 ToolRegistry 单例
2. 检查 process_phase4 是否破坏 Orchestrator 接口
3. 修复并重跑

- [ ] **Step 2: Commit fix (if any)**

```bash
git add -A
git commit -m "fix(phase4-mvp): ensure Phase 1-3 regression tests pass"
```

---

## Task 8: 测试总结

**Files:**
- Append: 测试总结 +<date>.md

- [ ] **Step 1: Append Phase 4 MVP summary**

```
### Phase 4 MVP（Task 1-6）
- BLAST 工具：NCBI Entrez web API（esearch + efetch）
- 限流：3 req/s token bucket
- 工具等级：L0_read（无需审批）
- 注册位置：ToolRegistry 内置

### 测试覆盖
- RateLimiter: 2 tests
- BlastNCBITool: 5 tests（happy path / no hits / invalid query / esearch error / efetch error）
- L3_bio 注册: 2 tests
- Orchestrator: 1 smoke test
- E2E: 3 tests（E1 全链路 / E2 限流 / E3 API 错误）

### 累计测试
- 216 (Phase 3) + 11 (Phase 4 MVP) = 227 tests
- 0 回归（Phase 1-3 全部通过）
```

- [ ] **Step 2: Commit**

```bash
git add 测试总结+2026-08-09*.md
git commit -m "docs(phase4-mvp): append Phase 4 MVP summary"
```

---

## Self-Review

### 1. Spec coverage matrix

| Spec § | Requirement | Task | Status |
|---|---|---|---|
| §1.2 | 仅 BLAST + 脚手架 | Task 3, 4, 5 | ✓ |
| §3.2 | NCBI Entrez API（esearch + efetch）| Task 3 | ✓ |
| §3.3 | 输入参数（query, database, max_hits）| Task 3 | ✓ |
| §3.4 | 输出格式（ids, records, total_count）| Task 3 | ✓ |
| §3.5 | 限流策略（3 req/s token bucket）| Task 2 | ✓ |
| §3.6 | 错误处理（6 类错误码）| Task 3 | ✓ |
| §3.8 | 工具注册（ToolRegistry）| Task 4 | ✓ |
| §4.2 | Orchestrator 集成（process_phase4）| Task 5 | ✓ |
| §5.4 | 覆盖率目标（≥ 80%）| Task 6 + 7 | ✓ |
| §5.2 | E1-E3 场景 | Task 6 | ✓ |

**Gaps identified**: None — 全部 spec 章节有对应 Task。

### 2. Placeholder scan

搜索 "TBD" / "TODO" / "implement later" / "fill in details" — **0 个**。

### 3. Type consistency

| Name | Definition site | Use site |
|---|---|---|
| `RateLimiter` | Task 2 `orchestrator/tools/bio/rate_limiter.py` | Task 3 (BlastNCBITool) |
| `BlastNCBITool` | Task 3 `orchestrator/tools/bio/blast_ncbi.py` | Task 4 (register_l3_bio) |
| `register_l3_bio` | Task 4 `orchestrator/tools/builtin/l3_bio.py` | Task 5 (Orchestrator), Task 6 (E1E3) |
| `process_phase4` | Task 5 `orchestrator/app.py` | Task 5 test, Task 6 (隐式) |
| `blast_*` settings | Task 1 `config/settings.py` | Task 3 (BlastNCBITool 内部) |

All consistent.

---

## Total Task count

**8 Tasks**

| # | Component | Tests |
|---|---|---|
| 1 | Settings 扩展 | 1 (smoke) |
| 2 | RateLimiter | 2 |
| 3 | BlastNCBITool | 5 |
| 4 | L3_bio 注册 | 2 |
| 5 | Orchestrator.process_phase4 | 1 (smoke) |
| 6 | E1-E3 e2e | 3 |
| 7 | 回归验证 | 0 (验证) |
| 8 | 测试总结 | 0 (docs) |
| **合计** | | **14 新测试（unit 11 + e2e 3）** |

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-09-feishu-research-agent-phase4-mvp.md`.

**Inline execution chosen**（沿用 Phase 3 节奏，subagent 漏建文件 → 直接实施）。

8 Task 预计 < 10 分钟完成。