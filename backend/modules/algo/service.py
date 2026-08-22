"""算法代理层：联邦学习与智能调度。契约 2.9 / 2.10。

**本模块不实现任何算法。** 它做的是六件事：
权限校验 → DID 验签 → 调用 algo-service（透传 traceId）→ 结果落库 → 存证上链 → WebSocket 推进度。

真正的 FedAvg、差分隐私、Top-k、DQN 都在乙的 algo-service 里。
乙那边没就绪时用 backend/tests/fake_algo.py 顶上，接口完全一致。
"""
import asyncio
import logging
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from core.database import SessionLocal
from core.exceptions import ConflictError, NotFoundError, ParamError
from core.gm_crypto import payload_hash, sm3_tag
from core.middleware import adopt_trace, audit_step, current_principal, current_trace_id
from core.response import iso, now_cst
from core.retry import run_with_retry
from modules.algo import client as algo_client
from modules.algo.model import AlgoDispatchTask, AlgoFlRound, AlgoFlTask, AlgoModelVersion
from modules.evidence.service import write_evidence

logger = logging.getLogger(__name__)

# 轮询算法服务的节奏。间隔 2 秒是权衡结果：
# 再密会让树莓派上的后台协程和 MySQL 抢资源，再稀疏前端曲线会一顿一顿的。
_POLL_INTERVAL = 2.0
_POLL_MAX_SECONDS = 900


def _f(value) -> float | None:
    return float(value) if isinstance(value, Decimal) else value


def _next_id(db: Session, model, column, prefix: str) -> str:
    count = db.execute(select(func.count()).select_from(model)).scalar_one() or 0
    return f"{prefix}-{count + 1:06d}"


# ================================================================ 联邦学习

def create_fl_task(db: Session, payload) -> dict:
    _assert_nodes_exist(db, payload.nodeIds)
    principal = current_principal.get()

    def _persist() -> dict:
        task = AlgoFlTask(
            task_id=_next_id(db, AlgoFlTask, AlgoFlTask.task_id, "fl"),
            name=payload.name, node_ids=payload.nodeIds, rounds=payload.rounds,
            current_round=0, status="created",
            dp_enabled=1 if payload.dp.enabled else 0,
            dp_epsilon=Decimal(str(payload.dp.epsilon)), dp_delta=payload.dp.delta,
            dp_epsilon_spent=Decimal("0"),
            topk_enabled=1 if payload.topk.enabled else 0,
            topk_ratio=Decimal(str(payload.topk.ratio)),
            creator_did=principal.did if principal else None,
            trace_id=current_trace_id.get(),
        )
        db.add(task)
        db.commit()
        return {"id": task.task_id, "status": task.status,
                "createdAt": iso(task.created_at), "traceId": task.trace_id}

    # 并发创建会撞任务号（_next_id 按 count+1 生成），重放一次即可
    return run_with_retry(db, _persist, what="创建联邦学习任务")


def _assert_nodes_exist(db: Session, node_ids: list[str]) -> None:
    from modules.node.model import NodeInfo

    found = set(db.execute(
        select(NodeInfo.id).where(NodeInfo.id.in_(node_ids))
    ).scalars().all())
    missing = set(node_ids) - found
    if missing:
        raise NotFoundError(f"节点不存在：{'、'.join(sorted(missing))}")


def _fl_task_or_404(db: Session, task_id: str) -> AlgoFlTask:
    task = db.execute(
        select(AlgoFlTask).where(AlgoFlTask.task_id == task_id)
    ).scalar_one_or_none()
    if task is None:
        raise NotFoundError(f"联邦学习任务 {task_id} 不存在")
    return task


