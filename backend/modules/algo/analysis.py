"""AI 智能分析与隐私风险评估。契约 2.11 / 2.12。

两个接口都遵循同一条原则：**永远返回 200，永远有可读的中文结论**。
真实 API → 缓存 → 规则化模板，三级降级由乙的 algo-service 内部完成；
后端这一层再兜一次底，保证算法服务整个挂掉时接口也不会报错。
"""
import logging
import time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.exceptions import NotFoundError
from core.middleware import current_principal, current_trace_id
from core.response import iso
from modules.algo import client as algo_client
from modules.algo.model import AlgoAiAnalysis, AlgoRiskAssessment
from modules.evidence.service import write_evidence

logger = logging.getLogger(__name__)


def _f(value) -> float | None:
    return float(value) if isinstance(value, Decimal) else value


# ================================================================ AI 分析

_SCENE_FALLBACK = {
    "dispatch": (
        "当前调度策略依据各节点的实时负荷、光伏出力与储能荷电状态综合计算得出："
        "负荷高且储能有裕度的节点安排放电削峰，光伏富余且荷电状态偏低的节点安排充电就地消纳，"
        "供需基本平衡的节点保持待机以留出备用容量。",
        ["按负荷从高到低排序确定削峰优先级", "校验 SOC 是否在 20%~95% 安全区间",
         "单节点出力不超过额定容量限值"],
    ),
    "risk": (
        "当前隐私风险主要来自查询频率与数据粒度的叠加效应："
        "高频查询配合分钟级粒度可以反推出用能主体的行为规律，"
        "建议收紧差分隐私预算并对高敏感字段做聚合处理。",
        ["查询频率超出基线", "分钟级粒度可反推用能行为", "暴露字段中含地理位置"],
    ),
    "data": (
        "该数据的敏感等级由三个因素共同决定：字段敏感度、采集粒度和数据量。"
        "含地理位置或主体标识、且采集粒度在分钟级以内的数据会被判定为敏感级以上，"
        "需要经过权限申请审批才能访问。",
        ["检查敏感字段", "评估采集粒度", "统计数据量级"],
    ),
    "audit": (
        "本期审计记录显示平台各项操作均已完整留痕并上链，"
        "被拒绝的越权尝试已由权限中心拦截并生成高危审计日志，"
        "建议持续关注越权访问类事件的发生频次。",
        ["全部高危操作已上链", "越权尝试已被拦截", "审计日志可按 traceId 全链路追溯"],
    ),
    "qa": (
        "可信数据空间是在数据不出域的前提下，通过可信身份、可信存证与细粒度授权"
        "实现数据要素安全流通的基础设施。本平台的四大安全能力分别对应："
        "DID 解决「你是谁」，权限中心解决「你能做什么」，"
        "存证链解决「做过什么改不了」，审计中心解决「出事能查清」。",
        ["身份可信", "过程可溯", "权限可控"],
    ),
}


def analyze(db: Session, scene: str, context: dict, question: str) -> dict:
    started = time.perf_counter()
    principal = current_principal.get()
    trace_id = current_trace_id.get()

    result = algo_client.call("POST", "/deepseek/analyze", json={
        "scene": scene, "context": context, "question": question,
    }, raise_on_error=False)

    if result and result.get("answer"):
        answer = result["answer"]
        reasoning = result.get("reasoning", [])
        source = result.get("source", "live")
        latency = result.get("latencyMs", int((time.perf_counter() - started) * 1000))
    else:
        # 算法服务整个不可达时的最后一道兜底
        answer, reasoning = _SCENE_FALLBACK.get(scene, _SCENE_FALLBACK["qa"])
        source = "rule"
        latency = int((time.perf_counter() - started) * 1000)
        logger.info("算法服务不可用，AI 分析（scene=%s）退回规则化文本", scene)

    record = AlgoAiAnalysis(
        scene=scene, context=context, question=question, answer=answer,
        reasoning=reasoning, source=source, latency_ms=latency,
        actor_did=principal.did if principal else None, trace_id=trace_id,
    )
    db.add(record)
    db.flush()

    evidence = write_evidence(db, category="algo", ref_id=str(record.id), payload={
        "action": "ai:analyze", "scene": scene, "question": question,
        "source": source, "answerHash": answer[:200],
    })
    record.evidence_id = evidence["evidenceId"]
    db.commit()

    return {
        "id": record.id, "answer": answer, "reasoning": reasoning, "source": source,
        "latencyMs": latency, "evidenceId": record.evidence_id, "traceId": trace_id,
    }


