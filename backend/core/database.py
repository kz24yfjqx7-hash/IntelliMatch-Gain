"""SQLAlchemy 2.x 同步引擎与会话。

树莓派内存约束：连接池开得很克制，所有大表查询必须走分页，禁止一次性把全表拉进内存。

连接池容量（B-022）：一个写操作请求会同时占用**两条**连接——
请求自身的 get_db 会话，加上 @audited 里 write_audit_log 另开的审计会话。
原来的 pool_size=5 / max_overflow=5 只有 10 条，30 并发登录时后来者全部卡在
QueuePool 的 30 秒默认等待上，表现为客户端 ReadTimeout（原判读成 bcrypt 排队，实测不是）。
现在 10+20=30 条，仍远低于 MariaDB 默认 max_connections=151；
pool_timeout 一并从 30 秒压到 8 秒，池子真被打满时快速失败成 5000，
而不是把客户端吊死半分钟。
"""
import logging
import time

from sqlalchemy import BigInteger, Integer, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,        # 一个请求要占 2 条（业务会话 + 审计会话），见模块注释 B-022
    pool_timeout=8,         # 打满时快速失败，不要让客户端等满 30 秒
    pool_recycle=3600,      # MySQL 默认 8 小时断连，提前回收
    pool_pre_ping=True,     # 断线自动重连，避免容器重启后第一个请求报错
    echo=settings.DEBUG,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@event.listens_for(engine, "connect")
def _set_session_timezone(dbapi_connection, _record) -> None:
    """每条 MySQL 连接都把会话时区钉在 +08:00。

    模型层的时间列已经改成由应用侧写入（core/response.now_naive），这里是第二道保险：
    建表 DDL 里的 `DEFAULT CURRENT_TIMESTAMP` / `ON UPDATE CURRENT_TIMESTAMP` 和任何
    原生 SQL 里的 NOW() 取的都是**数据库服务器**时区，本机 MariaDB 是 UTC，
    不钉时区就会出现 created_at 与 updated_at 差 8 小时（B-023）。
    SQLite（单测）没有这个语法，直接跳过。
    """
    if engine.dialect.name != "mysql":
        return
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("SET time_zone = '+08:00'")
        cursor.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("设置会话时区失败（不影响业务，时间列已由应用侧写入）：%s", exc)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


# 自增主键类型：MySQL 用 BIGINT，SQLite 退化成 INTEGER。
# SQLite 只对 INTEGER PRIMARY KEY 做自增，不加这个 variant 单测就跑不起来。
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


def get_db():
    """FastAPI 依赖：每个请求一个会话，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def wait_for_db(timeout: int | None = None) -> None:
    """启动时等待 MySQL 就绪。

    不引入 wait-for-it.sh 之类的外部脚本（多一个依赖多一份风险），
    直接在应用里做重试循环。MySQL 容器首次启动要执行 initdb 脚本，可能要等几十秒。
    """
    timeout = timeout or settings.DB_WAIT_TIMEOUT
    deadline = time.time() + timeout
    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("MySQL 已就绪（第 %d 次尝试）", attempt)
            return
        except Exception as exc:  # noqa: BLE001
            if time.time() > deadline:
                raise RuntimeError(f"等待 MySQL 超时（{timeout}s）：{exc}") from exc
            logger.warning("MySQL 未就绪，2 秒后重试（第 %d 次）：%s", attempt, exc)
            time.sleep(2)