def _fl_to_item(task: AlgoFlTask, rounds: list[AlgoFlRound] | None = None,
                nodes: list[dict] | None = None) -> dict:
    item = {
        "id": task.task_id,
        "name": task.name,
        "status": task.status,
        "currentRound": task.current_round,
        "totalRounds": task.rounds,
        "dp": {
            "enabled": bool(task.dp_enabled),
            "epsilon": _f(task.dp_epsilon),
            "delta": task.dp_delta,
            "epsilonSpent": _f(task.dp_epsilon_spent),
        },
        "topk": {
            "enabled": bool(task.topk_enabled),
            "ratio": _f(task.topk_ratio),
            "compressionRatio": _f(task.compression_ratio),
        },
        "modelVersion": task.model_version,
        "creatorDid": task.creator_did,
        "traceId": task.trace_id,
        "startedAt": iso(task.started_at),
        "finishedAt": iso(task.finished_at),
        "createdAt": iso(task.created_at),
    }
    if nodes is not None:
        item["nodes"] = nodes
    if rounds is not None:
        item["rounds"] = [_round_to_item(r) for r in rounds]
    return item


def _round_to_item(r: AlgoFlRound) -> dict:
    return {
        "round": r.round,
        "loss": _f(r.loss),
        "acc": _f(r.acc),
        "compressionRatio": _f(r.compression_ratio),
        "epsilonSpent": _f(r.epsilon_spent),
        "gradientHash": r.gradient_hash,
        "nodeContributions": r.node_contributions,
        "evidenceId": r.evidence_id,
        "at": iso(r.created_at),
    }


def list_fl_tasks(db: Session, page: int, size: int,
                  status: str | None = None) -> tuple[list[dict], int]:
    stmt = select(AlgoFlTask)
    count_stmt = select(func.count()).select_from(AlgoFlTask)
    if status:
        stmt = stmt.where(AlgoFlTask.status == status)
        count_stmt = count_stmt.where(AlgoFlTask.status == status)

    total = db.execute(count_stmt).scalar_one()
    tasks = db.execute(
        stmt.order_by(AlgoFlTask.id.desc()).offset((page - 1) * size).limit(size)
    ).scalars().all()
    return [_fl_to_item(t) for t in tasks], total


def get_fl_task(db: Session, task_id: str) -> dict:
    task = _fl_task_or_404(db, task_id)
    rounds = db.execute(
        select(AlgoFlRound).where(AlgoFlRound.task_id == task_id).order_by(AlgoFlRound.round)
    ).scalars().all()

    from modules.node.model import NodeInfo

    node_rows = db.execute(
        select(NodeInfo).where(NodeInfo.id.in_(task.node_ids or []))
    ).scalars().all()
    nodes = [
        {"nodeId": n.id, "did": n.did, "joined": n.status != "offline",
         "samples": _sample_count(db, n.id)}
        for n in node_rows
    ]
    return _fl_to_item(task, rounds, nodes)


def _sample_count(db: Session, node_id: str) -> int:
    """节点参与训练的样本量 = 它的历史指标条数。真实数据，不是编的。"""
    from modules.node.model import NodeMetric

    return db.execute(
        select(func.count()).select_from(NodeMetric).where(NodeMetric.node_id == node_id)
    ).scalar_one()


def get_fl_rounds(db: Session, task_id: str) -> dict:
    _fl_task_or_404(db, task_id)
    rounds = db.execute(
        select(AlgoFlRound).where(AlgoFlRound.task_id == task_id).order_by(AlgoFlRound.round)
    ).scalars().all()
    return {"taskId": task_id, "total": len(rounds),
            "items": [_round_to_item(r) for r in rounds]}


