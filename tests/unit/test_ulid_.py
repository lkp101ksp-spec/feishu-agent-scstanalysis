"""ULID 生成与解析。"""
from shared.ulid_ import new_ulid, parse_ulid_timestamp


def test_new_ulid_is_string():
    uid = new_ulid()
    assert isinstance(uid, str)
    assert len(uid) == 26


def test_new_ulids_are_unique():
    ids = {new_ulid() for _ in range(1000)}
    assert len(ids) == 1000


def test_parse_ulid_timestamp_roundtrip():
    uid = new_ulid()
    ts = parse_ulid_timestamp(uid)
    assert ts > 0
