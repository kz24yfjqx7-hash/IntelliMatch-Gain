# -*- coding: utf-8 -*-
"""生成 backend/sql/02_seed.sql。

在开发机上跑一次，产物是纯 SQL，目标机不需要 Python。
密钥、DID、存证哈希链全部真实计算，保证种子数据本身就能通过完整性校验。
"""
import json
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/home/stu/Huang_Hao/Zhi_softwire/backend")
from core.gm_crypto import (  # noqa: E402
    block_hash, derive_key, generate_keypair, payload_hash, sm3_hex, sm3_tag, sm4_cbc_encrypt,
)

import bcrypt  # noqa: E402

# 确定性生成：同一份脚本每次跑出完全相同的 SQL，
# 否则每次重跑都会换掉全部 DID 和密码哈希，代码评审时 diff 全红。
random.seed(20260817)
_rng = random.Random(20260817)
import secrets as _secrets  # noqa: E402
_secrets.randbelow = lambda n: _rng.randrange(n)  # 让 SM2 密钥生成可复现

CST = timezone(timedelta(hours=8))
BASE = datetime(2026, 8, 1, 9, 0, 0, tzinfo=CST)

out = []
W = out.append


def q(v):
    """SQL 值序列化。"""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


def dt(d):
    return d.strftime("%Y-%m-%d %H:%M:%S")


def iso(d):
    return d.isoformat(timespec="seconds")


_SALT_CHARS = "./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def bcrypt_hash(pwd):
    """确定性 bcrypt：盐由固定种子生成，保证多次生成的 SQL 完全一致。

    这是演示账号的种子数据，盐可复现不构成风险；真实用户注册走 core/security.py 的随机盐。
    """
    # bcrypt 的盐是 16 字节 = 128 位，用 base64 编成 22 个字符共 132 位，
    # 所以最后一个字符只有高 2 位是有效的，必须落在 . O e u 这四个上。
    # 随便取 22 个字符有 60/64 的概率造出非法盐，bcrypt 4.x 会直接 ValueError: Invalid salt。
    尾 = "".join(_rng.choice(_SALT_CHARS) for _ in range(21))
    salt = ("$2b$10$" + 尾 + _rng.choice(".Oeu")).encode()
    return bcrypt.hashpw(pwd.encode(), salt).decode()


# ============================================================ 哈希链
class Chain:
    def __init__(self):
        self.height = 0
        self.prev = "sm3:" + "0" * 64
        self.rows = []
        genesis_payload = {"genesis": True, "platform": "energy-tds", "version": "1.0"}
        self._append("data", "genesis", None, genesis_payload, BASE, is_genesis=True)

    def _append(self, category, ref_id, actor, payload, ts, is_genesis=False):
        ph = payload_hash(payload)
        bh = block_hash(self.prev, ph, iso(ts))
        eid = f"ev-{self.height:06d}"
        self.rows.append({
            "evidence_id": eid, "category": category, "ref_id": str(ref_id),
            "actor_did": actor, "payload_hash": ph, "prev_hash": self.prev,
            "block_hash": bh, "block_height": self.height,
            "tx_id": f"blk-{self.height:06d}-0", "trace_id": f"tr-{ts:%Y%m%d}-{sm3_hex(eid)[:8]}",
            "payload_snapshot": payload, "created_at": ts,
        })
        self.prev = bh
        self.height += 1
        return eid

    def add(self, category, ref_id, actor, payload, ts):
        return self._append(category, ref_id, actor, payload, ts)


chain = Chain()

# ============================================================ 角色
ROLES = [
    ("sys_admin", "系统管理员", "平台最高权限，负责身份签发、权限审批与安全审计"),
    ("grid_dispatcher", "电网调度员", "负责调度策略生成与指令下发"),
    ("vpp_operator", "虚拟电厂运营商", "负责聚合资源与数据登记"),
    ("energy_subject", "能源主体", "数据提供方，仅能操作自有数据"),
    ("regulator", "监管方", "只读与导出，用于合规检查"),
    ("edge_node", "边缘节点", "终端设备身份，上报数据与接收指令"),
]

# 角色-资源-操作-数据范围（严格照 contract/DB-SCHEMA.md 的权限矩阵）
PERMS = {
    "sys_admin": [("asset", "read", "all"), ("asset", "write", "all"), ("asset", "export", "all"),
                  ("model", "read", "all"), ("model", "write", "all"),
                  ("dispatch", "read", "all"), ("dispatch", "issue", "all"),
                  ("evidence", "read", "all"), ("evidence", "write", "all"),
                  ("algo", "execute", "all"), ("user", "manage", "all")],
    "grid_dispatcher": [("asset", "read", "all"), ("asset", "export", "all"),
                        ("model", "read", "all"), ("dispatch", "read", "all"),
                        ("dispatch", "issue", "all"), ("evidence", "read", "all"),
                        ("algo", "execute", "all")],
    "vpp_operator": [("asset", "read", "all"), ("asset", "write", "all"),
                     ("model", "read", "all"), ("dispatch", "read", "all"),
                     ("evidence", "read", "all")],
    "energy_subject": [("asset", "read", "own"), ("asset", "write", "own"),
                       ("evidence", "read", "own")],
    "regulator": [("asset", "read", "all"), ("asset", "export", "all"),
                  ("model", "read", "all"), ("dispatch", "read", "all"),
                  ("evidence", "read", "all")],
    "edge_node": [("asset", "read", "own"), ("asset", "write", "own"),
                  ("model", "read", "all"), ("dispatch", "read", "all")],
}

# ============================================================ DID 与密钥
identities = []   # dict
keys = []


CUSTODY_KEY = derive_key("energy-tds-key-custody-2026")


