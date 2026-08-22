"""国密 SM2 / SM3 实现（纯 Python，零外部依赖）。

为什么不直接用 gmssl 库：
1. 交付物要打成 x86_64 + arm64 双架构离线安装包，任何 C 扩展依赖都会在 QEMU
   模拟的 ARM 构建里触发源码编译，风险极高；
2. 平台必须完全离线运行，依赖越少越可控。

SM3 按 GB/T 32905-2016 实现，SM2 签名验签按 GB/T 32918.2-2016 实现，
曲线为 sm2p256v1。本文件已用国标测试向量自检（见 tests/test_gm_crypto.py）。

若日后要换成 gmssl 或硬件密码模块，只需替换本文件的四个公开函数，
接口字段名（algorithm='SM2'、hash 前缀 'sm3:'）保持不变。
"""
import hashlib
import os
import secrets
from typing import NamedTuple

# ---------------------------------------------------------------- SM3

_IV = [
    0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
    0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E,
]
_MASK = 0xFFFFFFFF


def _rotl(x: int, n: int) -> int:
    n &= 31
    return ((x << n) | (x >> (32 - n))) & _MASK


def _ff(j: int, x: int, y: int, z: int) -> int:
    return (x ^ y ^ z) if j < 16 else ((x & y) | (x & z) | (y & z))


def _gg(j: int, x: int, y: int, z: int) -> int:
    return (x ^ y ^ z) if j < 16 else ((x & y) | (~x & z))


def _p0(x: int) -> int:
    return x ^ _rotl(x, 9) ^ _rotl(x, 17)


def _p1(x: int) -> int:
    return x ^ _rotl(x, 15) ^ _rotl(x, 23)


def _cf(v: list[int], block: bytes) -> list[int]:
    w = [int.from_bytes(block[i * 4:i * 4 + 4], "big") for i in range(16)]
    for j in range(16, 68):
        w.append(
            _p1(w[j - 16] ^ w[j - 9] ^ _rotl(w[j - 3], 15)) ^ _rotl(w[j - 13], 7) ^ w[j - 6]
        )
    w1 = [w[j] ^ w[j + 4] for j in range(64)]

    a, b, c, d, e, f, g, h = v
    for j in range(64):
        t = 0x79CC4519 if j < 16 else 0x7A879D8A
        ss1 = _rotl((_rotl(a, 12) + e + _rotl(t, j)) & _MASK, 7)
        ss2 = ss1 ^ _rotl(a, 12)
        tt1 = (_ff(j, a, b, c) + d + ss2 + w1[j]) & _MASK
        tt2 = (_gg(j, e, f, g) + h + ss1 + w[j]) & _MASK
        d = c
        c = _rotl(b, 9)
        b = a
        a = tt1
        h = g
        g = _rotl(f, 19)
        f = e
        e = _p0(tt2)
    return [x ^ y for x, y in zip(v, [a, b, c, d, e, f, g, h])]


def sm3(data: bytes) -> bytes:
    """SM3 摘要，返回 32 字节。"""
    msg = bytearray(data)
    bit_len = len(data) * 8
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0x00)
    msg += bit_len.to_bytes(8, "big")

    v = _IV[:]
    for i in range(0, len(msg), 64):
        v = _cf(v, bytes(msg[i:i + 64]))
    return b"".join(x.to_bytes(4, "big") for x in v)


