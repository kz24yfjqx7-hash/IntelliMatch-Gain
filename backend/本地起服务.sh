#!/usr/bin/env bash
# 本机跑后端用的启动脚本。只做三件事：体检 -> 建库导数据 -> 起服务。
# 真机部署走 docker-compose，用不到这个脚本。
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
MYSQL_PORT=${MYSQL_PORT:-3306}
REDIS_PORT=${REDIS_PORT:-6379}

say() { printf '\n\033[1;36m== %s\033[0m\n' "$1"; }
die() { printf '\033[1;31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

say "1/4 体检"
[ -x "$PY" ] || die "找不到 $PY，先建虚拟环境：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
$PY - <<EOF || die "MySQL 连不上 127.0.0.1:$MYSQL_PORT。先把 MySQL 起起来，见 README「本地跑一遍」"
import socket,sys
try: socket.create_connection(("127.0.0.1",$MYSQL_PORT),3).close()
except OSError as e: sys.exit(1)
EOF
echo "  ✓ MySQL 127.0.0.1:$MYSQL_PORT 通"
$PY - <<EOF && echo "  ✓ Redis 127.0.0.1:$REDIS_PORT 通" || echo "  ! Redis 不通——不致命，缓存和风控计数会自动退化，登录照样能用"
import socket,sys
try: socket.create_connection(("127.0.0.1",$REDIS_PORT),3).close()
except OSError: sys.exit(1)
EOF

say "2/4 建库 + 导种子数据"
# 退出码：0=库是空的要导  1=库里已有数据  2=连不上（此时绝不能当成「已有数据」跳过）
set +e
$PY - <<'EOF'
import sys; sys.path.insert(0,'.')
import pymysql
from core.config import settings
try:
    c = pymysql.connect(host=settings.MYSQL_HOST, port=settings.MYSQL_PORT,
                        user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD)
except Exception as exc:
    print(f"  连不上 MySQL：{exc}", file=sys.stderr)
    sys.exit(2)
cur = c.cursor()
# 看的是「有没有数据」，不是「有没有表」。
# 导入中途失败时 DDL 已经隐式提交、INSERT 却回滚了，
# 只查表存不存在会把这种半吊子状态误判成「已导好」，然后静默跳过。
cur.execute("SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='energy_tds' AND table_name='sys_user'")
if cur.fetchone()[0] == 0:
    sys.exit(0)
cur.execute("SELECT COUNT(*) FROM energy_tds.sys_user")
sys.exit(0 if cur.fetchone()[0] == 0 else 1)
EOF
rc=$?
set -e
case $rc in
  0) echo "  库是空的，导入 01_schema.sql 与 02_seed.sql（600KB，约十几秒）"
     $PY sql/导入.py ;;
  1) echo "  ✓ 库里已有数据，跳过导入（要重来就先 DROP DATABASE energy_tds）" ;;
  *) die "MySQL 端口通了但登不进去。最常见的原因见 README「MySQL 8 的认证插件」一节" ;;
esac

say "3/4 自检存证哈希链"
$PY - <<'EOF'
import sys; sys.path.insert(0,".")
from core.database import SessionLocal
from modules.evidence.chain import get_chain
with SessionLocal() as db:
    r = get_chain().status(db)
    符 = "✓" if r["intact"] else "✗"
    print(f"  {符} 链完整 intact={r['intact']}  高度={r['height']}  存证={r['totalRecords']} 条")
    if not r["intact"]:
        print(f"    断在 {r['brokenAt']}——如果你刚点过 /evidence/demo/tamper，这是预期的")
EOF

say "4/4 起服务  http://127.0.0.1:8000/docs"
echo "  演示账号：admin/admin123  grid/grid123  vpp/vpp123  subject/subject123  regulator/reg123  edge/edge123"
echo "  Ctrl-C 停"
exec $PY -m uvicorn main:app --host 0.0.0.0 --port "${BACKEND_PORT:-8000}" --reload
