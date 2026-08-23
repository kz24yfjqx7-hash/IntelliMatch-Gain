"""五类风险规则引擎。契约 2.7 的 R01～R05，规则码固定，前端按码展示。

计数用 Redis 滑动窗口：第一次命中时给计数键设过期时间，窗口自然滑动。
Redis 不可用时计数恒为 0，规则不会误报——宁可漏报也不能让缓存故障造成噪声告警。

命中之后做三件事：写 audit_alert 表 → 存证上链 → WebSocket 推 audit_alert 消息。
"""
import logging
import uuid

from sqlalchemy.orm import Session

from core import redis_client
from core.database import SessionLocal
from core.middleware import current_trace_id
from core.response import iso, now_cst
from modules.audit.model import AuditAlert

logger = logging.getLogger(__name__)

RULES = {
    "R01_UNAUTHORIZED": {
        "name": "越权访问",
        "level": "high",
        "threshold": 3,
        "window": 300,
        "condition": "权限校验拒绝累计 ≥ 3 次 / 5 分钟",
    },
    "R02_ABNORMAL_DID": {
        "name": "异常 DID 登录",
        "level": "critical",
        "threshold": 1,
        "window": 300,
        "condition": "已冻结/注销 DID 尝试接入，或异地 IP",
    },
    "R03_PERM_CHURN": {
        "name": "高频权限变更",
        "level": "medium",
        "threshold": 5,
        "window": 600,
        "condition": "同一主体权限变更 ≥ 5 次 / 10 分钟",
    },
    "R04_BULK_EXPORT": {
        "name": "批量数据导出",
        "level": "medium",
        "threshold": 3,
        "window": 600,
        "condition": "单次导出 ≥ 1000 条或 10 分钟内导出 ≥ 3 次",
    },
    "R05_SUSPICIOUS_GRAD": {
        "name": "可疑梯度上传",
        "level": "high",
        "threshold": 1,
        "window": 600,
        "condition": "算法服务上报梯度异常 / 隐私预算超限",
    },
}

_COUNT_PREFIX = "risk:count:"
_ALERT_FLAG_PREFIX = "risk:alerted:"


def fire(rule_code: str, *, principal=None, message: str = "",
         actor_did: str | None = None, actor_name: str | None = None,
         immediate: bool = False, db: Session | None = None,
         dedup_key: str | None = None) -> dict | None:
    """记录一次规则命中。累计到阈值才真正产生告警。

    immediate=True 时跳过计数直接告警（R02、R05 这类一次就该报的规则）。
    返回告警字典，未达阈值返回 None。**任何异常都不向外抛**。

    `db`：调用方若正处在一个**已经写过存证**的请求事务里（典型是权限变更：
    先 write_evidence 再留痕再触发 R03），必须把自己的会话传进来。
    告警本身也要上链，上链要锁链尾行；那把行锁此刻正被调用方的事务攥着没提交，
    另开一条连接去写就是自己等自己：3 秒锁超时 × 3 次重试 = 请求多挂 9 秒，
    最后上链失败，重试里的 rollback 把同一会话上刚插的告警行也一起回滚——
    告警没了、请求也慢了。与审计模块 write_audit_log(request_db=…) 是同一个坑、同一个修法。
    """
    rule = RULES.get(rule_code)
    if rule is None:
        logger.warning("未知规则码：%s", rule_code)
        return None

    if actor_did is None and principal is not None:
        actor_did = principal.did
    if actor_name is None and principal is not None:
        actor_name = principal.real_name or principal.username
    # 计数与去重的主体：默认是触发者；R05 这类「按任务」而非「按人」的规则显式传 dedup_key，
    # 否则 actor 为空时所有任务共用一个 anonymous 桶，窗口内第二个异常任务永远不告警（B-029）
    subject = dedup_key or actor_did or actor_name or "anonymous"

    try:
        threshold = 1 if immediate else rule["threshold"]
        count = redis_client.safe_incr_window(f"{_COUNT_PREFIX}{rule_code}:{subject}",
                                              rule["window"])
        # Redis 不可用时 count=0；此时只有 immediate 规则才告警
        if not immediate and (count == 0 or count < threshold):
            return None

        # 告警去重：同一规则同一主体在一个窗口内只出一条，否则越权探测一次就能
        # 刷出几十条，真正的问题反而被淹没。
        flag_key = f"{_ALERT_FLAG_PREFIX}{rule_code}:{subject}"
        if redis_client.safe_exists(flag_key):
            return None

        payload = _create_alert(rule_code, rule, message, actor_did, actor_name,
                                max(count, threshold), db)
        # 标记必须在告警**成功落库之后**才种。原来是先种标记再落库：落库一旦失败
        # （上链锁超时、事务被回滚），标记却留下了，该主体在这条规则上就整整静默
        # 一个窗口（R03 是 10 分钟），期间再怎么触发都不会有告警，而且没人知道。
        redis_client.safe_set(flag_key, "1", ex=rule["window"])
        return payload
    except Exception as exc:  # noqa: BLE001
        logger.error("风控规则 %s 执行失败：%s", rule_code, exc)
        return None