def start_fl_task(db: Session, task_id: str) -> dict:
    """启动训练。同步部分只负责把任务交给算法服务，进度由后台协程轮询。"""
    task = _fl_task_or_404(db, task_id)
    if task.status == "running":
        raise ConflictError("任务已在运行中")
    if task.status == "success":
        raise ConflictError("任务已完成，如需重跑请新建任务")

    # B-015：训练、每轮上链、完成都沿用任务创建时的 traceId，
    # 这样 /audit/trace/{traceId} 能看到「创建 → 启动 → 每一轮 → 完成」的完整链
    trace_id = adopt_trace(task.trace_id)

    nodes = [{"id": nid, "samples": _sample_count(db, nid)} for nid in task.node_ids or []]
    algo_client.call("POST", "/fl/train", json={
        "jobId": task.task_id, "rounds": task.rounds, "nodes": nodes,
        "dp": {"enabled": bool(task.dp_enabled), "epsilon": _f(task.dp_epsilon),
               "delta": task.dp_delta},
        "topk": {"enabled": bool(task.topk_enabled), "ratio": _f(task.topk_ratio)},
    })

    task.status = "running"
    task.started_at = now_cst().replace(tzinfo=None)
    if not task.trace_id:
        task.trace_id = trace_id
    db.commit()

    return {"id": task.task_id, "status": "running", "traceId": task.trace_id}


def cancel_fl_task(db: Session, task_id: str) -> dict:
    task = _fl_task_or_404(db, task_id)
    if task.status not in ("running", "created"):
        raise ConflictError(f"任务状态为 {task.status}，不可取消")
    adopt_trace(task.trace_id)

    algo_client.call("POST", f"/fl/jobs/{task_id}/cancel", raise_on_error=False)
    task.status = "cancelled"
    task.finished_at = now_cst().replace(tzinfo=None)
    db.commit()
    return {"id": task_id, "status": "cancelled"}


