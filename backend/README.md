# backend · 后端安全内核（甲）

能源可信数据空间平台的安全内核：可信身份、数据可信存证、细粒度权限控制、安全审计，
以及对算法服务的统一代理。**本目录由甲独占**，乙不创建也不修改这里的任何文件。

## 目录

```
core/        横切能力：配置、DB、Redis、统一响应、JWT、国密、中间件链、依赖注入
modules/     业务模块，每个模块 router/service/schema/model 四件套
ws/          WebSocket 连接管理与广播
sql/         建表与种子数据（由 MySQL 容器的 initdb 自动执行）
tests/       pytest + 假算法服务
```

## 三条不能破的底线

1. **接口严格照 `contract/API-CONTRACT.md`**，路径、字段名、枚举值一个字母都不改。
2. **所有 `/api/v1/**` 响应都是 `{code, message, data, traceId}`**，错误也不例外。
3. **完全离线可运行**，不引入任何运行时需要联网的依赖。

## 国密实现说明

`core/gm_crypto.py` 用纯 Python 实现了国密三件套，不依赖 `gmssl` 或 `cryptography`：

| 算法 | 标准 | 用途 |
|---|---|---|
| SM3 | GB/T 32905-2016 | 数据摘要、区块哈希、DID 推导 |
| SM2 | GB/T 32918.2-2016（sm2p256v1） | 身份签名与验签 |
| SM4 | GB/T 32907-2016（CBC + PKCS7） | 托管私钥加密存储 |

这么做的原因是交付物要打成 x86_64 + arm64 双架构离线安装包：任何带 C 扩展的密码库
都会在 QEMU 模拟的 ARM 构建里触发源码编译，风险太高。纯 Python 实现性能足够
（单次签名约毫秒级），三个算法都用国标测试向量验证过。

**私钥永不明文落库。** `custody=true` 时私钥以 SM4-CBC 密文存在 `did_key.private_key_enc`，
加密密钥由 `KEY_CUSTODY_SECRET` 派生；`custody=false` 时平台完全不留存，私钥只在签发那一次返回。
托管这条路是为答辩现场留的——否则演示「签名下发调度指令」时手上没有任何一把可用私钥。

## 数据库

- 建表：`sql/01_schema.sql`，种子：`sql/02_seed.sql`
- 两个文件由乙的 docker-compose 挂到 MySQL 镜像的 `/docker-entrypoint-initdb.d`，
  容器**首次**启动时自动执行（数据卷已存在时不会重跑）
- 种子数据由 `sql/gen_seed.py` 生成，**确定性输出**，改数据请改脚本后重跑：

  ```bash
  python3 sql/gen_seed.py
  ```

- 存证哈希链是真实计算出来的，装完就能通过 `/evidence/chain/status` 的完整性校验

### 演示账号

| 用户名 | 密码 | 角色 |
|---|---|---|
| admin | admin123 | sys_admin |
| grid | grid123 | grid_dispatcher |
| vpp | vpp123 | vpp_operator |
| subject | subject123 | energy_subject |
| regulator | reg123 | regulator |
| edge | edge123 | edge_node |

## 本地开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 起依赖（乙的 compose 没合并进来之前，先手工起两个容器）
docker run -d --name tds-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=root123 -e MYSQL_DATABASE=energy_tds \
  -e MYSQL_USER=energy -e MYSQL_PASSWORD=energy123 \
  -v "$PWD/sql:/docker-entrypoint-initdb.d:ro" mysql:8.0
docker run -d --name tds-redis -p 6379:6379 redis:7-alpine

# 乙的算法服务没到位时，用假算法服务顶上（契约第三部分的可执行版本）
python -m uvicorn tests.fake_algo:app --port 8100 &

# 起后端
MYSQL_HOST=127.0.0.1 REDIS_HOST=127.0.0.1 ALGO_SERVICE_URL=http://127.0.0.1:8100 \
  python -m uvicorn main:app --reload --port 8000