def make_did(subject_type, subject_name, org, controller, metadata, ts, status="active"):
    pub, priv_hex = generate_keypair()
    priv = priv_hex
    did = f"did:vpp:{subject_type}:0x{sm3_hex(bytes.fromhex(pub))[:32]}"
    doc = {
        "@context": "https://w3id.org/did/v1",
        "id": did,
        "controller": controller or did,
        "verificationMethod": [{
            "id": f"{did}#key-1",
            "type": "SM2VerificationKey2023",
            "controller": did,
            "publicKeyHex": pub,
        }],
        "authentication": [f"{did}#key-1"],
        "created": iso(ts),
    }
    identities.append({
        "did": did, "subject_type": subject_type, "subject_name": subject_name,
        "org_name": org, "controller_did": controller, "did_document": doc,
        "status": status, "metadata": metadata, "created_at": ts,
    })
    keys.append({
        "did": did, "algorithm": "SM2", "public_key": pub,
        "key_hash": sm3_tag(pub), "status": "active", "version": 1,
        "bound_at": ts, "expire_at": ts + timedelta(days=730),
        # 演示账号的私钥由平台托管（SM4-CBC 加密），否则答辩现场没有任何一把私钥可以签名。
        # 加密密钥来自 KEY_CUSTODY_SECRET，与每次安装随机生成的 JWT_SECRET 分开。
        "private_key_enc": sm4_cbc_encrypt(priv_hex, CUSTODY_KEY),
    })
    ev = chain.add("identity", did, controller, {
        "action": "did:register", "did": did, "subjectType": subject_type,
        "subjectName": subject_name, "publicKey": pub, "createdAt": iso(ts),
    }, ts)
    return did, ev, priv


# 平台运营方（根组织，充当所有身份的 controller）
t = BASE
ORG_DID, ORG_EV, _ = make_did("org", "平台运营方", "能源可信数据空间平台", None,
                              {"orgCode": "91110000MA01ENERGY", "role": "platform"}, t)

USERS = [
    ("admin", "admin123", "系统管理员", "平台运营方", "sys_admin"),
    ("grid", "grid123", "电网调度员", "国网XX供电公司", "grid_dispatcher"),
    ("vpp", "vpp123", "虚拟电厂运营商", "XX虚拟电厂运营有限公司", "vpp_operator"),
    ("subject", "subject123", "能源主体", "XX工业园区", "energy_subject"),
    ("regulator", "reg123", "监管方", "XX能源监管局", "regulator"),
    ("edge", "edge123", "边缘节点", "XX园区边缘侧", "edge_node"),
]
user_rows = []
for i, (uname, pwd, real, org, role) in enumerate(USERS):
    t = BASE + timedelta(minutes=5 * (i + 1))
    did, ev, _ = make_did("user", real, org, ORG_DID, {"username": uname, "role": role}, t)
    user_rows.append({
        "id": i + 1, "username": uname, "password_hash": bcrypt_hash(pwd),
        "real_name": real, "org_name": org, "did": did, "role": role,
        "email": f"{uname}@energy-tds.local", "phone": f"1380000{i:04d}",
        "created_at": t,
    })

# 四个边缘节点（与 raspi/public/data/mock.json 对齐）
NODES = [
    ("Node-A", "虚拟电厂节点A", "online", "VPP-2000", 45.3, -12.0, 120.0, 65.0, "A区厂房屋顶", 200.0),
    ("Node-B", "虚拟电厂节点B", "online", "VPP-2000", 32.1, 8.5, 85.0, 78.0, "B区综合楼", 200.0),
    ("Node-C", "虚拟电厂节点C", "warning", "VPP-3000", 28.7, -25.3, 150.0, 42.0, "C区数据中心", 300.0),
    ("Node-D", "虚拟电厂节点D", "online", "VPP-2000", 38.9, 5.2, 95.0, 82.0, "D区仓储中心", 200.0),
]
node_rows = []
for i, n in enumerate(NODES):
    t = BASE + timedelta(hours=1, minutes=10 * i)
    did, ev, _ = make_did("edge", n[1], "XX工业园区", ORG_DID,
                          {"model": n[3], "location": n[8], "capacityKw": n[9]}, t)
    node_rows.append({
        "id": n[0], "name": n[1], "status": n[2], "model": n[3], "did": did,
        "pv": n[4], "storage": n[5], "load": n[6], "soc": n[7],
        "location": n[8], "capacity": n[9], "created_at": t,
    })

# 现场演示前已存在的设备身份（其中一个已冻结，用于演示 R02 异常 DID）
DEVICES = [
    ("光伏逆变器-A01", "VPP-2000", "A区厂房屋顶", "active"),
    ("光伏逆变器-A02", "VPP-2000", "A区厂房屋顶", "active"),
    ("储能PCS-B01", "VPP-2000", "B区综合楼", "active"),
    ("智能电表-C01", "VPP-3000", "C区数据中心", "active"),
    ("风机控制器-D01", "VPP-2000", "D区仓储中心", "active"),
    ("退役采集终端-X09", "VPP-1000", "X区（已退役）", "frozen"),
]
device_rows = []
for i, (name, model, loc, status) in enumerate(DEVICES):
    t = BASE + timedelta(hours=2, minutes=15 * i)
    did, ev, _ = make_did("device", name, "XX工业园区", ORG_DID,
                          {"model": model, "location": loc}, t, status=status)
    device_rows.append({"did": did, "name": name, "model": model, "created_at": t})

all_source_dids = [n["did"] for n in node_rows] + [d["did"] for d in device_rows[:5]]

# ============================================================ 数据资产
DATA_TYPES = ["pv"] * 30 + ["load"] * 20 + ["storage"] * 12 + ["wind"] * 10 + ["dispatch"] * 8
LEVELS = ["L1"] * 12 + ["L2"] * 30 + ["L3"] * 28 + ["L4"] * 10
random.shuffle(LEVELS)
TYPE_CN = {"pv": "光伏出力", "load": "负荷", "storage": "储能", "wind": "风电出力", "dispatch": "调度指令"}
LEVEL_REASON = {
    "L1": "仅含聚合后统计量，不含主体标识，判定为公开级",
    "L2": "含设备级运行数据，限内部共享，判定为内部级",
    "L3": "含地理位置字段且采集粒度为分钟级，判定为敏感级",
    "L4": "关联调度指令与主体身份，涉及电网运行安全，判定为核心级",
}

