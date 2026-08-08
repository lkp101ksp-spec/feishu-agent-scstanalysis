"""飞书事件签名校验。

算法（飞书官方）：
  string_to_sign = timestamp + "\\n" + encrypt_key + "\\n" + raw_body
  signature = base64(hmac_sha256(string_to_sign))

校验失败抛 SignatureInvalidError。
"""
import base64
import hashlib
import hmac
import time

from shared.errors import SignatureInvalidError


def verify_lark_signature(
    timestamp: str,
    body: str,
    signature: str,
    secret: str,
    ttl_sec: int = 300,
) -> bool:
    """校验飞书 webhook 签名。失败抛 SignatureInvalidError，成功返回 True。"""
    if not timestamp or not signature:
        raise SignatureInvalidError("timestamp or signature empty")

    try:
        ts_int = int(timestamp)
    except ValueError as e:
        raise SignatureInvalidError(f"invalid timestamp format: {timestamp}") from e

    now = int(time.time())
    if abs(now - ts_int) > ttl_sec:
        raise SignatureInvalidError(
            f"timestamp expired: now={now} ts={ts_int} ttl={ttl_sec}"
        )

    string_to_sign = f"{timestamp}\n{secret}\n{body}".encode("utf-8")
    expected = base64.b64encode(
        hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
    ).decode("utf-8")

    if not hmac.compare_digest(expected, signature):
        raise SignatureInvalidError("signature mismatch")

    return True