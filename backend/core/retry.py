"""并发冲突重试（B-010 / B-012 / B-014 的公共底座）。

现象与根因：多个 Agent 同时提交资产登记 / 风险评估 / 审批时，后端返回 500。
日志里是 `sqlalchemy.orm.exc.StaleDataError: UPDATE statement on table 'xxx'
expected to update 1 row(s); 0 were matched.`

根因链条（以 `POST /risk/assess` 为例）：

1. `db.add(record); db.flush()` —— INSERT 已经发到 MySQL，事务里有这一行；
2. `write_evidence()` 里链尾 `SELECT ... FOR UPDATE` 撞上死锁（1213）或锁等待超时（1205），
   **InnoDB 会把整个事务回滚掉**，第 1 步的 INSERT 随之消失；
3. 这个异常被 `write_evidence` 吞掉（存证失败不阻断业务，设计如此），
   但 SQLAlchemy 的 Session 并不知道数据库已经把事务回滚了，它仍然认为那一行是 persistent；
4. 于是 `record.evidence_id = ...; db.commit()` 发出 `UPDATE ... WHERE id=X`，
   影响行数为 0 —— ORM 判定「乐观锁冲突」抛 StaleDataError，一路冒泡成 500。

对策：把「一次完整的写事务」当成可重试单元。命中可重试错误就 `rollback()` 后整段重放，
重放仍然失败才按契约 1.1 返回 1006（状态冲突），**绝不返回 5000**。
"""
import logging
import random
import time
from typing import Callable, TypeVar

from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from core.exceptions import BizError, ConflictError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# MySQL/MariaDB 的可重试错误码与各数据库的等价文本
# 1062 是唯一键冲突：任务号按 `count+1` 生成，两个请求同时创建会撞号，
# 重放一次就能拿到新号，因此也算可重试（重放仍撞号才按 1006 返回）。
_RETRYABLE_CODES = {1213, 1205, 1614, 1062}  # 死锁 / 行锁等待超时 / 事务被回滚 / 唯一键冲突
_RETRYABLE_TEXT = ("deadlock", "lock wait timeout", "database is locked",
                   "database table is locked", "duplicate entry", "unique constraint failed")


def is_transient_conflict(exc: BaseException) -> bool:
    """判断是不是「重试一次多半就好了」的并发冲突。"""
    if isinstance(exc, StaleDataError):
        return True
    if isinstance(exc, DBAPIError):
        orig = getattr(exc, "orig", None)
        args = getattr(orig, "args", ()) or ()
        if args and isinstance(args[0], int) and args[0] in _RETRYABLE_CODES:
            return True
        text = str(orig or exc).lower()
        return any(token in text for token in _RETRYABLE_TEXT)
    return False


def run_with_retry(db: Session, action: Callable[[], T], *, what: str,
                   attempts: int = 3) -> T:
    """执行一段写事务，遇到并发冲突就回滚重放；始终失败则抛 1006。

    - `action` 必须是**可重放**的：内部自己 add / flush / commit，不依赖上一次的残留状态。
    - 业务异常（BizError）原样抛出，不重试也不改写错误码。
    """
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except BizError:
            raise
        except (StaleDataError, SQLAlchemyError) as exc:
            if not is_transient_conflict(exc):
                raise
            last = exc
            _safe_rollback(db)
            logger.warning("%s 遇到并发冲突（第 %d/%d 次）：%s", what, attempt, attempts, exc)
            if attempt < attempts:
                # 退避带抖动，避免两个请求踩着同一个节拍反复互相顶掉
                time.sleep(0.05 * attempt + random.uniform(0, 0.05))
    raise ConflictError(f"{what}并发冲突，请稍后重试") from last


def _safe_rollback(db: Session) -> None:
    try:
        db.rollback()
    except Exception as exc:  # noqa: BLE001  连接已经废掉时 rollback 也会抛
        logger.debug("回滚失败（忽略）：%s", exc)
