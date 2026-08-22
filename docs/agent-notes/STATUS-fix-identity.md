# STATUS · fix-identity（2026-08-22）

负责缺陷：**B-020（中）**、**B-013（P2）**、**B-023（低）**。三条全部修完并已在真机验证。

> **不需要执行任何数据库迁移**：本轮改动没有动表结构，RSA 的密钥/签名长度是按现有列宽
> （`did_key.public_key` 512、`did_key.private_key_enc` 1024、`algo_dispatch_task.signature` 256）反推选定的。

---

## 1. B-020 多算法密钥并存 → 验签全废 / ECC·RSA 只能生成不能验

### 根因

- `modules/did/service.py` 的两条 verify 路径都只取 `status='active'` 且 `version` 最大的**一把**密钥；
- `core/gm_crypto.generate_keypair_by_algorithm` 对 ECC/RSA **返回随机串占位**，根本不是密钥对，也没有对应的 sign/verify。

于是「给某个 DID 绑一把 ECC 密钥」＝「该 DID 之前所有 SM2 签名立即失效，且新密钥又验不了」。

### 改法（两条建议都做了）

**① 补齐算法实现** — `backend/core/gm_crypto.py`

- 椭圆曲线点运算参数化：`_Curve` / `_ec_add` / `_ec_mul` / `_ec_on_curve`；原来的 `_point_add` / `_point_mul` / `_on_curve` 变成 SM2 曲线上的薄封装，SM2 的国标向量自检不受影响。
- **ECC = secp256r1 上的 ECDSA（SHA-256）**：`ecdsa_generate_keypair` / `ecdsa_sign` / `ecdsa_verify`，复用上面同一套点运算。
- **RSA = PKCS#1 v1.5 + SHA-256**：`rsa_generate_keypair` / `rsa_sign` / `rsa_verify`，签名验签就是纯 Python `pow()`，素数用 Miller-Rabin（先小素数试除）生成。
- 统一入口：`generate_keypair_by_algorithm` / `sign_by_algorithm` / `verify_by_algorithm`（`SIGNATURE_ALGORITHMS = ("SM2","ECC","RSA")`）。verify 一侧算法未知或格式非法一律返回 `False`，绝不抛异常。
- **零新增依赖**：只用标准库 `hashlib` / `secrets`，没有任何需要编译的东西，arm64 离线包不受影响。
- 参数选择（为什么 RSA 是 1024 位）：公钥只存模数 n（指数固定 65537）＝256 字符 ≤ `public_key(512)`；私钥存 `p‖q`＝256 字符，SM4 托管密文 576 字符 ≤ `private_key_enc(1024)`；签名 256 字符 ≤ `DidVerifyRequest.signature(256)` 和 `algo_dispatch_task.signature(256)`。**所以不用改表**。要提到 2048 位只需改 `_RSA_BITS` 并同步放宽这三处列宽（代码里已写明）。实测生成 0.15s / 签名 4ms / 验签 <1ms。

**② verify 遍历全部活跃密钥** — `backend/modules/did/service.py`

- 新增 `_active_keys(db, did)`（版本从新到旧）与 `_match_key(keys, msg, sig)`；
- `verify_detail`（`POST /did/verify`）与 `verify_signature`（中间件/节点上线等复用）都改成逐把尝试，任意一把匹配即通过；
- 响应回传**实际命中**的 `keyId` / `keyVersion` / `algorithm`，失败时回传最新一把并说明「与 N 把活跃密钥均不匹配」。**契约 2.2 的 `valid` / `subjectType` / `status` / `reason` 字段名和语义保持不变**，`keyId` / `keyVersion` / `algorithm` 是附加字段（`keyVersion` 本来就有）。
- `sign_with_custody` 按密钥算法选签名算法，优先挑 SM2 托管密钥（国密是主算法、签名长度稳定 128 字符），没有 SM2 才退到版本最新的那把；对存量库里旧版「随机串占位」的 ECC/RSA 密钥做异常降级（返回 `None`，不 500）。

### 真机验证（自建实例，见下方「端口说明」）

```
=== 1. POST /did/register  → did:vpp:device:0x63226d72a1ea4d9a672ccdaea04774f2
=== 2. POST /keys 绑定
SM2 -> id=21 version=2 pubLen=130 privLen=64
ECC -> id=22 version=3 pubLen=130 privLen=64
RSA -> id=23 version=4 pubLen=256 privLen=256
=== 3. 用每把私钥签名后 POST /did/verify（message="设备上线请求 nonce=fixid-1787413527"）
v1 注册时的 SM2：{"code":0,...,"data":{"valid":true,"subjectType":"device","status":"active","keyId":20,"keyVersion":1,"algorithm":"SM2","reason":null}}
v2 SM2        ：{"code":0,...,"data":{"valid":true,"subjectType":"device","status":"active","keyId":21,"keyVersion":2,"algorithm":"SM2","reason":null}}
v3 ECC        ：{"code":0,...,"data":{"valid":true,"subjectType":"device","status":"active","keyId":22,"keyVersion":3,"algorithm":"ECC","reason":null}}
v4 RSA        ：{"code":0,...,"data":{"valid":true,"subjectType":"device","status":"active","keyId":23,"keyVersion":4,"algorithm":"RSA","reason":null}}
=== 4. 负例（原文改一个字）
{"code":0,...,"data":{"valid":false,...,"reason":"签名与该身份 4 把活跃密钥均不匹配，原文可能被篡改或签名伪造"}}
```

