"""Phase 23：bio_workspace_gc.sweep 单测（tmp_path 造假数据集 + now 注入控时）。"""
import os
import time
from pathlib import Path

from orchestrator.bio_workspace_gc import sweep

DAY = 86400


def _mk_dataset(root: Path, name: str, *, size: int = 100,
                age_sec: float = 0) -> Path:
    """造假数据集目录：data.bin（size 字节），文件与目录 mtime 回拨 age_sec。"""
    d = root / name
    d.mkdir()
    f = d / "data.bin"
    f.write_bytes(b"x" * size)
    past = time.time() - age_sec
    os.utime(f, (past, past))
    os.utime(d, (past, past))
    return d


def _touch_marker(d: Path) -> None:
    """模拟 BioRunner 打点：.last_access 置为当前时间。"""
    (d / ".last_access").touch()


def test_ttl_deletes_expired(tmp_path):
    """last_used 超过 ttl 的数据集被整目录删除并计入 freed_bytes。"""
    d = _mk_dataset(tmp_path, "aaaaaaaabbbb", age_sec=8 * DAY)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=10**12, grace_sec=7200)
    assert not d.exists()
    assert r["ttl_deleted"] == ["aaaaaaaabbbb"]
    assert r["freed_bytes"] == 100


def test_ttl_keeps_fresh(tmp_path):
    """未到期数据集保留。"""
    d = _mk_dataset(tmp_path, "aaaaaaaabbbb", age_sec=1 * DAY)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=10**12, grace_sec=7200)
    assert d.exists()
    assert r["ttl_deleted"] == []


def test_grace_protects_from_ttl(tmp_path):
    """宽限期内（2h 动过）的目录即使超 TTL 也跳过。"""
    d = _mk_dataset(tmp_path, "aaaaaaaabbbb", age_sec=8 * DAY)
    _touch_marker(d)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=10**12, grace_sec=7200)
    assert d.exists()
    assert "aaaaaaaabbbb" in r["skipped"]


def test_last_used_prefers_marker(tmp_path):
    """.last_access 新于目录 mtime 时以打点为准（grace=0 排除宽限期干扰）。"""
    d = _mk_dataset(tmp_path, "aaaaaaaabbbb", age_sec=8 * DAY)
    _touch_marker(d)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=10**12, grace_sec=0)
    assert d.exists()  # last_used≈now < ttl，不删


def test_non_dataset_dirs_skipped(tmp_path):
    """非 12hex 目录（如 smoke_st）不在治理范围。"""
    d = _mk_dataset(tmp_path, "smoke_st", age_sec=30 * DAY)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=10**12, grace_sec=7200)
    assert d.exists()
    assert r["ttl_deleted"] == [] and r["skipped"] == []


def test_lru_evicts_oldest_first(tmp_path):
    """总量超 cap 时按 last_used 升序驱逐，达标即停。"""
    old = _mk_dataset(tmp_path, "aaaaaaaaaaaa", size=600, age_sec=3 * DAY)
    mid = _mk_dataset(tmp_path, "bbbbbbbbbbbb", size=600, age_sec=2 * DAY)
    new = _mk_dataset(tmp_path, "cccccccccccc", size=600, age_sec=1 * DAY)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=1300, grace_sec=7200)
    # 总量 1800 > 1300：驱逐最旧的 aaaa（600）后 1200 ≤ 1300 停
    assert not old.exists()
    assert mid.exists() and new.exists()
    assert r["lru_deleted"] == ["aaaaaaaaaaaa"]


def test_lru_respects_grace(tmp_path):
    """LRU 阶段同样遵守宽限期：最旧但宽限内的不驱逐，驱逐次旧。"""
    g = _mk_dataset(tmp_path, "dddddddddddd", size=600, age_sec=5 * DAY)
    _touch_marker(g)  # last_used=now → 宽限期内
    old = _mk_dataset(tmp_path, "eeeeeeeeeeee", size=600, age_sec=3 * DAY)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=600, grace_sec=7200)
    assert g.exists()
    assert not old.exists()
    assert r["lru_deleted"] == ["eeeeeeeeeeee"]


def test_delete_failure_continues(tmp_path, monkeypatch):
    """单目录删除失败仅 warn，其余照常删除。"""
    import orchestrator.bio_workspace_gc as gc
    bad = _mk_dataset(tmp_path, "ffffffffffff", age_sec=8 * DAY)
    good = _mk_dataset(tmp_path, "111111111111", age_sec=8 * DAY)
    real_rmtree = gc.shutil.rmtree

    def flaky(path, *a, **k):
        if Path(path).name == "ffffffffffff":
            raise OSError("locked")
        return real_rmtree(path, *a, **k)

    monkeypatch.setattr(gc.shutil, "rmtree", flaky)
    r = sweep(tmp_path, ttl_sec=7 * DAY, cap_bytes=10**12, grace_sec=7200)
    assert bad.exists()
    assert not good.exists()
    assert r["ttl_deleted"] == ["111111111111"]


def test_gc_settings_fields_exist():
    """Phase 23 五个配置字段存在且默认值符合 spec §3。"""
    from config.settings import Settings
    fields = Settings.__dataclass_fields__
    assert fields["bio_workspace_ttl_sec"].default == 604800
    assert fields["bio_workspace_cap_gb"].default == 10
    assert fields["bio_workspace_grace_sec"].default == 7200
    assert fields["bio_workspace_gc_interval_sec"].default == 3600
    assert fields["bio_workspace_gc_enabled"].default is True


def test_gc_settings_env_override(monkeypatch):
    """env 覆盖生效（BIO_WORKSPACE_GC_ENABLED=false 语义关闭）。"""
    from config.settings import load_settings
    monkeypatch.setenv("BIO_WORKSPACE_TTL_SEC", "3600")
    monkeypatch.setenv("BIO_WORKSPACE_CAP_GB", "5")
    monkeypatch.setenv("BIO_WORKSPACE_GC_ENABLED", "false")
    s = load_settings()
    assert s.bio_workspace_ttl_sec == 3600
    assert s.bio_workspace_cap_gb == 5
    assert s.bio_workspace_gc_enabled is False
