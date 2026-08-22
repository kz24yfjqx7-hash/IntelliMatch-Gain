"""国密实现的正确性测试：SM3 用国标向量，SM2 用签名/验签/篡改三组用例。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.gm_crypto import (  # noqa: E402
    block_hash,
    generate_keypair,
    payload_hash,
    sign,
    sm3_hex,
    sm3_tag,
    verify,
)


def test_sm3_国标测试向量():
    assert sm3_hex("abc") == "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
    assert sm3_hex("abcd" * 16) == "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732"


def test_sm3_带前缀格式():
    tag = sm3_tag("energy")
    assert tag.startswith("sm3:") and len(tag) == 68


def test_sm2_签名可验证():
    pub, priv = generate_keypair()
    assert pub.startswith("04") and len(pub) == 130
    assert len(priv) == 64
    msg = "调度指令下发：Node-C discharge 24kW"
    sig = sign(msg, priv, pub)
    assert len(sig) == 128
    assert verify(msg, sig, pub) is True


def test_sm2_原文被篡改则验签失败():
    pub, priv = generate_keypair()
    sig = sign("Node-C discharge 24kW", priv, pub)
    assert verify("Node-C discharge 240kW", sig, pub) is False


def test_sm2_换公钥则验签失败():
    pub, priv = generate_keypair()
    other_pub, _ = generate_keypair()
    sig = sign("hello", priv, pub)
    assert verify("hello", sig, other_pub) is False


def test_sm2_非法签名不抛异常():
    pub, _ = generate_keypair()
    assert verify("hello", "not-a-signature", pub) is False
    assert verify("hello", "00" * 64, pub) is False


def test_摘要规范化与键序无关():
    """同一份数据不同键序必须算出相同摘要，否则完整性校验会误报篡改。"""
    assert payload_hash({"a": 1, "b": {"x": 2, "y": 3}}) == payload_hash({"b": {"y": 3, "x": 2}, "a": 1})


def test_区块哈希对输入敏感():
    h1 = block_hash("sm3:" + "0" * 64, "sm3:aa", "2026-08-17T14:23:05+08:00")
    h2 = block_hash("sm3:" + "0" * 64, "sm3:ab", "2026-08-17T14:23:05+08:00")
    h3 = block_hash("sm3:" + "0" * 64, "sm3:aa", "2026-08-17T14:23:06+08:00")
    assert h1 != h2 and h1 != h3
