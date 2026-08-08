"""飞书事件签名校验测试。"""
import base64
import hashlib
import hmac
import time

import pytest

from gateway.signature import verify_lark_signature
from shared.errors import SignatureInvalidError


SECRET = "test_secret_key"


def _sign(timestamp: str, body: str, secret: str = SECRET) -> str:
    """按飞书官方算法生成签名。"""
    string_to_sign = f"{timestamp}\n{secret}\n{body}".encode("utf-8")
    return base64.b64encode(
        hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
    ).decode("utf-8")


def test_verify_valid_signature_returns_true():
    ts = str(int(time.time()))
    body = '{"event":{"type":"im.message.receive_v1"}}'
    sig = _sign(ts, body)
    assert verify_lark_signature(timestamp=ts, body=body, signature=sig, secret=SECRET) is True


def test_verify_expired_timestamp_raises():
    ts = str(int(time.time()) - 3600)
    body = "{}"
    sig = _sign(ts, body)
    with pytest.raises(SignatureInvalidError) as exc:
        verify_lark_signature(timestamp=ts, body=body, signature=sig, secret=SECRET, ttl_sec=300)
    assert "expired" in str(exc.value).lower()


def test_verify_wrong_signature_raises():
    ts = str(int(time.time()))
    with pytest.raises(SignatureInvalidError) as exc:
        verify_lark_signature(timestamp=ts, body="{}", signature="bogus", secret=SECRET)
    assert "mismatch" in str(exc.value).lower()


def test_verify_empty_timestamp_raises():
    with pytest.raises(SignatureInvalidError):
        verify_lark_signature(timestamp="", body="{}", signature="x", secret=SECRET)


def test_verify_empty_signature_raises():
    with pytest.raises(SignatureInvalidError):
        verify_lark_signature(timestamp=str(int(time.time())), body="{}", signature="", secret=SECRET)


def test_verify_invalid_timestamp_format_raises():
    with pytest.raises(SignatureInvalidError) as exc:
        verify_lark_signature(timestamp="not-a-number", body="{}", signature="x", secret=SECRET)
    assert "invalid" in str(exc.value).lower()