对应 QA 用例 TC-33-06（`qa/func-tests/api_func_test.py::t_key_ecc_breaks_verify`）的期望「valid true」现已满足。

---

## 2. B-013 `POST /did/{did}/rotate-key` 强制要求 body

`backend/modules/did/router.py:84-86`：`body: DidRotateKeyRequest = DidRotateKeyRequest()`。契约 2.2 没为该接口定义请求体，现在不带 body 也能轮换；带 `{"reason":...}` 的老用法完全兼容（乙方前端固定发 `{}`，无需回归）。

真机（不带 `-d`）：

```
$ curl -s -X POST "$API/did/$DID/rotate-key" -H "$AUTH"
{"code": 0, "message": "ok", "version": 5, "algorithm": "RSA", "publicKey": "b02519ac41e3c7e2817e1384..."}
```

---

## 3. B-023 `createdAt` 与 `updatedAt` 差 8 小时

- `backend/core/response.py` 新增 `now_naive()`：东八区、秒级、去 tzinfo，专供 DATETIME 列使用。
- **所有 `modules/*/model.py` 的 34 个时间列**（did / key / asset / permission / evidence / audit / algo / node / auth 全看过一遍）统一改成 `default=now_naive`，`updated_at` 另加 `onupdate=now_naive`；`server_default=func.now()` 保留，只用于建表 DDL。
- `backend/core/database.py` 加 `connect` 事件：每条 MySQL 连接 `SET time_zone='+08:00'`，让 DDL 的 `CURRENT_TIMESTAMP` / `ON UPDATE CURRENT_TIMESTAMP` 与原生 SQL 的 `NOW()` 也走东八区（SQLite 自动跳过，单测同样成立）。两条互为保险。

真机 MariaDB（时区仍是 UTC）：

```
新建 DID：createdAt=2026-08-22T23:45:45+08:00 updatedAt=2026-08-22T23:45:45+08:00   （差 0s）
绑定密钥：boundAt  =2026-08-22T23:45:45+08:00
存证记录：createdAt=2026-08-22T23:45:45+08:00
更新后（轮换过密钥的 DID）：createdAt=23:45:27 / updatedAt=23:45:30，updatedAt 正确地晚于 createdAt 且同为 +08:00
```

> 存量老数据的 `updated_at` 仍是按 UTC 写进去的旧值，本轮不做回填（历史行的语义无从恢复）；重新导库（`01_schema.sql` + `02_seed.sql`）后不会再出现。

---

## 单测

新增 `backend/tests/test_did_fix_b020_b013_b023.py`（13 条，用例名带缺陷编号）：

- B-020：三算法自检（签/验/篡改/跨算法混淆）、四把密钥并存全部验得过、后绑 ECC 不破坏原 SM2、ECC/RSA 走完整接口链路、伪造签名与改动原文验不过、注销密钥立刻失效、托管代签支持 ECC、存量占位密钥只降级不抛异常；
- B-013：不带 body 轮换成功 + 带 body 兼容；
- B-023：新建记录两字段同刻、更新后仍是东八区、**防回归**（元数据扫描：不允许再出现只有 `server_default` 的时间列、`updated_at` 必须有 `onupdate`）、`now_naive()` 自检。

`cd backend && .venv/bin/python -m pytest -q`：我负责的文件相关用例全绿（`test_did.py` / `test_gm_crypto.py` / `test_did_fix_*.py` 共 42 条 passed）。
全量跑的时候会看到 `test_fix_algo.py` / `test_evidence_fixes.py` 里若干条时红时绿——那是同一工作区里 fix-algo / fix-evidence 两位正在改自己的文件，单独跑它们的文件是过的；本轮唯一稳定失败的仍是约定豁免的 `test_audit.py::test_审计报告在算法服务不可用时降级`。

## 端口说明

FIX-ROUND-ENV 分配给我的 **8011 被别的进程占着**（`ss` 显示在 LISTEN，但 `/api/v1/auth/login` 返回 404，不是本项目的后端），所以我起在 **8021**，验证完已 `pkill` 掉并确认端口释放。全程没碰 8000 / 8100 / 5199。

## 遗留 / 提醒

1. `rotate-key` 沿用「版本最大的活跃密钥」的算法：如果一个 DID 最后绑的是 RSA，那么轮换出来的新密钥也是 RSA（上面真机输出里的 `version:5, algorithm:"RSA"` 就是这个原因）。这是原有行为，未改动；若前端想指定算法，需要契约层面加参数。
2. RSA 固定 1024 位、指数固定 65537，是为了不改表（理由见上）。答辩若被问到强度，口径是「演示环境按现有存储约束选参，主算法是国密 SM2，RSA/ECC 用于演示多算法密钥并存」。
3. 存量库里在本轮之前绑定的 ECC/RSA 密钥是随机串占位，验签必然 false、托管代签会降级成 None。演示前建议重新导库，或对这些 DID 执行一次 `rotate-key`。