def analysis_history(db: Session, page: int, size: int,
                     scene: str | None = None) -> tuple[list[dict], int]:
    stmt = select(AlgoAiAnalysis)
    count_stmt = select(func.count()).select_from(AlgoAiAnalysis)
    if scene:
        stmt = stmt.where(AlgoAiAnalysis.scene == scene)
        count_stmt = count_stmt.where(AlgoAiAnalysis.scene == scene)

    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(
        stmt.order_by(AlgoAiAnalysis.id.desc()).offset((page - 1) * size).limit(size)
    ).scalars().all()
    return [
        {
            "id": r.id, "scene": r.scene, "context": r.context, "question": r.question,
            "answer": r.answer, "reasoning": r.reasoning, "source": r.source,
            "latencyMs": r.latency_ms, "actorDid": r.actor_did,
            "evidenceId": r.evidence_id, "traceId": r.trace_id,
            "createdAt": iso(r.created_at),
        }
        for r in rows
    ], total


# ================================================================ 隐私风险评估

_RISK_WEIGHTS = [
    ("查询频率", "queryFreq", 0.35),
    ("数据粒度", "dataGranularity", 0.30),
    ("暴露字段数", "exposedFields", 0.20),
    ("隐私预算余量", "epsilonRemaining", 0.15),
]
_GRANULARITY_SCORE = {"second": 100, "minute": 80, "hour": 50, "day": 20}


def _score_feature(key: str, value) -> tuple[float, str]:
    """把单个特征映射到 0~100 的分值，并给出中文说明。"""
    if key == "queryFreq":
        freq = float(value or 0)
        return min(100.0, freq * 7), f"5 分钟内 {int(freq)} 次查询"
    if key == "dataGranularity":
        return float(_GRANULARITY_SCORE.get(str(value), 50)), f"采集粒度为 {value}"
    if key == "exposedFields":
        count = float(value or 0)
        return min(100.0, count * 12), f"暴露 {int(count)} 个字段"
    if key == "epsilonRemaining":
        # 预算余量越少风险越高
        remaining = float(value if value is not None else 1.0)
        return max(0.0, min(100.0, (1 - remaining) * 100)), f"隐私预算余量 {remaining:.2f}"
    return 0.0, ""


def _level_of(score: float) -> str:
    if score < 30:
        return "low"
    if score < 55:
        return "medium"
    if score < 80:
        return "high"
    return "critical"


def assess_locally(node_id: str, features: dict) -> dict:
    factors = []
    total = 0.0
    for name, key, weight in _RISK_WEIGHTS:
        score, desc = _score_feature(key, features.get(key))
        total += score * weight
        factors.append({"name": name, "weight": weight,
                        "score": round(score, 1), "desc": desc})

    level = _level_of(total)
    if level in ("high", "critical"):
        suggestion = "建议将差分隐私 ε 由 1.0 降至 0.5，并对分钟级数据做小时聚合后再开放"
    elif level == "medium":
        suggestion = "建议限制单主体查询频率，并对地理位置字段做模糊化处理"
    else:
        suggestion = "当前隐私风险可控，保持现有策略"

    return {
        "nodeId": node_id, "riskScore": round(total, 2), "level": level,
        "factors": factors, "suggestion": suggestion, "source": "rule",
    }


def assess(db: Session, node_id: str, features: dict) -> dict:
    from modules.node.model import NodeInfo

    if db.get(NodeInfo, node_id) is None:
        raise NotFoundError(f"节点 {node_id} 不存在")

    remote = algo_client.call("POST", "/risk/assess", json={
        "nodeId": node_id, "features": features,
    }, raise_on_error=False)

    if remote and remote.get("riskScore") is not None:
        result = {**remote, "source": "algo"}
    else:
        result = assess_locally(node_id, features)

    principal = current_principal.get()
    record = AlgoRiskAssessment(
        node_id=node_id, risk_score=Decimal(str(result["riskScore"])),
        level=result["level"], features=features, factors=result.get("factors"),
        suggestion=result.get("suggestion"),
        actor_did=principal.did if principal else None,
        trace_id=current_trace_id.get(),
    )
    db.add(record)
    db.flush()

    evidence = write_evidence(db, category="algo", ref_id=node_id, payload={
        "action": "risk:assess", "nodeId": node_id, "riskScore": result["riskScore"],
        "level": result["level"], "features": features,
    })
    record.evidence_id = evidence["evidenceId"]
    db.commit()

    return {**result, "evidenceId": record.evidence_id,
            "traceId": record.trace_id, "at": iso(record.created_at)}


def risk_history(db: Session, page: int, size: int,
                 node_id: str | None = None) -> tuple[list[dict], int]:
    stmt = select(AlgoRiskAssessment)
    count_stmt = select(func.count()).select_from(AlgoRiskAssessment)
    if node_id:
        stmt = stmt.where(AlgoRiskAssessment.node_id == node_id)
        count_stmt = count_stmt.where(AlgoRiskAssessment.node_id == node_id)

    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(
        stmt.order_by(AlgoRiskAssessment.id.desc()).offset((page - 1) * size).limit(size)
    ).scalars().all()
    return [
        {
            "id": r.id, "nodeId": r.node_id, "riskScore": _f(r.risk_score),
            "level": r.level, "features": r.features, "factors": r.factors,
            "suggestion": r.suggestion, "evidenceId": r.evidence_id,
            "traceId": r.trace_id, "at": iso(r.created_at),
        }
        for r in rows
    ], total