def sm3_hex(data: bytes | str) -> str:
    """SM3 摘要的十六进制字符串（64 字符，不带前缀）。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return sm3(data).hex()


def sm3_tag(data: bytes | str) -> str:
    """带前缀的摘要，格式 'sm3:xxxx'。契约里 payload_hash / hash 字段一律用这个格式。"""
    return "sm3:" + sm3_hex(data)


# ---------------------------------------------------------------- SM2 椭圆曲线

_P = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF
_A = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFC
_B = 0x28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93
_N = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123
_GX = 0x32C4AE2C1F1981195F9904466A39C9948FE30BBFF2660BE1715A4589334C74C7
_GY = 0xBC3736A2F4F6779C59BDCEE36B692153D0A9877CC62A474002DF32E52139F0A0
_G = (_GX, _GY)

# 国标默认用户标识
_DEFAULT_ID = b"1234567812345678"

Point = tuple[int, int] | None


class _Curve(NamedTuple):
    """短 Weierstrass 曲线 y² = x³ + ax + b (mod p)，阶 n，基点 (gx, gy)。"""

    p: int
    a: int
    b: int
    n: int
    gx: int
    gy: int

    @property
    def g(self) -> Point:
        return (self.gx, self.gy)


SM2_CURVE = _Curve(_P, _A, _B, _N, _GX, _GY)

# secp256r1 / NIST P-256：契约 2.3 的 algorithm='ECC' 用它做 ECDSA。
# 选它的原因：同为 256 位短 Weierstrass 曲线，可以复用下面同一套点运算，
# 不引入任何 C 扩展依赖（交付要打 arm64 离线包，见文件头注释）。
ECC_CURVE = _Curve(
    p=0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF,
    a=0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFC,
    b=0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B,
    n=0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551,
    gx=0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    gy=0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
)


def _inv(x: int, m: int) -> int:
    return pow(x, m - 2, m)


def _ec_add(curve: _Curve, p1: Point, p2: Point) -> Point:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    mod = curve.p
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % mod == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + curve.a) * _inv(2 * y1 % mod, mod) % mod
    else:
        lam = (y2 - y1) * _inv((x2 - x1) % mod, mod) % mod
    x3 = (lam * lam - x1 - x2) % mod
    y3 = (lam * (x1 - x3) - y1) % mod
    return (x3, y3)


def _ec_mul(curve: _Curve, k: int, p: Point) -> Point:
    """二进制展开的标量乘。演示级性能足够（单次约 1ms 量级）。"""
    result: Point = None
    addend = p
    while k:
        if k & 1:
            result = _ec_add(curve, result, addend)
        addend = _ec_add(curve, addend, addend)
        k >>= 1
    return result


def _ec_on_curve(curve: _Curve, p: Point) -> bool:
    if p is None:
        return False
    x, y = p
    return (y * y - (x * x * x + curve.a * x + curve.b)) % curve.p == 0


# SM2 曲线上的简写，保持原有调用点不变
def _point_add(p1: Point, p2: Point) -> Point:
    return _ec_add(SM2_CURVE, p1, p2)


def _point_mul(k: int, p: Point) -> Point:
    return _ec_mul(SM2_CURVE, k, p)


def _on_curve(p: Point) -> bool:
    return _ec_on_curve(SM2_CURVE, p)


# ---------------------------------------------------------------- SM2 对外接口

def generate_keypair() -> tuple[str, str]:
    """生成 SM2 密钥对。

    返回 (public_key_hex, private_key_hex)：
      - 公钥为未压缩格式 '04' + X(64hex) + Y(64hex)，共 130 字符，契约示例即此格式
      - 私钥为 64 位十六进制
    """
    while True:
        d = secrets.randbelow(_N - 1) + 1
        pub = _point_mul(d, _G)
        if pub is not None:
            break
    x, y = pub
    return "04" + f"{x:064x}" + f"{y:064x}", f"{d:064x}"


def _parse_public_key(public_key: str) -> Point:
    pk = public_key.lower().removeprefix("0x")
    if pk.startswith("04"):
        pk = pk[2:]
    if len(pk) != 128:
        raise ValueError("SM2 公钥格式非法，应为 04 + 128 位十六进制")
    return int(pk[:64], 16), int(pk[64:], 16)


def _za(public_key: str, user_id: bytes = _DEFAULT_ID) -> bytes:
    """计算 ZA = SM3(ENTL || ID || a || b || Gx || Gy || Px || Py)。"""
    x, y = _parse_public_key(public_key)
    entl = (len(user_id) * 8).to_bytes(2, "big")
    buf = (
        entl + user_id
        + _A.to_bytes(32, "big") + _B.to_bytes(32, "big")
        + _GX.to_bytes(32, "big") + _GY.to_bytes(32, "big")
        + x.to_bytes(32, "big") + y.to_bytes(32, "big")
    )
    return sm3(buf)


def _digest(message: bytes | str, public_key: str) -> int:
    if isinstance(message, str):
        message = message.encode("utf-8")
    return int.from_bytes(sm3(_za(public_key) + message), "big")


def sign(message: bytes | str, private_key: str, public_key: str | None = None) -> str:
    """SM2 签名，返回 128 位十六进制字符串（r || s）。"""
    d = int(private_key.lower().removeprefix("0x"), 16)
    if public_key is None:
        pub = _point_mul(d, _G)
        public_key = "04" + f"{pub[0]:064x}" + f"{pub[1]:064x}"
    e = _digest(message, public_key)

    while True:
        k = secrets.randbelow(_N - 1) + 1
        point = _point_mul(k, _G)
        if point is None:
            continue
        x1 = point[0]
        r = (e + x1) % _N
        if r == 0 or r + k == _N:
            continue
        s = _inv((1 + d) % _N, _N) * (k - r * d) % _N
        if s == 0:
            continue
        return f"{r:064x}{s:064x}"


def verify(message: bytes | str, signature: str, public_key: str) -> bool:
    """SM2 验签。任何格式异常一律返回 False，不抛异常（调用方按 code 1004 处理）。"""
    try:
        sig = signature.lower().removeprefix("0x")
        if len(sig) != 128:
            return False
        r = int(sig[:64], 16)
        s = int(sig[64:], 16)
        if not (1 <= r < _N and 1 <= s < _N):
            return False

        pub = _parse_public_key(public_key)
        if not _on_curve(pub):
            return False

        e = _digest(message, public_key)
        t = (r + s) % _N
        if t == 0:
            return False
        point = _point_add(_point_mul(s, _G), _point_mul(t, pub))
        if point is None:
            return False
        return (e + point[0]) % _N == r
    except Exception:
        return False


# ---------------------------------------------------------------- ECC（secp256r1 上的 ECDSA）

def ecdsa_generate_keypair() -> tuple[str, str]:
    """生成 secp256r1 密钥对，格式与 SM2 一致（公钥 04+X+Y，私钥 64 hex）。"""
    curve = ECC_CURVE
    while True:
        d = secrets.randbelow(curve.n - 1) + 1
        pub = _ec_mul(curve, d, curve.g)
        if pub is not None:
            break
    x, y = pub
    return "04" + f"{x:064x}" + f"{y:064x}", f"{d:064x}"


def _ecdsa_digest(message: bytes | str) -> int:
    """ECDSA 用 SHA-256（RFC 6979 / FIPS 186-4）。曲线阶为 256 位，摘要无需截断。"""
    if isinstance(message, str):
        message = message.encode("utf-8")
    return int.from_bytes(hashlib.sha256(message).digest(), "big")


def ecdsa_sign(message: bytes | str, private_key: str) -> str:
    """ECDSA 签名，返回 128 位十六进制（r || s），与 SM2 签名长度一致。"""
    curve = ECC_CURVE
    d = int(private_key.lower().removeprefix("0x"), 16)
    e = _ecdsa_digest(message)
    while True:
        k = secrets.randbelow(curve.n - 1) + 1
        point = _ec_mul(curve, k, curve.g)
        if point is None:
            continue
        r = point[0] % curve.n
        if r == 0:
            continue
        s = pow(k, -1, curve.n) * (e + r * d) % curve.n
        if s == 0:
            continue
        return f"{r:064x}{s:064x}"


def ecdsa_verify(message: bytes | str, signature: str, public_key: str) -> bool:
    """ECDSA 验签。任何格式异常一律返回 False，不抛异常。"""
    try:
        curve = ECC_CURVE
        sig = signature.lower().removeprefix("0x")
        if len(sig) != 128:
            return False
        r = int(sig[:64], 16)
        s = int(sig[64:], 16)
        if not (1 <= r < curve.n and 1 <= s < curve.n):
            return False

        pub = _parse_public_key(public_key)
        if not _ec_on_curve(curve, pub):
            return False

        e = _ecdsa_digest(message)
        w = pow(s, -1, curve.n)
        point = _ec_add(
            curve,
            _ec_mul(curve, e * w % curve.n, curve.g),
            _ec_mul(curve, r * w % curve.n, pub),
        )
        if point is None:
            return False
        return point[0] % curve.n == r
    except Exception:
        return False


# ---------------------------------------------------------------- RSA（PKCS#1 v1.5 签名）

# 演示用 1024 位：纯 Python `pow()` 足够快（生成约 0.2s、签名约 3ms），
# 且签名 hex 恰好 256 字符、公钥 hex 恰好 256 字符，落在既有列宽
# （did_key.public_key VARCHAR(512)、algo_dispatch_task.signature VARCHAR(256)）之内，
# 不需要改表。要提到 2048 位只需改这个常量并同步放宽这两处列宽。
_RSA_BITS = 1024
_RSA_E = 65537
# DigestInfo(SHA-256) 的 DER 前缀，见 RFC 8017 9.2
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")

_SMALL_PRIMES = [
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
    73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151,
    157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229, 233,
    239, 241, 251,
]


def _is_probable_prime(n: int, rounds: int = 24) -> bool:
    """Miller-Rabin 素性检验（先做小素数试除，绝大多数候选一步就被筛掉）。"""
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int) -> int:
    """生成 bits 位素数。最高两位置 1，保证 p*q 恰好是 2*bits 位。"""
    while True:
        cand = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if (cand - 1) % _RSA_E and _is_probable_prime(cand):
            return cand


def rsa_generate_keypair() -> tuple[str, str]:
    """生成 RSA 密钥对，公钥指数固定 65537。

    返回 (public_key_hex, private_key_hex)：
      - 公钥为模数 n 的十六进制（_RSA_BITS/4 个字符），指数固定不入库
      - 私钥为 p||q 的十六进制（各 _RSA_BITS/8 个字符），自包含：
        n = p*q、d = e⁻¹ mod lcm(p-1, q-1) 都能由它算出来。
        存 p||q 而不是 d||n，是为了让 SM4 托管密文塞得进 did_key.private_key_enc(1024)。
    """
    half = _RSA_BITS // 2
    while True:
        p = _random_prime(half)
        q = _random_prime(half)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != _RSA_BITS:
            continue
        lam = (p - 1) * (q - 1) // _gcd(p - 1, q - 1)
        if _gcd(_RSA_E, lam) != 1:
            continue
        return f"{n:0{_RSA_BITS // 4}x}", f"{p:0{half // 4}x}{q:0{half // 4}x}"


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def _rsa_parse_private(private_key: str) -> tuple[int, int]:
    """私钥 hex（p||q）-> (n, d)。"""
    pk = private_key.lower().removeprefix("0x")
    if len(pk) % 2 or len(pk) < 32:
        raise ValueError("RSA 私钥格式非法")
    half = len(pk) // 2
    p = int(pk[:half], 16)
    q = int(pk[half:], 16)
    n = p * q
    lam = (p - 1) * (q - 1) // _gcd(p - 1, q - 1)
    return n, pow(_RSA_E, -1, lam)


def _rsa_pkcs1_v15_encode(message: bytes | str, key_len: int) -> bytes:
    """EMSA-PKCS1-v1_5：0x00 || 0x01 || 0xFF... || 0x00 || DigestInfo(SHA-256)。"""
    if isinstance(message, str):
        message = message.encode("utf-8")
    digest_info = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if key_len < len(digest_info) + 11:
        raise ValueError("RSA 密钥长度不足以承载 SHA-256 签名")
    padding = b"\xff" * (key_len - len(digest_info) - 3)
    return b"\x00\x01" + padding + b"\x00" + digest_info


def rsa_sign(message: bytes | str, private_key: str) -> str:
    """RSA PKCS#1 v1.5 + SHA-256 签名，返回 key_len*2 位十六进制。"""
    n, d = _rsa_parse_private(private_key)
    key_len = (n.bit_length() + 7) // 8
    em = _rsa_pkcs1_v15_encode(message, key_len)
    sig = pow(int.from_bytes(em, "big"), d, n)
    return f"{sig:0{key_len * 2}x}"