asset_rows = []
for i in range(80):
    dtp = DATA_TYPES[i]
    lvl = LEVELS[i]
    src = all_source_dids[i % len(all_source_dids)]
    day = BASE + timedelta(days=i // 6, hours=3 + (i % 6) * 2, minutes=17)
    payload = {
        "ts": iso(day),
        "dataType": dtp,
        "interval": "1min",
        "records": 1440,
        "summary": {
            "avg": round(random.uniform(10, 160), 2),
            "max": round(random.uniform(160, 260), 2),
            "min": round(random.uniform(0, 10), 2),
        },
    }
    if dtp == "storage":
        payload["socRange"] = [round(random.uniform(20, 40), 1), round(random.uniform(70, 95), 1)]
    if lvl in ("L3", "L4"):
        payload["location"] = {"lat": round(39.9 + random.uniform(-0.1, 0.1), 5),
                               "lng": round(116.4 + random.uniform(-0.1, 0.1), 5)}
    ph = payload_hash(payload)
    ev = chain.add("data", i + 1001, src, {
        "action": "asset:register", "assetId": i + 1001, "dataType": dtp,
        "level": lvl, "payloadHash": ph, "sourceDid": src, "createdAt": iso(day),
    }, day)
    asset_rows.append({
        "id": i + 1001,
        "name": f"{[n['name'] for n in node_rows][i % 4]}{TYPE_CN[dtp]}-{day:%Y%m%d}-{i % 6 + 1:02d}",
        "data_type": dtp, "source_did": src, "owner_did": src, "level": lvl,
        "payload": payload, "payload_hash": ph,
        "description": f"{TYPE_CN[dtp]}分钟级采集数据，共 1440 条记录",
        "record_count": 1440,
        "auth_status": "authorized" if i % 2 == 0 else "unauthorized",
        "classify_score": round(random.uniform(0.62, 0.95), 3),
        "classify_reason": LEVEL_REASON[lvl],
        "chain_tx_id": f"blk-{chain.height - 1:06d}-0", "evidence_id": ev,
        "created_at": day,
    })

# 资产溯源：给前 8 条资产补齐完整生命周期
lineage_rows = []
for a in asset_rows[:8]:
    stages = [("register", a["created_at"], a["evidence_id"], "数据登记并生成 SM3 摘要上链")]
    t1 = a["created_at"] + timedelta(hours=6)
    ev1 = chain.add("permission", a["id"], user_rows[0]["did"], {
        "action": "asset:authorize", "assetId": a["id"], "approver": user_rows[0]["did"]}, t1)
    stages.append(("authorize", t1, ev1, "管理员审批通过，授予读权限"))
    t2 = t1 + timedelta(hours=2)
    ev2 = chain.add("data", a["id"], user_rows[2]["did"], {
        "action": "asset:access", "assetId": a["id"], "accessor": user_rows[2]["did"]}, t2)
    stages.append(("access", t2, ev2, "虚拟电厂运营商读取数据"))
    t3 = t2 + timedelta(hours=1)
    ev3 = chain.add("algo", a["id"], user_rows[1]["did"], {
        "action": "asset:compute", "assetId": a["id"], "usage": "联邦学习本地训练"}, t3)
    stages.append(("compute", t3, ev3, "参与联邦学习本地训练，原始数据不出域"))
    for stage, ts, ev, detail in stages:
        lineage_rows.append({"asset_id": a["id"], "stage": stage, "actor_did": a["source_did"],
                             "evidence_id": ev, "hash": a["payload_hash"], "detail": detail,
                             "created_at": ts})

# ============================================================ 权限申请与授权
app_rows, grant_rows, change_rows = [], [], []
APPLY_CASES = [
    (3, "asset", "1001", "read", "approved", "联合建模需要读取节点A光伏数据"),
    (3, "asset", "1005", "read", "approved", "负荷预测模型训练需要历史负荷数据"),
    (3, "asset", "1009", "export", "rejected", "拟导出全量原始数据用于外部分析"),
    (2, "asset", "1013", "read", "approved", "聚合调度需要读取储能运行数据"),
    (2, "model", "v11", "read", "approved", "查看联合建模产出的负荷预测模型"),
    (4, "asset", "1017", "read", "pending", "监管抽查需要调阅该资产明细"),
    (4, "evidence", "ev-000030", "read", "approved", "核验数据上链记录真实性"),
    (5, "asset", "1021", "read", "approved", "边缘节点需读取本地历史数据做自校准"),
    (2, "dispatch", "dp-000001", "issue", "rejected", "申请调度下发权限"),
    (3, "asset", "1025", "read", "pending", "拟开展跨主体联合分析"),
    (5, "asset", "1029", "write", "approved", "边缘侧补录缺失采样点"),
    (4, "asset", "1033", "export", "approved", "季度合规报告需导出汇总数据"),
]
uid_by_index = {i + 1: u for i, u in enumerate(user_rows)}
for i, (uidx, rtype, rid, act, status, reason) in enumerate(APPLY_CASES):
    applicant = uid_by_index[uidx + 1]  # 1-based，跳过 admin
    ts = BASE + timedelta(days=2 + i, hours=10, minutes=i * 7)
    ev = chain.add("permission", f"app-{i + 1}", applicant["did"], {
        "action": "permission:apply", "applicant": applicant["did"],
        "resourceType": rtype, "resourceId": rid, "operation": act, "reason": reason}, ts)
    approved_at = ts + timedelta(hours=3) if status in ("approved", "rejected") else None
    app_rows.append({
        "id": i + 1, "applicant_did": applicant["did"], "applicant_name": applicant["real_name"],
        "resource_type": rtype, "resource_id": rid, "action": act, "reason": reason,
        "status": status, "expire_at": ts + timedelta(days=30),
        "approver_did": user_rows[0]["did"] if approved_at else None,
        "approver_name": "系统管理员" if approved_at else None,
        "approve_reason": ("符合最小必要原则，予以授权" if status == "approved"
                           else "申请范围超出最小必要原则，予以驳回" if status == "rejected" else None),
        "approved_at": approved_at, "evidence_id": ev, "created_at": ts,
    })
    change_rows.append({"target_did": applicant["did"], "change_type": "apply",
                        "resource_type": rtype, "resource_id": rid, "action": act,
                        "operator_did": applicant["did"], "detail": reason,
                        "evidence_id": ev, "created_at": ts})
    if status == "approved":
        ev2 = chain.add("permission", f"grant-{len(grant_rows) + 1}", user_rows[0]["did"], {
            "action": "permission:grant", "grantee": applicant["did"],
            "resourceType": rtype, "resourceId": rid, "operation": act}, approved_at)
        grant_rows.append({
            "id": len(grant_rows) + 1, "application_id": i + 1,
            "grantee_did": applicant["did"], "grantee_name": applicant["real_name"],
            "resource_type": rtype, "resource_id": rid, "action": act,
            "status": "active", "granted_at": approved_at,
            "expire_at": approved_at + timedelta(days=30), "evidence_id": ev2,
        })
        change_rows.append({"target_did": applicant["did"], "change_type": "approve",
                            "resource_type": rtype, "resource_id": rid, "action": act,
                            "operator_did": user_rows[0]["did"], "detail": "审批通过并生成授权",
                            "evidence_id": ev2, "created_at": approved_at})
    elif status == "rejected":
        change_rows.append({"target_did": applicant["did"], "change_type": "reject",
                            "resource_type": rtype, "resource_id": rid, "action": act,
                            "operator_did": user_rows[0]["did"], "detail": "超出最小必要原则",
                            "evidence_id": ev, "created_at": approved_at})

# 一条已回收的授权，用于演示权限回收留痕
revoke_ts = BASE + timedelta(days=12, hours=15)
ev_rev = chain.add("permission", "grant-revoke", user_rows[0]["did"], {
    "action": "permission:revoke", "grantee": user_rows[2]["did"], "resourceId": "1013"}, revoke_ts)
grant_rows.append({
    "id": len(grant_rows) + 1, "application_id": None,
    "grantee_did": user_rows[2]["did"], "grantee_name": user_rows[2]["real_name"],
    "resource_type": "asset", "resource_id": "1037", "action": "read",
    "status": "revoked", "granted_at": revoke_ts - timedelta(days=5),
    "expire_at": revoke_ts + timedelta(days=25), "revoked_at": revoke_ts,
    "revoke_reason": "业务已结束，按最小必要原则回收", "evidence_id": ev_rev,
})
change_rows.append({"target_did": user_rows[2]["did"], "change_type": "revoke",
                    "resource_type": "asset", "resource_id": "1037", "action": "read",
                    "operator_did": user_rows[0]["did"], "detail": "业务结束回收授权",
                    "evidence_id": ev_rev, "created_at": revoke_ts})

# ============================================================ 联邦学习 / 调度 / AI
fl_ts = BASE + timedelta(days=14, hours=9)
fl_rounds = []
loss, acc, eps = 0.812, 0.612, 0.0
for r in range(1, 11):
    loss = round(loss * random.uniform(0.80, 0.90), 6)
    acc = round(min(0.965, acc + random.uniform(0.025, 0.045)), 4)
    eps = round(eps + random.uniform(0.08, 0.12), 4)
    ts = fl_ts + timedelta(minutes=3 * r)
    contributions = [{"nodeId": n["id"], "weight": round(w, 3), "localLoss": round(loss * random.uniform(0.95, 1.15), 4)}
                     for n, w in zip(node_rows, [0.31, 0.22, 0.29, 0.18])]
    ghash = sm3_tag(f"fl-000001:{r}:{loss}:{acc}")
    ev = chain.add("algo", "fl-000001", user_rows[1]["did"], {
        "action": "fl:round", "taskId": "fl-000001", "round": r, "loss": loss,
        "acc": acc, "gradientHash": ghash, "epsilonSpent": eps}, ts)
    fl_rounds.append({"round": r, "loss": loss, "acc": acc,
                      "compression_ratio": round(random.uniform(88.5, 92.5), 2),
                      "epsilon_spent": eps, "gradient_hash": ghash,
                      "node_contributions": contributions, "evidence_id": ev, "created_at": ts})

dp_ts = BASE + timedelta(days=15, hours=15)
dispatch_strategy = {
    "actions": [
        {"nodeId": "Node-C", "action": "discharge", "powerKw": 24.0, "qValue": 8.42,
         "reason": "负荷最高且SOC充足"},
        {"nodeId": "Node-A", "action": "idle", "powerKw": 0.0, "qValue": 5.21,
         "reason": "供需基本平衡，保持待机"},
        {"nodeId": "Node-D", "action": "charge", "powerKw": 12.0, "qValue": 6.03,
         "reason": "光伏富余且SOC未满，就地消纳"},
    ],
    "totalReward": 15.7,
    "timeWindow": "2026-08-16T15:00~16:00+08:00",
}
ev_dp = chain.add("algo", "dp-000001", user_rows[1]["did"], {
    "action": "dispatch:issue", "taskId": "dp-000001", "strategy": dispatch_strategy}, dp_ts)

# ============================================================ 告警
alert_rows = [
    ("al-000001", "R01_UNAUTHORIZED", "越权访问", "high",
     "5 分钟内累计 3 次权限校验拒绝，疑似越权探测", user_rows[2], "open"),
    ("al-000002", "R02_ABNORMAL_DID", "异常 DID 登录", "critical",
     "已冻结身份 退役采集终端-X09 尝试接入平台", None, "open"),
    ("al-000003", "R04_BULK_EXPORT", "批量数据导出", "medium",
     "10 分钟内连续导出 3 次，单次超过 1000 条", user_rows[4], "acked"),
    ("al-000004", "R05_SUSPICIOUS_GRAD", "可疑梯度上传", "high",
     "联邦学习第 7 轮检测到节点C梯度范数异常，已隔离该轮更新", user_rows[1], "acked"),
]

# ============================================================ 输出 SQL
W("-- ============================================================================")
W("-- 能源可信数据空间平台 · 种子数据")
W("-- 本文件由 backend/sql/gen_seed.py 生成，请勿手改；改数据请改生成脚本后重跑")
W("-- 目标：前端每个页面首次打开就有内容，不出现空表")
W("-- 存证哈希链为真实计算结果，可直接通过 /evidence/chain/status 完整性校验")
W("-- ============================================================================")
W("USE energy_tds;")
W("SET NAMES utf8mb4;")
W("")

W("-- ---------------------------------------------------------------- 角色")
W("INSERT INTO sys_role (code, name, description, is_builtin) VALUES")
W(",\n".join(f"  ({q(c)}, {q(n)}, {q(d)}, 1)" for c, n, d in ROLES) + ";")
W("")

W("-- ---------------------------------------------------------------- 角色权限矩阵")
vals = []
for role, plist in PERMS.items():
    for res, act, scope in plist:
        vals.append(f"  ({q(role)}, {q(res)}, {q(act)}, {q(scope)})")
W("INSERT INTO sys_role_permission (role_code, resource_type, action, scope) VALUES")
W(",\n".join(vals) + ";")
W("")

W("-- ---------------------------------------------------------------- 演示账号")
W("-- admin/admin123  grid/grid123  vpp/vpp123  subject/subject123  regulator/reg123  edge/edge123")
W("INSERT INTO sys_user (id, username, password_hash, real_name, org_name, did, phone, email, status, created_at) VALUES")
W(",\n".join(
    f"  ({u['id']}, {q(u['username'])}, {q(u['password_hash'])}, {q(u['real_name'])}, "
    f"{q(u['org_name'])}, {q(u['did'])}, {q(u['phone'])}, {q(u['email'])}, 'active', {q(dt(u['created_at']))})"
    for u in user_rows) + ";")
W("")
W("INSERT INTO sys_user_role (user_id, role_code) VALUES")
W(",\n".join(f"  ({u['id']}, {q(u['role'])})" for u in user_rows) + ";")
W("")

W("-- ---------------------------------------------------------------- DID 身份")
W("INSERT INTO did_identity (did, subject_type, subject_name, org_name, controller_did, did_document, status, metadata, created_at) VALUES")
W(",\n".join(
    f"  ({q(i['did'])}, {q(i['subject_type'])}, {q(i['subject_name'])}, {q(i['org_name'])}, "
    f"{q(i['controller_did'])}, {q(i['did_document'])}, {q(i['status'])}, {q(i['metadata'])}, {q(dt(i['created_at']))})"
    for i in identities) + ";")
W("")

W("-- ---------------------------------------------------------------- 密钥（私钥不落库，仅存公钥与指纹）")
W("INSERT INTO did_key (did, algorithm, public_key, key_hash, private_key_enc, custody, status, version, purpose, bound_at, expire_at, created_at) VALUES")
W(",\n".join(
    f"  ({q(k['did'])}, 'SM2', {q(k['public_key'])}, {q(k['key_hash'])}, {q(k['private_key_enc'])}, 1, "
    f"{q(k['status'])}, {k['version']}, 'sign', {q(dt(k['bound_at']))}, {q(dt(k['expire_at']))}, "
    f"{q(dt(k['bound_at']))})" for k in keys) + ";")
W("")

W("-- ---------------------------------------------------------------- 节点")
W("INSERT INTO node_info (id, name, status, model, did, location, capacity_kw, pv_output, storage_output, load_kw, soc, last_seen_at, created_at) VALUES")
W(",\n".join(
    f"  ({q(n['id'])}, {q(n['name'])}, {q(n['status'])}, {q(n['model'])}, {q(n['did'])}, "
    f"{q(n['location'])}, {n['capacity']}, {n['pv']}, {n['storage']}, {n['load']}, {n['soc']}, "
    f"NOW(), {q(dt(n['created_at']))})" for n in node_rows) + ";")
W("")
W("-- 设备身份绑定")
W("INSERT INTO did_device_binding (did, device_id, device_model, bound_at, last_online_at, status) VALUES")
W(",\n".join(
    f"  ({q(n['did'])}, {q(n['id'])}, {q(n['model'])}, {q(dt(n['created_at']))}, NOW(), 'bound')"
    for n in node_rows) + ";")
W("")

W("-- ---------------------------------------------------------------- 30 天历史指标（相对当前时间生成，保证曲线永远是新的）")
metric_vals = []
for n in node_rows:
    base_load = n["load"]
    soc = n["soc"]
    for h in range(30 * 24, 0, -1):
        hour = (24 - h % 24) % 24
        # 光伏出力：日出到日落的钟形曲线
        if 6 <= hour <= 18:
            pv = round(max(0.0, (n["capacity"] * 0.28) * (1 - ((hour - 12) / 6.5) ** 2) * random.uniform(0.85, 1.1)), 2)
        else:
            pv = 0.0
        # 负荷：早晚双峰
        peak = 1.25 if hour in (9, 10, 11, 19, 20, 21) else (0.72 if hour in (0, 1, 2, 3, 4, 5) else 1.0)
        load = round(base_load * peak * random.uniform(0.92, 1.08), 2)
        # 储能：光伏富余时充电（负值），负荷高峰时放电（正值）
        net = pv - load
        storage = round(max(-30.0, min(30.0, -net * 0.35)) * random.uniform(0.8, 1.15), 2)
        soc = round(min(95.0, max(20.0, soc - storage * 0.12)), 2)
        price = 1.05 if hour in (10, 11, 19, 20) else (0.32 if hour <= 6 else 0.62)
        metric_vals.append(
            f"  ({q(n['id'])}, DATE_ADD(DATE_FORMAT(NOW(), '%Y-%m-%d %H:00:00'), INTERVAL -{h} HOUR), "
            f"{pv}, {storage}, {load}, {soc}, {price})")
W("INSERT INTO node_metric (node_id, ts, pv_output, storage_output, load_kw, soc, price) VALUES")
# 分批 INSERT，避免单条语句过大
CHUNK = 500
for i in range(0, len(metric_vals), CHUNK):
    part = metric_vals[i:i + CHUNK]
    if i:
        W("INSERT INTO node_metric (node_id, ts, pv_output, storage_output, load_kw, soc, price) VALUES")
    W(",\n".join(part) + ";")
W("")

W("-- ---------------------------------------------------------------- 数据资产（80 条，覆盖 5 种类型 4 个等级）")
W("INSERT INTO energy_asset (id, name, data_type, source_did, owner_did, level, payload, payload_hash, description, record_count, auth_status, classify_score, classify_reason, chain_tx_id, evidence_id, created_at) VALUES")
W(",\n".join(
    f"  ({a['id']}, {q(a['name'])}, {q(a['data_type'])}, {q(a['source_did'])}, {q(a['owner_did'])}, "
    f"{q(a['level'])}, {q(a['payload'])}, {q(a['payload_hash'])}, {q(a['description'])}, "
    f"{a['record_count']}, {q(a['auth_status'])}, {a['classify_score']}, {q(a['classify_reason'])}, "
    f"{q(a['chain_tx_id'])}, {q(a['evidence_id'])}, {q(dt(a['created_at']))})"
    for a in asset_rows) + ";")
W("")

W("-- 资产溯源链（前 8 条资产有完整生命周期）")
W("INSERT INTO energy_asset_lineage (asset_id, stage, actor_did, evidence_id, hash, detail, created_at) VALUES")
W(",\n".join(
    f"  ({l['asset_id']}, {q(l['stage'])}, {q(l['actor_did'])}, {q(l['evidence_id'])}, "
    f"{q(l['hash'])}, {q(l['detail'])}, {q(dt(l['created_at']))})" for l in lineage_rows) + ";")
W("")

W("-- ---------------------------------------------------------------- 权限申请 / 授权 / 变更留痕")
W("INSERT INTO perm_application (id, applicant_did, applicant_name, resource_type, resource_id, action, reason, status, expire_at, approver_did, approver_name, approve_reason, approved_at, evidence_id, created_at) VALUES")
W(",\n".join(
    f"  ({a['id']}, {q(a['applicant_did'])}, {q(a['applicant_name'])}, {q(a['resource_type'])}, "
    f"{q(a['resource_id'])}, {q(a['action'])}, {q(a['reason'])}, {q(a['status'])}, "
    f"{q(dt(a['expire_at']))}, {q(a['approver_did'])}, {q(a['approver_name'])}, "
    f"{q(a['approve_reason'])}, {q(dt(a['approved_at']) if a['approved_at'] else None)}, "
    f"{q(a['evidence_id'])}, {q(dt(a['created_at']))})" for a in app_rows) + ";")
W("")
W("INSERT INTO perm_grant (id, application_id, grantee_did, grantee_name, resource_type, resource_id, action, status, granted_at, expire_at, revoked_at, revoke_reason, evidence_id) VALUES")
W(",\n".join(
    f"  ({g['id']}, {q(g['application_id'])}, {q(g['grantee_did'])}, {q(g['grantee_name'])}, "
    f"{q(g['resource_type'])}, {q(g['resource_id'])}, {q(g['action'])}, {q(g['status'])}, "
    f"{q(dt(g['granted_at']))}, {q(dt(g['expire_at']))}, "
    f"{q(dt(g['revoked_at']) if g.get('revoked_at') else None)}, {q(g.get('revoke_reason'))}, "
    f"{q(g['evidence_id'])})" for g in grant_rows) + ";")
W("")
W("INSERT INTO perm_change_log (target_did, change_type, resource_type, resource_id, action, operator_did, detail, evidence_id, created_at) VALUES")
W(",\n".join(
    f"  ({q(c['target_did'])}, {q(c['change_type'])}, {q(c['resource_type'])}, {q(c['resource_id'])}, "
    f"{q(c['action'])}, {q(c['operator_did'])}, {q(c['detail'])}, {q(c['evidence_id'])}, "
    f"{q(dt(c['created_at']))})" for c in change_rows) + ";")
W("")

W("-- ---------------------------------------------------------------- 联邦学习")
W("INSERT INTO algo_fl_task (task_id, name, node_ids, rounds, current_round, status, dp_enabled, dp_epsilon, dp_delta, dp_epsilon_spent, topk_enabled, topk_ratio, compression_ratio, model_version, creator_did, started_at, finished_at, created_at) VALUES")
W(f"  ('fl-000001', '负荷预测联合建模-第2轮', {q([n['id'] for n in node_rows])}, 10, 10, 'success', 1, 1.0, 0.00001, "
  f"{fl_rounds[-1]['epsilon_spent']}, 1, 0.100, {fl_rounds[-1]['compression_ratio']}, 'v11', "
  f"{q(user_rows[1]['did'])}, {q(dt(fl_ts))}, {q(dt(fl_ts + timedelta(minutes=33)))}, {q(dt(fl_ts))}),")
W(f"  ('fl-000002', '光伏出力联合建模-第1轮', {q([n['id'] for n in node_rows[:3]])}, 10, 0, 'created', 1, 0.8, 0.00001, "
  f"0, 1, 0.150, NULL, NULL, {q(user_rows[0]['did'])}, NULL, NULL, {q(dt(fl_ts + timedelta(days=1)))});")
W("")
W("INSERT INTO algo_fl_round (task_id, round, loss, acc, compression_ratio, epsilon_spent, gradient_hash, node_contributions, evidence_id, created_at) VALUES")
W(",\n".join(
    f"  ('fl-000001', {r['round']}, {r['loss']}, {r['acc']}, {r['compression_ratio']}, "
    f"{r['epsilon_spent']}, {q(r['gradient_hash'])}, {q(r['node_contributions'])}, "
    f"{q(r['evidence_id'])}, {q(dt(r['created_at']))})" for r in fl_rounds) + ";")
W("")
W("INSERT INTO algo_model_version (version, task_id, name, metrics, status, publisher_did, published_at, model_hash, created_at) VALUES")
W(f"  ('v11', 'fl-000001', '负荷预测模型 v11', "
  f"{q({'loss': fl_rounds[-1]['loss'], 'acc': fl_rounds[-1]['acc'], 'rounds': 10})}, 'published', "
  f"{q(user_rows[0]['did'])}, {q(dt(fl_ts + timedelta(minutes=40)))}, {q(sm3_tag('model-v11'))}, {q(dt(fl_ts))}),")
W(f"  ('v10', 'fl-000000', '负荷预测模型 v10', {q({'loss': 0.216, 'acc': 0.901, 'rounds': 8})}, "
  f"'deprecated', {q(user_rows[0]['did'])}, {q(dt(fl_ts - timedelta(days=7)))}, {q(sm3_tag('model-v10'))}, "
  f"{q(dt(fl_ts - timedelta(days=7)))});")
W("")

W("-- ---------------------------------------------------------------- 智能调度")
W("INSERT INTO algo_dispatch_task (task_id, name, time_window, node_ids, status, strategy, total_reward, q_table, explanation, explanation_source, issued, command_id, signer_did, issued_at, ack_status, creator_did, evidence_id, created_at) VALUES")
W(f"  ('dp-000001', '晚高峰削峰调度', '2026-08-16T15:00~16:00+08:00', {q([n['id'] for n in node_rows])}, "
  f"'success', {q(dispatch_strategy)}, 15.7, "
  f"{q([{'nodeId': 'Node-C', 'charge': 3.1, 'idle': 5.2, 'discharge': 8.42}, {'nodeId': 'Node-A', 'charge': 4.0, 'idle': 5.21, 'discharge': 4.8}])}, "
  f"{q('节点C当前负荷150kW为全网最高且SOC为42%仍有放电裕度，放电24kW可削去约16%峰值负荷；节点D光伏富余且SOC达82%接近上限，转为充电就地消纳；节点A供需基本平衡，保持待机以留出备用容量。')}, "
  f"'cache', 1, 'cmd-000001', {q(user_rows[1]['did'])}, {q(dt(dp_ts))}, 'all', {q(user_rows[1]['did'])}, "
  f"{q(ev_dp)}, {q(dt(dp_ts))}),")
W(f"  ('dp-000002', '午间光伏消纳调度', '2026-08-17T12:00~13:00+08:00', {q([n['id'] for n in node_rows[:2]])}, "
  f"'created', NULL, NULL, NULL, NULL, 'rule', 0, NULL, NULL, NULL, 'none', {q(user_rows[0]['did'])}, "
  f"NULL, {q(dt(dp_ts + timedelta(days=1)))});")
W("")

W("-- ---------------------------------------------------------------- AI 分析记录")
ai_cases = [
    ("dispatch", {"taskId": "dp-000001"}, "为什么选择节点C放电？",
     "节点C当前负荷150kW为全网最高，SOC 42% 仍有放电裕度，放电24kW的边际收益最大。",
     ["总负荷450kW高于总光伏145kW，存在缺口", "节点C负荷占比33%为最高", "节点C SOC 42% 高于下限20%"], "cache", 1240),
    ("risk", {"nodeId": "Node-A"}, "当前隐私风险主要来自哪里？",
     "主要风险来自查询频率过高与数据粒度过细的叠加效应，建议收紧差分隐私预算。",
     ["5分钟内12次查询，超出基线3倍", "分钟级粒度可反推用能行为", "暴露字段6个含地理位置"], "cache", 980),
    ("audit", {"period": "day"}, "今日审计有什么异常？",
     "今日出现1次越权访问告警与1次异常DID接入尝试，均已拦截并留痕。",
     ["R01越权访问命中3次", "R02异常DID接入1次", "全部高危操作已上链"], "rule", 12),
    ("data", {"assetId": 1001}, "这条数据的敏感等级为什么是L3？",
     "该资产包含地理位置字段且采集粒度为分钟级，可关联到具体用能主体，判定为敏感级。",
     ["含location字段", "采集粒度1min", "可关联主体身份"], "cache", 1105),
    ("qa", {}, "什么是可信数据空间？",
     "可信数据空间是在数据不出域前提下，通过可信身份、可信存证与细粒度授权实现数据要素安全流通的基础设施。",
     ["身份可信", "过程可溯", "权限可控"], "cache", 1520),
]
ai_vals = []
for i, (scene, ctx, question, answer, reasoning, source, lat) in enumerate(ai_cases):
    ts = BASE + timedelta(days=15, hours=16, minutes=i * 11)
    ai_vals.append(
        f"  ({q(scene)}, {q(ctx)}, {q(question)}, {q(answer)}, {q(reasoning)}, {q(source)}, {lat}, "
        f"{q(user_rows[0]['did'])}, NULL, {q(dt(ts))})")
W("INSERT INTO algo_ai_analysis (scene, context, question, answer, reasoning, source, latency_ms, actor_did, evidence_id, created_at) VALUES")
W(",\n".join(ai_vals) + ";")
W("")

W("-- ---------------------------------------------------------------- 隐私风险评估历史")
risk_vals = []
for i in range(8):
    n = node_rows[i % 4]
    ts = BASE + timedelta(days=13 + i // 4, hours=10 + i * 2)
    score = round(random.uniform(28, 82), 2)
    level = "low" if score < 40 else ("medium" if score < 60 else "high")
    factors = [{"name": "查询频率", "weight": 0.35, "score": round(random.uniform(30, 90), 1)},
               {"name": "数据粒度", "weight": 0.30, "score": round(random.uniform(40, 85), 1)},
               {"name": "暴露字段数", "weight": 0.20, "score": round(random.uniform(20, 70), 1)},
               {"name": "隐私预算余量", "weight": 0.15, "score": round(random.uniform(25, 80), 1)}]
    risk_vals.append(
        f"  ({q(n['id'])}, {score}, {q(level)}, "
        f"{q({'queryFreq': random.randint(3, 18), 'dataGranularity': 'minute', 'exposedFields': random.randint(3, 8)})}, "
        f"{q(factors)}, {q('建议将差分隐私 ε 由 1.0 降至 0.5' if level == 'high' else '当前隐私风险可控，保持现有策略')}, "
        f"{q(user_rows[0]['did'])}, NULL, {q(dt(ts))})")
W("INSERT INTO algo_risk_assessment (node_id, risk_score, level, features, factors, suggestion, actor_did, evidence_id, created_at) VALUES")
W(",\n".join(risk_vals) + ";")
W("")

W("-- ---------------------------------------------------------------- 风险告警")
alert_vals = []
for i, (aid, code, name, level, msg, actor, status) in enumerate(alert_rows):
    ts = BASE + timedelta(days=16, hours=9 + i * 3)
    alert_vals.append(
        f"  ({q(aid)}, {q(code)}, {q(name)}, {q(level)}, {q(msg)}, "
        f"{q(actor['did'] if actor else None)}, {q(actor['real_name'] if actor else None)}, "
        f"{3 if code == 'R01_UNAUTHORIZED' else 1}, {q(status)}, "
        f"{q(user_rows[0]['did'] if status == 'acked' else None)}, "
        f"{q(dt(ts + timedelta(hours=1)) if status == 'acked' else None)}, {q(dt(ts))})")
W("INSERT INTO audit_alert (alert_id, rule_code, rule_name, risk_level, message, actor_did, actor_name, hit_count, status, acked_by, acked_at, created_at) VALUES")
W(",\n".join(alert_vals) + ";")
W("")

# ============================================================ 存证链
W("-- ---------------------------------------------------------------- 存证哈希链")
W("-- block_hash = SM3(prev_hash + payload_hash + timestamp)，可直接通过完整性校验")
W(f"-- 共 {len(chain.rows)} 个区块，创世块 height=0")
ev_vals = []
for r in chain.rows:
    ev_vals.append(
        f"  ({q(r['evidence_id'])}, {q(r['category'])}, {q(r['ref_id'])}, {q(r['actor_did'])}, "
        f"{q(r['payload_hash'])}, {q(r['prev_hash'])}, {q(r['block_hash'])}, {r['block_height']}, "
        f"{q(r['tx_id'])}, {q(r['trace_id'])}, {q(r['payload_snapshot'])}, {q(dt(r['created_at']))})")
for i in range(0, len(ev_vals), CHUNK):
    W("INSERT INTO chain_evidence (evidence_id, category, ref_id, actor_did, payload_hash, prev_hash, block_hash, block_height, tx_id, trace_id, payload_snapshot, created_at) VALUES")
    W(",\n".join(ev_vals[i:i + CHUNK]) + ";")
W("")

# ============================================================ 审计日志
ACTIONS = [
    ("auth", "login", "success", "low", "用户登录成功"),
    ("did", "did:register", "success", "medium", "签发设备身份"),
    ("did", "did:verify", "success", "low", "身份验签通过"),
    ("asset", "asset:register", "success", "low", "登记能源数据资产"),
    ("asset", "asset:read", "success", "low", "读取数据资产"),
    ("permission", "permission:apply", "success", "low", "提交权限申请"),
    ("permission", "permission:approve", "success", "medium", "审批通过权限申请"),
    ("evidence", "evidence:write", "success", "low", "数据摘要上链"),
    ("algo", "fl:train", "success", "medium", "启动联邦学习训练"),
    ("algo", "dispatch:run", "success", "medium", "运行DQN生成调度策略"),
    ("audit", "audit:query", "success", "low", "查询审计日志"),
    ("key", "key:rotate", "success", "medium", "密钥轮换"),
]
DENIED = [
    ("permission", "dispatch:issue", "denied", "high", "越权尝试下发调度指令，已被权限中心拦截"),
    ("permission", "asset:export", "denied", "high", "越权尝试导出核心级数据，已被拦截"),
    ("did", "did:verify", "denied", "critical", "已冻结身份尝试接入，已被拒绝"),
    ("algo", "fl:train", "denied", "high", "无 algo:execute 权限，拒绝启动训练"),
]
log_vals = []
for i in range(160):
    if i % 23 == 7:
        mod, act, result, risk, detail = DENIED[(i // 23) % len(DENIED)]
        actor = user_rows[2]
    else:
        mod, act, result, risk, detail = ACTIONS[i % len(ACTIONS)]
        actor = user_rows[i % len(user_rows)]
    minutes_ago = 5 + i * 9
    ev_ref = chain.rows[(i * 7) % len(chain.rows)]["evidence_id"] if risk in ("high", "critical") else None
    log_vals.append(
        f"  ({q(f'tr-20260818-{sm3_hex(str(i))[:8]}')}, {q(actor['did'])}, {q(actor['real_name'])}, "
        f"{q(mod)}, {q(act)}, {q(mod if mod in ('asset', 'evidence') else None)}, "
        f"{q(str(1001 + i % 80) if mod == 'asset' else None)}, {q(result)}, {q(risk)}, {q(detail)}, "
        f"{q(f'192.168.1.{40 + i % 60}')}, {q(ev_ref)}, {q(sm3_tag(f'log-{i}'))}, "
        f"DATE_SUB(NOW(), INTERVAL {minutes_ago} MINUTE))")

W("-- ---------------------------------------------------------------- 审计日志（按月分表）")
W("-- 先写入基准月表，再按当前月份动态建表并复制，保证无论何时安装，")
W("-- 「今日日志」「近7天趋势」等看板统计都有数据，不会开局空白。")
W("INSERT INTO audit_log_202608 (trace_id, actor_did, actor_name, module, action, resource_type, resource_id, result, risk_level, detail, ip, evidence_id, hash, created_at) VALUES")
W(",\n".join(log_vals) + ";")
W("")
W("SET @cur_tbl = CONCAT('audit_log_', DATE_FORMAT(NOW(), '%Y%m'));")
# 当前月份正好是 2026-08 时，@cur_tbl 就等于基准表本身，
# 而 MySQL 的 CREATE TABLE t LIKE t 会直接报 1066 Not unique table/alias，
# IF NOT EXISTS 也救不了（它在名字解析之后才生效）。所以这里要和下面的 INSERT 一样加护栏。
W("SET @s = IF(@cur_tbl = 'audit_log_202608', 'SELECT 1',")
W("  CONCAT('CREATE TABLE IF NOT EXISTS ', @cur_tbl, ' LIKE audit_log_202608'));")
W("PREPARE stmt FROM @s; EXECUTE stmt; DEALLOCATE PREPARE stmt;")
W("SET @s = IF(@cur_tbl = 'audit_log_202608', 'SELECT 1',")
W("  CONCAT('INSERT INTO ', @cur_tbl, ' (trace_id, actor_did, actor_name, module, action, resource_type,',")
W("         ' resource_id, result, risk_level, detail, ip, evidence_id, hash, created_at)',")
W("         ' SELECT trace_id, actor_did, actor_name, module, action, resource_type, resource_id,',")
W("         ' result, risk_level, detail, ip, evidence_id, hash, created_at FROM audit_log_202608'));")
W("PREPARE stmt FROM @s; EXECUTE stmt; DEALLOCATE PREPARE stmt;")
W("")
W("-- 若当前月份不是 2026-08，基准月表里的数据只作为「历史月份」保留，用于演示跨月 UNION ALL 查询。")

# ============================================================ 自检：种子存证链必须能通过完整性校验
prev = "sm3:" + "0" * 64
for r in chain.rows:
    assert r["prev_hash"] == prev, f"链断裂于 {r['evidence_id']}"
    assert r["payload_hash"] == payload_hash(r["payload_snapshot"]), f"摘要不一致于 {r['evidence_id']}"
    assert r["block_hash"] == block_hash(r["prev_hash"], r["payload_hash"], iso(r["created_at"])), \
        f"区块哈希不一致于 {r['evidence_id']}"
    prev = r["block_hash"]
print(f"存证链自检通过：{len(chain.rows)} 个区块全部连续且摘要一致")

sql = "\n".join(out) + "\n"
path = "/home/stu/Huang_Hao/Zhi_softwire/backend/sql/02_seed.sql"
with open(path, "w", encoding="utf-8") as f:
    f.write(sql)

print(f"已生成 {path}")
print(f"  身份 {len(identities)} 个 / 密钥 {len(keys)} 把 / 资产 {len(asset_rows)} 条")
print(f"  指标 {len(metric_vals)} 行 / 存证区块 {len(chain.rows)} 个 / 审计日志 {len(log_vals)} 条")
print(f"  权限申请 {len(app_rows)} 条 / 授权 {len(grant_rows)} 条 / 变更留痕 {len(change_rows)} 条")
print(f"  文件大小 {len(sql) / 1024:.1f} KB")