async def poll_fl_job(task_id: str, trace_id: str) -> None:
    """后台轮询算法服务，把每一轮结果落库、上链、推给前端。

    用 asyncio 协程而不是线程池——树莓派上 backend 容器只有 300MB，
    开线程池跑轮询是笔不划算的账。数据库操作用 to_thread 丢出去，不阻塞事件循环。
    """
    waited = 0.0
    logger.info("开始轮询联邦学习任务 %s", task_id)

    while waited < _POLL_MAX_SECONDS:
        await asyncio.sleep(_POLL_INTERVAL)
        waited += _POLL_INTERVAL

        job = await asyncio.to_thread(
            algo_client.call, "GET", f"/fl/jobs/{task_id}", raise_on_error=False
        )
        if job is None:
            logger.warning("轮询 %s 失败，算法服务无响应", task_id)
            continue

        try:
            finished = await asyncio.to_thread(persist_fl_progress, task_id, job, trace_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("落库联邦学习进度失败 %s：%s", task_id, exc)
            continue

        if finished:
            logger.info("联邦学习任务 %s 结束", task_id)
            return

    logger.warning("联邦学习任务 %s 轮询超时（%s 秒）", task_id, _POLL_MAX_SECONDS)
    await asyncio.to_thread(_mark_failed, task_id, "轮询超时，算法服务未在预期时间内完成")


def persist_fl_progress(task_id: str, job: dict, trace_id: str) -> bool:
    """把算法服务返回的进度落库。返回任务是否已结束。

    只处理数据库里还没有的轮次，所以重复调用是安全的（轮询天然会重复拿到旧数据）。
    """
    from modules.audit.rules import fire_suspicious_gradient
    from ws import manager as ws_manager

    with SessionLocal() as db:
        task = db.execute(
            select(AlgoFlTask).where(AlgoFlTask.task_id == task_id)
        ).scalar_one_or_none()
        if task is None:
            return True

        known = set(db.execute(
            select(AlgoFlRound.round).where(AlgoFlRound.task_id == task_id)
        ).scalars().all())

        pushes = []
        for r in job.get("rounds", []):
            if r["round"] in known:
                continue

            gradient_hash = r.get("gradientHash") or sm3_tag(
                f"{task_id}:{r['round']}:{r.get('loss')}")
            evidence = write_evidence(db, category="algo", ref_id=task_id, payload={
                "action": "fl:round", "taskId": task_id, "round": r["round"],
                "loss": r.get("loss"), "acc": r.get("acc"),
                "gradientHash": gradient_hash,
                "epsilonSpent": r.get("epsilonSpent"),
                "compressionRatio": r.get("compressionRatio"),
            }, actor_did=task.creator_did, trace_id=trace_id)

            db.add(AlgoFlRound(
                task_id=task_id, round=r["round"],
                loss=_dec(r.get("loss")), acc=_dec(r.get("acc")),
                compression_ratio=_dec(r.get("compressionRatio")),
                epsilon_spent=_dec(r.get("epsilonSpent")),
                gradient_hash=gradient_hash,
                node_contributions=r.get("nodeContributions"),
                evidence_id=evidence["evidenceId"],
            ))
            pushes.append({
                "taskId": task_id, "round": r["round"], "totalRounds": task.rounds,
                "loss": r.get("loss"), "acc": r.get("acc"),
                "compressionRatio": r.get("compressionRatio"),
                "epsilonSpent": r.get("epsilonSpent"),
                "gradientHash": gradient_hash,
                "evidenceId": evidence["evidenceId"],
            })

        task.current_round = job.get("currentRound", task.current_round)
        if job.get("rounds"):
            last = job["rounds"][-1]
            task.dp_epsilon_spent = _dec(last.get("epsilonSpent")) or task.dp_epsilon_spent
            task.compression_ratio = _dec(last.get("compressionRatio")) or task.compression_ratio

        # 先 flush，否则下面查最后一轮指标时看不到本次刚 add 进去的轮次
        # （SessionLocal 关掉了 autoflush）
        db.flush()

        status = job.get("status", "running")
        finished = status in ("success", "failed", "cancelled")
        if finished:
            task.status = status
            task.finished_at = now_cst().replace(tzinfo=None)
            if status == "success" and job.get("modelVersion"):
                task.model_version = job["modelVersion"]
                _upsert_model_version(db, task, job["modelVersion"])

        anomaly = job.get("anomaly")
        creator_did = task.creator_did
        total_rounds = task.rounds
        db.commit()

    for payload in pushes:
        ws_manager.push("fl_progress", payload, trace_id)
        # B-015：每一轮都补一条审计埋点，且沿用任务的 traceId，
        # 否则 /audit/trace 的时间轴上整个训练过程是空白的（只有一条 fl:train）
        audit_step(
            module="algo", action="fl:round", risk="low", resource_type="algo",
            resource_id=task_id, trace_id=trace_id, actor_did=creator_did,
            evidence_id=payload["evidenceId"],
            detail=(f"第 {payload['round']}/{payload['totalRounds']} 轮聚合完成："
                    f"loss={payload['loss']}，acc={payload['acc']}，"
                    f"梯度哈希已上链 {payload['evidenceId']}"),
        )

    if finished:
        audit_step(
            module="algo", action="fl:finish", risk="low", resource_type="algo",
            resource_id=task_id, trace_id=trace_id, actor_did=creator_did,
            result="success" if status == "success" else "failed",
            detail=f"联邦学习任务 {task_id} 结束，状态 {status}，共 {total_rounds} 轮",
        )

    # 契约 3.2 明确要求：算法服务上报 anomaly 时必须生成 R05 高危审计日志
    if anomaly:
        fire_suspicious_gradient(task_id, anomaly)

    return finished


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _upsert_model_version(db: Session, task: AlgoFlTask, version: str) -> None:
    exists = db.execute(
        select(AlgoModelVersion.id).where(AlgoModelVersion.version == version)
    ).first()
    if exists:
        return
    last = db.execute(
        select(AlgoFlRound).where(AlgoFlRound.task_id == task.task_id)
        .order_by(AlgoFlRound.round.desc()).limit(1)
    ).scalar_one_or_none()
    db.add(AlgoModelVersion(
        version=version, task_id=task.task_id, name=f"{task.name} 产出模型 {version}",
        metrics={"loss": _f(last.loss) if last else None,
                 "acc": _f(last.acc) if last else None,
                 "rounds": task.rounds},
        status="draft", model_hash=sm3_tag(f"model-{version}-{task.task_id}"),
    ))


def _mark_failed(task_id: str, reason: str) -> None:
    with SessionLocal() as db:
        task = db.execute(
            select(AlgoFlTask).where(AlgoFlTask.task_id == task_id)
        ).scalar_one_or_none()
        if task and task.status == "running":
            task.status = "failed"
            task.finished_at = now_cst().replace(tzinfo=None)
            db.commit()
            logger.error("联邦学习任务 %s 标记为失败：%s", task_id, reason)


def list_models(db: Session, page: int, size: int) -> tuple[list[dict], int]:
    total = db.execute(select(func.count()).select_from(AlgoModelVersion)).scalar_one()
    models = db.execute(
        select(AlgoModelVersion).order_by(AlgoModelVersion.id.desc())
        .offset((page - 1) * size).limit(size)
    ).scalars().all()
    return [
        {
            "version": m.version, "taskId": m.task_id, "name": m.name,
            "metrics": m.metrics, "status": m.status, "publisherDid": m.publisher_did,
            "publishedAt": iso(m.published_at), "modelHash": m.model_hash,
            "evidenceId": m.evidence_id, "createdAt": iso(m.created_at),
        }
        for m in models
    ], total


def publish_model(db: Session, version: str) -> dict:
    model = db.execute(
        select(AlgoModelVersion).where(AlgoModelVersion.version == version)
    ).scalar_one_or_none()
    if model is None:
        raise NotFoundError(f"模型版本 {version} 不存在")
    if model.status == "published":
        raise ConflictError("该模型版本已发布")

    # 模型是某个联邦学习任务的产出，发布这一步也串回该任务的 traceId（B-015）
    if model.task_id:
        task_trace = db.execute(
            select(AlgoFlTask.trace_id).where(AlgoFlTask.task_id == model.task_id)
        ).scalar_one_or_none()
        adopt_trace(task_trace)

    principal = current_principal.get()
    model.status = "published"
    model.publisher_did = principal.did if principal else None
    model.published_at = now_cst().replace(tzinfo=None)

    evidence = write_evidence(db, category="algo", ref_id=version, payload={
        "action": "model:publish", "version": version, "taskId": model.task_id,
        "metrics": model.metrics, "modelHash": model.model_hash,
    })
    model.evidence_id = evidence["evidenceId"]
    db.commit()
    return {"version": version, "status": "published",
            "publishedAt": iso(model.published_at), "evidenceId": model.evidence_id}


# ================================================================ 智能调度

def create_dispatch_task(db: Session, payload) -> dict:
    _assert_nodes_exist(db, payload.nodeIds)
    principal = current_principal.get()

    now = now_cst()
    window = payload.timeWindow or (
        f"{now:%Y-%m-%dT%H}:00~{(now.hour + 1) % 24:02d}:00+08:00")

    def _persist() -> dict:
        task = AlgoDispatchTask(
            task_id=_next_id(db, AlgoDispatchTask, AlgoDispatchTask.task_id, "dp"),
            name=payload.name, node_ids=payload.nodeIds, time_window=window,
            status="created", creator_did=principal.did if principal else None,
            trace_id=current_trace_id.get(),
        )
        db.add(task)
        db.commit()
        return {"id": task.task_id, "status": "created", "timeWindow": window,
                "createdAt": iso(task.created_at), "traceId": task.trace_id}

    return run_with_retry(db, _persist, what="创建调度任务")


def _dispatch_or_404(db: Session, task_id: str) -> AlgoDispatchTask:
    task = db.execute(
        select(AlgoDispatchTask).where(AlgoDispatchTask.task_id == task_id)
    ).scalar_one_or_none()
    if task is None:
        raise NotFoundError(f"调度任务 {task_id} 不存在")
    return task


def _dispatch_to_item(task: AlgoDispatchTask) -> dict:
    return {
        "id": task.task_id,
        "name": task.name,
        "timeWindow": task.time_window,
        "nodeIds": task.node_ids,
        "status": task.status,
        "strategy": task.strategy,
        "totalReward": _f(task.total_reward),
        "qTable": task.q_table,
        "explanation": task.explanation,
        "explanationSource": task.explanation_source,
        "issued": bool(task.issued),
        "commandId": task.command_id,
        "signerDid": task.signer_did,
        # B-004 / B-016：签名字节本就是公开可验证数据，回传给边端才能做真验签
        # （前端 TerminalResponse.vue 拿到 signature + signPayload 就走 SM2 验签分支）
        "signature": task.signature,
        "issuedAt": iso(task.issued_at),
        "ackStatus": task.ack_status,
        "ackDetail": task.ack_detail,
        "creatorDid": task.creator_did,
        "evidenceId": task.evidence_id,
        "traceId": task.trace_id,
        "createdAt": iso(task.created_at),
        # 前端要签名下发时，签的就是这个串，不能让客户端自己决定签什么
        "signPayload": build_sign_message(task),
    }


def list_dispatch_tasks(db: Session, page: int, size: int,
                        status: str | None = None) -> tuple[list[dict], int]:
    stmt = select(AlgoDispatchTask)
    count_stmt = select(func.count()).select_from(AlgoDispatchTask)
    if status:
        stmt = stmt.where(AlgoDispatchTask.status == status)
        count_stmt = count_stmt.where(AlgoDispatchTask.status == status)

    total = db.execute(count_stmt).scalar_one()
    tasks = db.execute(
        stmt.order_by(AlgoDispatchTask.id.desc()).offset((page - 1) * size).limit(size)
    ).scalars().all()
    return [_dispatch_to_item(t) for t in tasks], total


def get_dispatch_task(db: Session, task_id: str) -> dict:
    return _dispatch_to_item(_dispatch_or_404(db, task_id))


def build_sign_message(task: AlgoDispatchTask | str) -> str:
    """待签原文：任务号 + 策略摘要。

    签策略摘要而不是签任务号，是为了让签名真正绑定到「下发了什么」——
    策略变了签名自然失效，改不了内容再复用旧签名。
    """
    if isinstance(task, str):
        with SessionLocal() as db:
            task = _dispatch_or_404(db, task)
    strategy_hash = payload_hash(task.strategy or {})
    return f"dispatch:issue:{task.task_id}:{strategy_hash}"


def run_dispatch(db: Session, task_id: str) -> dict:
    """运行 DQN 生成策略，再让 DeepSeek 解释原因。"""
    from ws import manager as ws_manager

    task = _dispatch_or_404(db, task_id)
    if task.issued:
        raise ConflictError("该任务已下发，不能重新生成策略")

    # B-015：run / issue / ack 全部沿用任务创建时的 traceId，
    # 调度链路在 /audit/trace 上才是一条「创建 → 生成策略 → 下发 → 回执」的时间轴
    trace_id = adopt_trace(task.trace_id)
    ws_manager.push("dispatch_progress",
                    {"taskId": task_id, "stage": "aggregating",
                     "detail": "汇集各节点实时运行数据"}, trace_id)

    from modules.node.model import NodeInfo

    nodes = db.execute(
        select(NodeInfo).where(NodeInfo.id.in_(task.node_ids or []))
    ).scalars().all()
    payload_nodes = [
        {"id": n.id, "pv": _f(n.pv_output), "load": _f(n.load_kw),
         "soc": _f(n.soc), "storage": _f(n.storage_output),
         "price": _current_price(db, n.id)}
        for n in nodes
    ]

    ws_manager.push("dispatch_progress",
                    {"taskId": task_id, "stage": "computing",
                     "detail": "DQN 推理生成调度策略"}, trace_id)

    result = algo_client.call("POST", "/dqn/dispatch", json={
        "taskId": task_id, "timeWindow": task.time_window, "nodes": payload_nodes,
    })

    strategy = {
        "actions": result.get("actions", []),
        "totalReward": result.get("totalReward"),
        "timeWindow": task.time_window,
        "constraintsChecked": result.get("constraintsChecked"),
    }

    ws_manager.push("dispatch_progress",
                    {"taskId": task_id, "stage": "explaining",
                     "detail": "生成策略解释"}, trace_id)

    explanation, source = _explain_dispatch(task, strategy)

    def _persist() -> dict:
        # 每次重放都重新取一遍任务，避免用到上一次失败事务里的脏对象
        row = _dispatch_or_404(db, task_id)
        row.strategy = strategy
        row.total_reward = _dec(result.get("totalReward"))
        row.q_table = result.get("qTable")
        row.status = "success"
        row.explanation = explanation
        row.explanation_source = source

        evidence = write_evidence(db, category="algo", ref_id=task_id, payload={
            "action": "dispatch:run", "taskId": task_id, "strategy": strategy,
            "explanationSource": source,
        })
        row.evidence_id = evidence["evidenceId"]
        db.commit()
        return {
            "id": task_id, "status": "success", "strategy": strategy,
            "qTable": row.q_table,
            "explanation": explanation, "explanationSource": source,
            "evidenceId": row.evidence_id, "traceId": trace_id,
            "signPayload": build_sign_message(row),
        }

    return run_with_retry(db, _persist, what="生成调度策略")


def _current_price(db: Session, node_id: str) -> float | None:
    from modules.node.model import NodeMetric

    row = db.execute(
        select(NodeMetric.price).where(NodeMetric.node_id == node_id)
        .order_by(NodeMetric.ts.desc()).limit(1)
    ).scalar_one_or_none()
    return _f(row)


def _explain_dispatch(task: AlgoDispatchTask, strategy: dict) -> tuple[str, str]:
    """策略解释：优先 DeepSeek，失败退回按策略内容拼的规则化中文。

    离线兜底是硬性要求，断网演示时这里必须还能说出人话。
    """
    result = algo_client.call("POST", "/deepseek/analyze", json={
        "scene": "dispatch",
        "context": {"taskId": task.task_id, "strategy": strategy},
        "question": "请解释这套调度策略的依据。",
    }, raise_on_error=False)
    if result and result.get("answer"):
        return result["answer"], result.get("source", "live")

    verbs = {"discharge": "放电", "charge": "充电", "idle": "待机"}
    parts = []
    for action in strategy.get("actions", []):
        parts.append(
            f"节点 {action.get('nodeId')} {verbs.get(action.get('action'), action.get('action'))}"
            f" {abs(action.get('powerKw', 0))}kW"
            + (f"（{action['reason']}）" if action.get("reason") else "")
        )
    body = "；".join(parts) if parts else "本时段各节点供需平衡，无需调整"
    return (f"本时段（{task.time_window}）策略：{body}。"
            f"综合收益 {strategy.get('totalReward')}。"), "rule"


def issue_dispatch(db: Session, task_id: str, signature: str | None) -> dict:
    """下发指令。签名校验由 @require_signature 完成，这里只处理业务状态。"""
    from ws import manager as ws_manager

    task = _dispatch_or_404(db, task_id)
    trace_id = adopt_trace(task.trace_id)
    if task.status != "success" or not task.strategy:
        raise ConflictError("请先运行 DQN 生成策略，再下发指令")
    if task.issued:
        raise ConflictError("该任务已下发，不能重复下发")

    principal = current_principal.get()

    def _persist() -> dict:
        row = _dispatch_or_404(db, task_id)
        # 条件更新占位：并发下只有把 issued 从 0 改成 1 的那个请求算下发成功，
        # 后到者拿到 rowcount=0，按契约返回 1006 而不是重复下发或 500
        claimed = db.execute(
            update(AlgoDispatchTask)
            .where(AlgoDispatchTask.task_id == task_id, AlgoDispatchTask.issued == 0)
            .values(issued=1)
            .execution_options(synchronize_session=False)
        ).rowcount
        if not claimed:
            raise ConflictError("该任务已下发，不能重复下发")

        row.issued = 1
        row.command_id = f"cmd-{row.id:06d}"
        row.signer_did = principal.did if principal else None
        # B-004：托管代签的签名由 @require_signature 回写进请求体，这里必须落库，
        # 否则 algo_dispatch_task.signature 恒为 NULL，签名无从追溯、边端也无法复核
        row.signature = signature
        row.issued_at = now_cst().replace(tzinfo=None)
        row.ack_status = "none"
        row.ack_detail = []

        targets = [a.get("nodeId") for a in (row.strategy or {}).get("actions", [])]
        evidence = write_evidence(db, category="algo", ref_id=task_id, payload={
            "action": "dispatch:issue", "taskId": task_id, "commandId": row.command_id,
            "signerDid": row.signer_did, "targets": targets,
            "signature": row.signature,
            "strategyHash": payload_hash(row.strategy),
        })
        db.commit()

        ws_manager.push("dispatch_progress",
                        {"taskId": task_id, "stage": "issued",
                         "detail": f"指令已下发至 {'、'.join(targets)}"}, trace_id)

        return {
            "issued": True, "commandId": row.command_id, "signerDid": row.signer_did,
            "evidenceId": evidence["evidenceId"], "targets": targets,
        }

    return run_with_retry(db, _persist, what="下发调度指令")


def _issue_targets(task: AlgoDispatchTask) -> set[str]:
    """本次下发真正覆盖到的节点：策略里有动作的节点，退化时用任务的节点列表。"""
    targets = {a.get("nodeId") for a in (task.strategy or {}).get("actions", [])
               if a.get("nodeId")}
    return targets or set(task.node_ids or [])


def ack_dispatch(db: Session, task_id: str, node_id: str, accepted: bool,
                 detail: str | None) -> dict:
    """边缘节点回执。回执是「端侧执行确认」，属于调度闭环的最后一环，同样要上链。"""
    from ws import manager as ws_manager

    task = _dispatch_or_404(db, task_id)
    # 先沿用任务的 traceId，被拒的回执同样要落在这条任务链上（B-015）
    trace_id = adopt_trace(task.trace_id)
    if not task.issued:
        raise ConflictError("该任务尚未下发，无法回执")

    # B-008：只有本次下发的目标节点才能回执。
    # 不校验的话任意节点都能把 ackStatus 顶成 partial，回执数据失去可信度。
    targets = _issue_targets(task)
    if node_id not in targets:
        raise ParamError(
            f"节点 {node_id} 不在本次下发的目标节点内（{('、'.join(sorted(targets))) or '无'}），拒绝回执")

    def _persist() -> dict:
        row = _dispatch_or_404(db, task_id)
        acks = [a for a in list(row.ack_detail or []) if a.get("nodeId") != node_id]
        acks.append({"nodeId": node_id, "accepted": accepted, "detail": detail,
                     "at": iso(now_cst())})
        row.ack_detail = acks

        acked = {a["nodeId"] for a in acks if a["accepted"]}
        row.ack_status = ("all" if targets and targets <= acked
                          else ("partial" if acked else "none"))

        # B-026：回执上链，前端「回执存证」才有内容，调度链路在存证中心也才完整
        evidence = write_evidence(db, category="algo", ref_id=task_id, payload={
            "action": "dispatch:ack", "taskId": task_id, "commandId": row.command_id,
            "nodeId": node_id, "accepted": accepted, "detail": detail,
            "ackStatus": row.ack_status, "at": iso(now_cst()),
        }, trace_id=trace_id)
        db.commit()

        ws_manager.push("dispatch_progress",
                        {"taskId": task_id, "stage": "acked",
                         "detail": f"节点 {node_id} 已回执（{len(acked)}/{len(targets)}）",
                         "evidenceId": evidence["evidenceId"]},
                        trace_id)

        return {"taskId": task_id, "nodeId": node_id, "ackStatus": row.ack_status,
                "acked": len(acked), "total": len(targets),
                "evidenceId": evidence["evidenceId"]}

    return run_with_retry(db, _persist, what="调度回执")