def rsa_verify(message: bytes | str, signature: str, public_key: str) -> bool:
    """RSA 验签。任何格式异常一律返回 False，不抛异常。"""
    try:
        n = int(public_key.lower().removeprefix("0x"), 16)
        key_len = (n.bit_length() + 7) // 8
        sig = signature.lower().removeprefix("0x")
        if len(sig) != key_len * 2:
            return False
        s = int(sig, 16)
        if not (0 <= s < n):
            return False
        em = pow(s, _RSA_E, n).to_bytes(key_len, "big")
        return secrets.compare_digest(em, _rsa_pkcs1_v15_encode(message, key_len))
    except Exception:
        return False


# ---------------------------------------------------------------- 多算法统一入口（密钥表 algorithm 字段用）

# 契约 2.3：algorithm 取值 SM2（默认）| ECC | RSA。三种都能签能验，
# 不允许出现「能生成但验不了」的算法——那会让绑了该密钥的身份无法可信接入。
SIGNATURE_ALGORITHMS = ("SM2", "ECC", "RSA")


def generate_keypair_by_algorithm(algorithm: str = "SM2") -> tuple[str, str]:
    """按算法生成密钥对，返回 (public_key_hex, private_key_hex)。"""
    alg = (algorithm or "SM2").upper()
    if alg == "SM2":
        return generate_keypair()
    if alg == "ECC":
        return ecdsa_generate_keypair()
    if alg == "RSA":
        return rsa_generate_keypair()
    raise ValueError(f"不支持的密钥算法 {algorithm}，可选 {' / '.join(SIGNATURE_ALGORITHMS)}")