def _create_alert(rule_code: str, rule: dict, message: str, actor_did: str | None,
                  actor_name: str | None, hit_count: int,
                  request_db: Session | None = None) -> dict:
    trace_id = current_trace_id.get()
    if request_db is not None and request_db.is_active:
        # 借调用方的事务：告警行、存证块都落在同一条连接上，链尾行锁不会自锁；
        # 不在这里 commit——调用方（如 _revoke_grant）紧接着就会提交整个事务，
        # 也只有这样 core/retry.run_with_retry 的「整段重放」语义才成立。
        payload = _insert_alert(request_db, rule_code, rule, message, actor_did, actor_name,
                                hit_count, trace_id)
    else:
        with SessionLocal() as db:
            payload = _insert_alert(db, rule_code, rule, message, actor_did, actor_name,
                                    hit_count, trace_id)
            db.commit()

    logger.warning("【风控告警】%s %s：%s", rule_code, rule["name"], payload["message"])

    from ws import manager as ws_manager

    ws_manager.push("audit_alert", payload, trace_id)
    return payload


def _insert_alert(db: Session, rule_code: str, rule: dict, message: str,
                  actor_did: str | None, actor_name: str | None, hit_count: int,
                  trace_id: str | None) -> dict:
    from modules.evidence.service import write_evidence

    # alert_id 用行自增主键派生，不用 COUNT(*)+1（B-030）：
    # 两个并发告警会读到相同 count → 拼出同一个 al-000099 → 唯一键 1062 冲突，
    # 告警被 fire() 吞掉丢失（24 路压测实测 10 次）。alert_id 是 NOT NULL UNIQUE，
    # 先用一个 uuid 临时占位插入拿到自增 id，再回填成 al-<id>，天然不撞。
    alert = AuditAlert(
        alert_id=f"tmp-{uuid.uuid4().hex}", rule_code=rule_code, rule_name=rule["name"],
        risk_level=rule["level"], message=message or rule["condition"],
        actor_did=actor_did, actor_name=actor_name, hit_count=hit_count,
        trace_id=trace_id, status="open",
    )
    db.add(alert)
    db.flush()                       # 拿到 alert.id
    alert.alert_id = f"al-{alert.id:06d}"
    db.flush()

    evidence = write_evidence(db, category="audit", ref_id=alert.alert_id, payload={
        "action": "risk:alert", "ruleCode": rule_code, "ruleName": rule["name"],
        "riskLevel": rule["level"], "message": alert.message,
        "actorDid": actor_did, "hitCount": hit_count, "at": iso(now_cst()),
    }, actor_did=actor_did, trace_id=trace_id)
    alert.evidence_id = evidence["evidenceId"]
    db.flush()

    return {
        "alertId": alert.alert_id,
        "ruleCode": rule_code,
        "ruleName": rule["name"],
        "riskLevel": rule["level"],
        "message": alert.message,
        "actorDid": actor_did,
        "actorName": actor_name,
        "hitCount": hit_count,
        "evidenceId": alert.evidence_id,
        "at": iso(alert.created_at),
    }


# ---------------------------------------------------------------- 各规则的调用入口

def fire_perm_change(target_did: str, change_type: str,
                     db: Session | None = None) -> dict | None:
    """R03：同一主体权限变更过于频繁。由权限中心在每次变更留痕时调用。

    权限变更的留痕发生在同一事务刚写完存证之后，链尾行锁还在手上，
    所以这里必须把请求会话透传下去（见 fire 的说明）。
    """
    return fire("R03_PERM_CHURN", actor_did=target_did, db=db,
                message=f"主体 {target_did} 权限变更频繁，最近一次为 {change_type}")


def fire_bulk_export(principal, record_count: int) -> dict | None:
    """R04：批量导出。单次超过 1000 条直接告警，否则按 10 分钟内 3 次累计。"""
    if record_count >= 1000:
        return fire("R04_BULK_EXPORT", principal=principal, immediate=True,
                    message=f"单次导出 {record_count} 条数据，超过 1000 条阈值")
    return fire("R04_BULK_EXPORT", principal=principal,
                message=f"10 分钟内多次导出数据，最近一次 {record_count} 条")


def fire_suspicious_gradient(task_id: str, anomaly: dict, actor_did: str | None = None
                             ) -> dict | None:
    """R05：算法服务上报梯度异常或隐私预算超限。由联邦学习轮询任务调用。"""
    detail = anomaly.get("detail") or anomaly.get("type", "未知异常")
    node = anomaly.get("nodeId")
    kind = {"gradient_poisoning": "梯度投毒", "privacy_budget_exhausted": "隐私预算耗尽",
            "training_diverged": "训练发散"}.get(anomaly.get("type"), "梯度异常")
    return fire("R05_SUSPICIOUS_GRAD", actor_did=actor_did, immediate=True,
                dedup_key=f"fl:{task_id}",
                message=f"联邦学习任务 {task_id} 检测到{kind}"
                        + (f"（节点 {node}）" if node else "") + f"：{detail}")


def list_rules() -> list[dict]:
    """给前端展示规则清单用。"""
    return [{"ruleCode": code, **rule} for code, rule in RULES.items()]