```

`tests/fake_algo.py` 里的数字是编出来的，只保证结构正确。真正的 FedAvg、
差分隐私、Top-k、DQN 由乙在 algo-service 里实现，契约第三部分对此有明确要求。

### MySQL 8 的认证插件

MySQL 8 默认用 `caching_sha2_password`。pymysql 走 TCP 首次握手要做 RSA 密钥交换，
而这需要 `cryptography` 包——本项目**刻意没有装它**（见 requirements.txt 的开头，
arm64 构建禁止引入需要源码编译的依赖）。结果就是连不上，报：

```
RuntimeError: 'cryptography' package is required for sha256_password or caching_sha2_password auth methods
```

两种解法，**部署时用第一种**：

```yaml
# docker-compose.yml（乙负责）——让服务端默认就发 native_password
mysql:
  command: --default-authentication-plugin=mysql_native_password --performance-schema=OFF
```

```sql
-- 本地手工装的 MySQL，改单个用户即可
ALTER USER 'energy'@'localhost' IDENTIFIED WITH mysql_native_password BY 'energy123';
FLUSH PRIVILEGES;
```

不要靠装 `cryptography` 绕过去：它在 arm64 上会拖进 Rust 工具链，
树莓派镜像构建会直接卡死。

接口文档：http://localhost:8000/docs　健康检查：http://localhost:8000/health

## 测试

```bash
pytest                            # 全部 273 条，SQLite 内存库，不需要先起容器
pytest tests/test_security.py -v  # 安全专项，按攻击面组织
```

`tests/test_security.py` 和别的测试文件不一样：它不按模块分，按**攻击面**分。

| 段 | 攻击面 | 挡的是什么 |
|---|---|---|
| A | 认证绕过 | 不带凭证、篡改 payload 提权、alg=none、换密钥重签、过期、登出后重用、账号已停用 |
| B | 垂直越权 | 低权限角色调高权限接口，11 组参数化 |
| C | 水平越权 | 同角色之间改 URL 里的 id 互相翻数据 |
| D | 身份伪造 | 伪造签名、冒用他人 DID、跨任务重放、策略被改后复用签名、nonce 重放、冻结身份 |
| E | 注入与恶意输入 | SQL 注入、分表名白名单、路径穿越、超长输入、枚举与分页越界 |
| F | 信息泄露 | 口令哈希与私钥、账号枚举、堆栈外泄、错误信封 |
| G | 审计绕过 | 审计与存证有没有写接口、绕过应用层直接改库会不会被抓 |

另外补了两处加固，都不改契约：

- **停用账号即时生效**：Token 有效期 8 小时，账号状态跟着姓名一起缓存在
  `user:profile:<id>`（5 分钟），`PUT /users/{id}` 会主动删这个键，
  所以管理员停用一个账号后对方立刻掉线，而不是还能撑大半天。
- **撞库锁定**：同一账号 5 分钟内密码错 5 次返回 `1007`（契约 1.1 里本来就有这个码）。
  按账号限不按 IP——演示现场所有人共用一个出口 IP，按 IP 限会把整台机器锁死。
  Redis 不可用时自动退化为不限流，加固手段不能变成新的单点。

A 段第一条是**全量扫描**：遍历 `app.routes` 逐个打，只要有一个 `/api/v1` 接口
忘了挂鉴权依赖就立刻变红。以后加接口不用靠记性。

真机上还需要跑一遍只有 MySQL / 真 Redis 才能验的部分，见 `验证清单.md` 第 5bis 节
（停用账号即时失效、冻结身份缓存删除，这两条 SQLite + 假 Redis 测不准）。

## 树莓派约束

ARM 版本跑在树莓派 4B 上，本容器内存上限 300MB。因此：单进程 uvicorn（不用 gunicorn
多 worker）、后台任务用 asyncio 协程不开线程池、大表查询一律分页（`size` 上限 200）。