def sign_by_algorithm(message: bytes | str, private_key: str, algorithm: str = "SM2",
                      public_key: str | None = None) -> str:
    """按算法签名。algorithm 非法时抛 ValueError（调用方是平台自己，属编程错误）。"""
    alg = (algorithm or "SM2").upper()
    if alg == "SM2":
        return sign(message, private_key, public_key)
    if alg == "ECC":
        return ecdsa_sign(message, private_key)
    if alg == "RSA":
        return rsa_sign(message, private_key)
    raise ValueError(f"不支持的签名算法 {algorithm}，可选 {' / '.join(SIGNATURE_ALGORITHMS)}")


def verify_by_algorithm(message: bytes | str, signature: str, public_key: str,
                        algorithm: str = "SM2") -> bool:
    """按算法验签。算法未知或任何格式异常一律返回 False，绝不抛异常。"""
    alg = (algorithm or "SM2").upper()
    if alg == "SM2":
        return verify(message, signature, public_key)
    if alg == "ECC":
        return ecdsa_verify(message, signature, public_key)
    if alg == "RSA":
        return rsa_verify(message, signature, public_key)
    return False


def sha256_hex(data: bytes | str) -> str:
    """内部用途（如 nonce 去重键），业务摘要一律用 SM3。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- 存证摘要规范

def canonical_json(payload) -> str:
    """存证摘要的唯一规范化方式。

    键排序 + 无多余空格 + 不转义中文。任何地方计算 payload_hash 都必须走这里，
    否则同一份数据在不同时刻会算出不同摘要，完整性校验就会误报篡改。
    """
    import json as _json

    return _json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(payload) -> str:
    """业务数据摘要，格式 'sm3:xxxx'。"""
    return sm3_tag(canonical_json(payload))


def block_hash(prev_hash: str, payload_hash_value: str, timestamp: str) -> str:
    """区块摘要：SM3(prev_hash + payload_hash + timestamp)。

    timestamp 统一用 ISO 8601 秒级字符串（如 2026-08-17T14:23:05+08:00）。
    """
    return sm3_tag(f"{prev_hash}{payload_hash_value}{timestamp}")


# ---------------------------------------------------------------- SM4 分组密码

_SM4_SBOX = bytes([
    0xd6, 0x90, 0xe9, 0xfe, 0xcc, 0xe1, 0x3d, 0xb7, 0x16, 0xb6, 0x14, 0xc2, 0x28, 0xfb, 0x2c, 0x05,
    0x2b, 0x67, 0x9a, 0x76, 0x2a, 0xbe, 0x04, 0xc3, 0xaa, 0x44, 0x13, 0x26, 0x49, 0x86, 0x06, 0x99,
    0x9c, 0x42, 0x50, 0xf4, 0x91, 0xef, 0x98, 0x7a, 0x33, 0x54, 0x0b, 0x43, 0xed, 0xcf, 0xac, 0x62,
    0xe4, 0xb3, 0x1c, 0xa9, 0xc9, 0x08, 0xe8, 0x95, 0x80, 0xdf, 0x94, 0xfa, 0x75, 0x8f, 0x3f, 0xa6,
    0x47, 0x07, 0xa7, 0xfc, 0xf3, 0x73, 0x17, 0xba, 0x83, 0x59, 0x3c, 0x19, 0xe6, 0x85, 0x4f, 0xa8,
    0x68, 0x6b, 0x81, 0xb2, 0x71, 0x64, 0xda, 0x8b, 0xf8, 0xeb, 0x0f, 0x4b, 0x70, 0x56, 0x9d, 0x35,
    0x1e, 0x24, 0x0e, 0x5e, 0x63, 0x58, 0xd1, 0xa2, 0x25, 0x22, 0x7c, 0x3b, 0x01, 0x21, 0x78, 0x87,
    0xd4, 0x00, 0x46, 0x57, 0x9f, 0xd3, 0x27, 0x52, 0x4c, 0x36, 0x02, 0xe7, 0xa0, 0xc4, 0xc8, 0x9e,
    0xea, 0xbf, 0x8a, 0xd2, 0x40, 0xc7, 0x38, 0xb5, 0xa3, 0xf7, 0xf2, 0xce, 0xf9, 0x61, 0x15, 0xa1,
    0xe0, 0xae, 0x5d, 0xa4, 0x9b, 0x34, 0x1a, 0x55, 0xad, 0x93, 0x32, 0x30, 0xf5, 0x8c, 0xb1, 0xe3,
    0x1d, 0xf6, 0xe2, 0x2e, 0x82, 0x66, 0xca, 0x60, 0xc0, 0x29, 0x23, 0xab, 0x0d, 0x53, 0x4e, 0x6f,
    0xd5, 0xdb, 0x37, 0x45, 0xde, 0xfd, 0x8e, 0x2f, 0x03, 0xff, 0x6a, 0x72, 0x6d, 0x6c, 0x5b, 0x51,
    0x8d, 0x1b, 0xaf, 0x92, 0xbb, 0xdd, 0xbc, 0x7f, 0x11, 0xd9, 0x5c, 0x41, 0x1f, 0x10, 0x5a, 0xd8,
    0x0a, 0xc1, 0x31, 0x88, 0xa5, 0xcd, 0x7b, 0xbd, 0x2d, 0x74, 0xd0, 0x12, 0xb8, 0xe5, 0xb4, 0xb0,
    0x89, 0x69, 0x97, 0x4a, 0x0c, 0x96, 0x77, 0x7e, 0x65, 0xb9, 0xf1, 0x09, 0xc5, 0x6e, 0xc6, 0x84,
    0x18, 0xf0, 0x7d, 0xec, 0x3a, 0xdc, 0x4d, 0x20, 0x79, 0xee, 0x5f, 0x3e, 0xd7, 0xcb, 0x39, 0x48,
])

_SM4_FK = (0xA3B1BAC6, 0x56AA3350, 0x677D9197, 0xB27022DC)
def _sm4_ck(i: int) -> int:
    """固定参数 CK[i]，按国标由 (4i+j)*7 mod 256 逐字节生成。"""
    return sum(((4 * i + j) * 7 % 256) << (24 - 8 * j) for j in range(4))


def _sm4_tau(x: int) -> int:
    return (
        (_SM4_SBOX[(x >> 24) & 0xFF] << 24)
        | (_SM4_SBOX[(x >> 16) & 0xFF] << 16)
        | (_SM4_SBOX[(x >> 8) & 0xFF] << 8)
        | _SM4_SBOX[x & 0xFF]
    )


def _sm4_t(x: int) -> int:
    b = _sm4_tau(x)
    return b ^ _rotl(b, 2) ^ _rotl(b, 10) ^ _rotl(b, 18) ^ _rotl(b, 24)


def _sm4_t_prime(x: int) -> int:
    b = _sm4_tau(x)
    return b ^ _rotl(b, 13) ^ _rotl(b, 23)


def _sm4_round_keys(key: bytes) -> list[int]:
    if len(key) != 16:
        raise ValueError("SM4 密钥必须是 16 字节")
    k = [int.from_bytes(key[i * 4:i * 4 + 4], "big") ^ _SM4_FK[i] for i in range(4)]
    rk = []
    for i in range(32):
        k.append(k[i] ^ _sm4_t_prime(k[i + 1] ^ k[i + 2] ^ k[i + 3] ^ _sm4_ck(i)))
        rk.append(k[i + 4])
    return rk


def _sm4_crypt_block(block: bytes, rk: list[int]) -> bytes:
    x = [int.from_bytes(block[i * 4:i * 4 + 4], "big") for i in range(4)]
    for i in range(32):
        x.append(x[i] ^ _sm4_t(x[i + 1] ^ x[i + 2] ^ x[i + 3] ^ rk[i]))
    return b"".join(x[35 - i].to_bytes(4, "big") for i in range(4))


def sm4_encrypt_block(block: bytes, key: bytes) -> bytes:
    return _sm4_crypt_block(block, _sm4_round_keys(key))


def sm4_decrypt_block(block: bytes, key: bytes) -> bytes:
    return _sm4_crypt_block(block, _sm4_round_keys(key)[::-1])


def _pkcs7_pad(data: bytes) -> bytes:
    pad = 16 - len(data) % 16
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise ValueError("密文长度非法")
    pad = data[-1]
    if pad < 1 or pad > 16 or data[-pad:] != bytes([pad]) * pad:
        raise ValueError("填充非法，密钥错误或密文被篡改")
    return data[:-pad]


def sm4_cbc_encrypt(plaintext: bytes | str, key: bytes) -> str:
    """SM4-CBC 加密，返回 hex(iv + 密文)。IV 每次随机。"""
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    iv = os.urandom(16)
    rk = _sm4_round_keys(key)
    padded = _pkcs7_pad(plaintext)
    prev = iv
    out = bytearray()
    for i in range(0, len(padded), 16):
        block = padded[i:i + 16]
        xored = bytes(a ^ b for a, b in zip(block, prev))
        prev = _sm4_crypt_block(xored, rk)
        out += prev
    return (iv + bytes(out)).hex()


def sm4_cbc_decrypt(ciphertext_hex: str, key: bytes) -> bytes:
    raw = bytes.fromhex(ciphertext_hex)
    iv, body = raw[:16], raw[16:]
    rk = _sm4_round_keys(key)[::-1]
    prev = iv
    out = bytearray()
    for i in range(0, len(body), 16):
        block = body[i:i + 16]
        out += bytes(a ^ b for a, b in zip(_sm4_crypt_block(block, rk), prev))
        prev = block
    return _pkcs7_unpad(bytes(out))


def derive_key(secret: str, salt: str = "energy-tds-key-custody") -> bytes:
    """由部署密钥派生 16 字节 SM4 密钥。"""
    return sm3((salt + secret).encode("utf-8"))[:16]
