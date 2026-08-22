"""种子数据自检：不依赖数据库，直接校验 02_seed.sql 的内容是否自洽。

这几条断言挡住的是最难排查的一类故障——种子数据本身就是坏的，
结果答辩现场点开「链完整性校验」直接标红。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.gm_crypto import sm3_hex  # noqa: E402
from core.security import verify_password  # noqa: E402

SQL = (Path(__file__).resolve().parents[1] / "sql" / "02_seed.sql").read_text(encoding="utf-8")

DEMO_ACCOUNTS = {
    "admin": "admin123", "grid": "grid123", "vpp": "vpp123",
    "subject": "subject123", "regulator": "reg123", "edge": "edge123",
}


def test_六个演示账号密码可用():
    rows = re.findall(r"\(\d+, '(\w+)', '(\$2b\$[^']+)'", SQL)
    assert {u for u, _ in rows} == set(DEMO_ACCOUNTS)
    for username, hashed in rows:
        assert verify_password(DEMO_ACCOUNTS[username], hashed), f"{username} 密码哈希不匹配"


def test_did_由公钥摘要推导():
    pairs = re.findall(r"\('(did:vpp:[^']+)', 'SM2', '(04[0-9a-f]{128})'", SQL)
    assert len(pairs) == 17
    for did, pub in pairs:
        assert did.endswith(sm3_hex(bytes.fromhex(pub))[:32]), f"{did} 与公钥不对应"


def test_四个节点与前端_mock_对齐():
    for node_id in ("Node-A", "Node-B", "Node-C", "Node-D"):
        assert f"'{node_id}'" in SQL


def test_资产覆盖全部类型与等级():
    for data_type in ("pv", "wind", "storage", "load", "dispatch"):
        assert f"'{data_type}'" in SQL
    for level in ("L1", "L2", "L3", "L4"):
        assert f"'{level}'" in SQL


def test_存证链创世块存在且高度连续():
    heights = [int(h) for h in re.findall(r"'blk-(\d{6})-0', 'tr-", SQL)]
    assert heights[0] == 0
    assert heights == sorted(heights)
    assert len(set(heights)) == len(heights), "区块高度重复"
