"""Phase 8 T6: VersionDiffService 单元测试。"""
import json
from unittest.mock import MagicMock

import pytest

from orchestrator.templates.diff_service import VersionDiffService


def _ver(blocks=None, steps=None, name="std", desc="d"):
    v = MagicMock()
    v.blocks_json = json.dumps(blocks) if blocks else None
    v.steps_json = json.dumps(steps) if steps else None
    v.name = name
    v.description = desc
    return v


def _svc(va, vb):
    repo = MagicMock()
    repo.get_by_version.side_effect = lambda tid, vn: (
        va if vn == 1 else vb if vn == 2 else None
    )
    return VersionDiffService(repo)


def test_diff_added_block():
    svc = _svc(
        _ver(blocks=[{"type": "heading", "text": "A"}]),
        _ver(blocks=[{"type": "heading", "text": "A"},
                     {"type": "code", "language": "python"}]),
    )
    out = svc.diff(template_id="t1", v_a=1, v_b=2)
    assert len(out["blocks"]["added"]) == 1
    assert out["blocks"]["added"][0]["type"] == "code"
    assert out["blocks"]["removed"] == []
    assert out["blocks"]["changed"] == []


def test_diff_removed_block():
    svc = _svc(
        _ver(blocks=[{"type": "heading", "text": "A"},
                     {"type": "quote", "text": "q"}]),
        _ver(blocks=[{"type": "heading", "text": "A"}]),
    )
    out = svc.diff(template_id="t1", v_a=1, v_b=2)
    assert len(out["blocks"]["removed"]) == 1
    assert out["blocks"]["removed"][0]["type"] == "quote"


def test_diff_changed_fields():
    svc = _svc(
        _ver(blocks=[{"type": "heading", "text": "A"}]),
        _ver(blocks=[{"type": "heading", "text": "B"}]),
    )
    out = svc.diff(template_id="t1", v_a=1, v_b=2)
    assert len(out["blocks"]["changed"]) == 1
    assert out["blocks"]["changed"][0]["fields"] == ["text"]


def test_diff_same_versions_empty():
    svc = _svc(
        _ver(blocks=[{"type": "heading", "text": "A"}]),
        _ver(blocks=[{"type": "heading", "text": "A"}]),
    )
    out = svc.diff(template_id="t1", v_a=1, v_b=2)
    for section in ("added", "removed", "changed"):
        assert out["blocks"][section] == []
    assert out["meta"]["name_changed"] is False


def test_diff_missing_version_raises():
    svc = _svc(_ver(), _ver())
    with pytest.raises(ValueError):
        svc.diff(template_id="t1", v_a=1, v_b=99)


def test_render_changed_and_name():
    svc = _svc(
        _ver(blocks=[{"type": "heading", "text": "A"}], name="old"),
        _ver(blocks=[{"type": "heading", "text": "B"}], name="new"),
    )
    diff = svc.diff(template_id="t1", v_a=1, v_b=2)
    text = svc.render(diff)
    assert "~ [0] heading (字段: text)" in text
    assert "~ name: old → new" in text


def test_render_no_diff_placeholder():
    svc = _svc(_ver(), _ver())
    diff = svc.diff(template_id="t1", v_a=1, v_b=2)
    assert "（无差异）" in svc.render(diff)
