"""SQLAlchemy 2.x 同步引擎与会话。

树莓派内存约束：连接池刻意开得很小（pool_size=5），
所有大表查询必须走分页，禁止一次性把全表拉进内存。
"""
import logging
import time

from sqlalchemy import BigInteger, Integer, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    pool_size=5,
    max_overflow=5,
    pool_recycle=3600,      # MySQL 默认 8 小时断连，提前回收
    pool_pre_ping=True,     # 断线自动重连，避免容器重启后第一个请求报错
    echo=settings.DEBUG,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


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
