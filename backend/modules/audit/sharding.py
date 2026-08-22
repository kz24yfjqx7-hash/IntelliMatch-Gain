"""审计日志按月分表。

表名规则：audit_log_ + YYYYMM。写入前确保当月表存在，跨月查询用 UNION ALL 合并。

为什么要分表：审计日志是全平台写入量最大的表，单表跑几个月就会拖慢查询，
而按月切分之后，绝大多数查询（今日/本周）只碰一张小表，历史月份表可以整表归档。
"""
import logging
import re
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.exceptions import ParamError

logger = logging.getLogger(__name__)

TABLE_PREFIX = "audit_log_"
# 表名只允许 audit_log_ + 6 位数字。所有拼接表名的地方都要过这个校验，
# 因为表名没法用参数绑定，只能靠白名单挡住注入。
_TABLE_PATTERN = re.compile(r"^audit_log_\d{6}$")

COLUMNS = [
    "trace_id", "actor_did", "actor_name", "module", "action", "resource_type",
    "resource_id", "result", "risk_level", "detail", "ip", "evidence_id", "hash",
    "created_at",
]

_MYSQL_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  trace_id      VARCHAR(64) NOT NULL,
  actor_did     VARCHAR(128),
  actor_name    VARCHAR(128),
  module        VARCHAR(32) NOT NULL,
  action        VARCHAR(64) NOT NULL,
  resource_type VARCHAR(32),
  resource_id   VARCHAR(64),
  result        ENUM('success','failed','denied') NOT NULL,
  risk_level    ENUM('low','medium','high','critical') NOT NULL DEFAULT 'low',
  detail        TEXT,
  ip            VARCHAR(64),
  evidence_id   VARCHAR(64),
  hash          VARCHAR(80),
  created_at    DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_trace (trace_id),
  INDEX idx_risk_time (risk_level, created_at),
  INDEX idx_actor_time (actor_did, created_at),
  INDEX idx_action (action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id      VARCHAR(64) NOT NULL,
  actor_did     VARCHAR(128),
  actor_name    VARCHAR(128),
  module        VARCHAR(32) NOT NULL,
  action        VARCHAR(64) NOT NULL,
  resource_type VARCHAR(32),
  resource_id   VARCHAR(64),
  result        VARCHAR(16) NOT NULL,
  risk_level    VARCHAR(16) NOT NULL DEFAULT 'low',
  detail        TEXT,
  ip            VARCHAR(64),
  evidence_id   VARCHAR(64),
  hash          VARCHAR(80),
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

# 进程内缓存已确认存在的表，避免每写一条日志都跑一次 CREATE TABLE IF NOT EXISTS
_known_tables: set[str] = set()


def table_of(moment: datetime) -> str:
    return f"{TABLE_PREFIX}{moment:%Y%m}"


def assert_valid(table: str) -> str:
    """分表名要拼进 SQL，所以只认 audit_log_ + 6 位数字这一种形状。

    抛 ParamError 而不是 ValueError：万一哪天有人把它接到查询参数上，
    也是规规矩矩的 1001，而不是一个 500 加半页堆栈。
    """
    if not _TABLE_PATTERN.match(table):
        raise ParamError(f"非法的审计表名：{table}")
    return table


def ensure_table(db: Session, table: str) -> str:
    """确保分表存在。跨月的第一条日志会触发建表。"""
    assert_valid(table)
    if table in _known_tables:
        return table

    ddl = _MYSQL_DDL if db.bind.dialect.name == "mysql" else _SQLITE_DDL
    db.execute(text(ddl.format(table=table)))
    db.commit()
    _known_tables.add(table)
    logger.info("审计分表已就绪：%s", table)
    return table


def existing_tables(db: Session) -> list[str]:
    """列出库里已存在的全部审计分表，按时间倒序。"""
    dialect = db.bind.dialect.name
    if dialect == "mysql":
        rows = db.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name LIKE :pattern"
        ), {"pattern": TABLE_PREFIX + "%"}).scalars().all()
    else:
        rows = db.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE :pattern"
        ), {"pattern": TABLE_PREFIX + "%"}).scalars().all()
    return sorted((t for t in rows if _TABLE_PATTERN.match(t)), reverse=True)


def tables_in_range(db: Session, start: datetime | None, end: datetime | None) -> list[str]:
    """挑出时间范围覆盖到的分表。不给范围就返回全部，但最多回溯 12 个月。

    上限 12 个月是给树莓派留的保险：真跑满几年之后，
    一次 UNION ALL 全表会把内存吃光。
    """
    tables = existing_tables(db)
    if start or end:
        lo = f"{start:%Y%m}" if start else "000000"
        hi = f"{end:%Y%m}" if end else "999999"
        tables = [t for t in tables if lo <= t[len(TABLE_PREFIX):] <= hi]
    return tables[:12]


def union_query(tables: list[str], where: str = "", order: str = "",
                limit: str = "") -> str:
    """把多张分表拼成一个 UNION ALL 子查询。

    tables 已经过 assert_valid 白名单校验，where 里的值一律用参数绑定，
    所以这里的字符串拼接不构成注入面。
    """
    if not tables:
        return ""
    cols = ", ".join(COLUMNS)
    parts = [
        f"SELECT id, {cols} FROM {assert_valid(t)}" + (f" WHERE {where}" if where else "")
        for t in tables
    ]
    sql = " UNION ALL ".join(parts)
    if len(parts) > 1:
        sql = f"SELECT * FROM ({sql}) AS merged"
    if order:
        sql += f" ORDER BY {order}"
    if limit:
        sql += f" {limit}"
    return sql


def reset_cache() -> None:
    """测试用：清掉建表缓存。"""
    _known_tables.clear()
