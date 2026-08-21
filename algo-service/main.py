"""
能源可信数据空间平台 · 算法服务（乙方）

FastAPI 应用，实现 contract/API-CONTRACT.md 第三部分全部接口，路径前缀 /algo/v1，
响应不包装，失败返回 HTTP 4xx/5xx + {"error": "..."}。每个请求读取 X-Trace-Id 并回写响应头。

启动：uvicorn main:app --host 0.0.0.0 --port ${ALGO_PORT:-8100}
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config
from adapters import deepseek
from algorithms import classifier, risk
from algorithms.dqn import DQNDispatcher
from algorithms.fedavg import FedAvgTrainer, load_node_datasets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("algo")

app = FastAPI(title="energy-tds algo-service", version=config.SERVICE_VERSION, docs_url="/algo/v1/docs", openapi_url="/algo/v1/openapi.json")
PREFIX = "/algo/v1"

# ---------------------------------------------------------------- 全局状态
STATE: dict[str, Any] = {"dqn": None, "datasets": None, "dqn_status": "loading"}


@app.on_event("startup")
def _startup() -> None:
    # 1. 数据集（缺失自动生成）
    STATE["datasets"] = load_node_datasets()
    log.info("节点数据集已加载：%s", ", ".join(f"{k}({v[0].shape[0]})" for k, v in STATE["datasets"]["nodes"].items()))
    # 2. DQN checkpoint（缺失则快速训练一个并告警）
    if not config.DQN_MODEL_PATH.exists():
        log.warning("未找到 %s，正在以 --quick 模式训练一个临时 DQN（建议先运行 scripts/train_dqn.py 完整训练）", config.DQN_MODEL_PATH)
        from scripts.train_dqn import main as train_main

        train_main(["--quick"])
    STATE["dqn"] = DQNDispatcher(config.DQN_MODEL_PATH)
    STATE["dqn_status"] = "loaded"
    log.info("DQN 已加载：%s", STATE["dqn"].meta)
    # 3. 缓存文件兜底创建
    deepseek.load_cache()
    log.info("DeepSeek 模式：%s", deepseek.status())


# ---------------------------------------------------------------- 中间件 / 异常
@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or f"tr-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    request.state.trace_id = trace_id
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        log.exception("[%s] %s %s 未捕获异常", trace_id, request.method, request.url.path)
        response = JSONResponse({"error": f"internal error: {exc}"}, status_code=500)
    response.headers["X-Trace-Id"] = trace_id
    log.info("[%s] %s %s -> %s (%.0fms)", trace_id, request.method, request.url.path, response.status_code, (time.perf_counter() - t0) * 1000)
    return response


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    errs = "; ".join(f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors())
    return JSONResponse({"error": f"参数错误: {errs}"}, status_code=400)


@app.exception_handler(Exception)
async def _generic_handler(request: Request, exc: Exception):
    log.exception("未处理异常")
    return JSONResponse({"error": str(exc)}, status_code=500)


class ApiError(Exception):
    def __init__(self, status: int, msg: str):
        self.status, self.msg = status, msg


@app.exception_handler(ApiError)
async def _api_error_handler(request: Request, exc: ApiError):
    return JSONResponse({"error": exc.msg}, status_code=exc.status)


# ---------------------------------------------------------------- 请求模型（字段名与契约一致）
class FlNode(BaseModel):
    id: str
    samples: int | None = None


class DpCfg(BaseModel):
    enabled: bool = False
    epsilon: float = 1.0
    delta: float = 1e-5


class TopkCfg(BaseModel):
    enabled: bool = False
    ratio: float = Field(0.1, gt=0, le=1)


class FlTrainReq(BaseModel):
    jobId: str
    rounds: int = Field(10, ge=1, le=500)
    nodes: list[FlNode] = Field(..., min_length=1)
    dp: DpCfg = DpCfg()
    topk: TopkCfg = TopkCfg()
    simulatePoison: str | None = None  # 可选扩展：指定节点 id 模拟梯度投毒（甲不传则无影响）


class DqnNode(BaseModel):
    id: str
    pv: float = 0.0
    load: float = 0.0
    soc: float = 50.0
    storage: float = 0.0
    price: float = 0.62
    hour: int | None = None  # 可选扩展：小时(0~23)，不传则按 timeWindow 或当前时间


class DqnReq(BaseModel):
    taskId: str
    timeWindow: str | None = None
    nodes: list[DqnNode] = Field(..., min_length=1)


class DeepseekReq(BaseModel):
    scene: str = "qa"
    context: dict | None = None
    question: str | None = None


class ClassifyReq(BaseModel):
    records: list[dict] = Field(..., min_length=1)


class RiskReq(BaseModel):
    nodeId: str
    features: dict = Field(default_factory=dict)


# ---------------------------------------------------------------- FL 任务管理器
class FlJob:
    def __init__(self, req: FlTrainReq):
        self.id = req.jobId
        self.req = req
        self.status = "created"
        self.rounds: list[dict] = []
        self.trainer: FedAvgTrainer | None = None
        self.anomaly: dict | None = None  # 首个异常（契约字段，后续不覆盖）
        self.anomalies: list[dict] = []  # 全部异常（扩展字段）
        self.error: str | None = None
        self.cancel_flag = threading.Event()
        self.thread: threading.Thread | None = None
        self.createdAt = time.time()

    @property
    def model_version(self) -> str | None:
        """modelVersion = "v" + jobId 的数字部分（fl-000012 → v12），训练成功后才非空。"""
        if self.status != "success":
            return None
        digits = "".join(ch for ch in self.id if ch.isdigit()).lstrip("0")
        return f"v{digits or '0'}"

    def to_dict(self) -> dict:
        return {
            "jobId": self.id,
            "status": self.status,
            "currentRound": len(self.rounds),
            "totalRounds": self.req.rounds,
            "rounds": self.rounds,
            "modelVersion": self.model_version,
            "anomaly": self.anomaly,
            "anomalies": self.anomalies,
            **({"error": self.error} if self.error else {}),
        }

    def run(self) -> None:
        try:
            self.trainer = FedAvgTrainer(
                rounds=self.req.rounds,
                nodes=[n.model_dump() for n in self.req.nodes],
                dp=self.req.dp.model_dump(),
                topk=self.req.topk.model_dump(),
                simulate_poison=self.req.simulatePoison,
                datasets=STATE["datasets"] or load_node_datasets(),
            )
            self.status = "running"
            for _ in range(self.req.rounds):
                if self.cancel_flag.is_set():
                    self.status = "cancelled"
                    return
                res = self.trainer.run_round()
                with JOBS_LOCK:
                    self.rounds.append(res.to_dict())
                    if res.anomaly is not None:
                        self.anomalies.append(res.anomaly)
                        if self.anomaly is None:
                            self.anomaly = res.anomaly
                log.info("[fl %s] round %d loss=%.5f acc=%.3f eps=%.3f cr=%.1f%% anomaly=%s", self.id, res.round, res.loss, res.acc, res.epsilonSpent, res.compressionRatio, (res.anomaly or {}).get("type"))
                if config.FL_ROUND_DELAY > 0 and len(self.rounds) < self.req.rounds:
                    # 分段 sleep 以便及时响应取消
                    end = time.time() + config.FL_ROUND_DELAY
                    while time.time() < end and not self.cancel_flag.is_set():
                        time.sleep(min(0.1, end - time.time()))
            self.status = "cancelled" if self.cancel_flag.is_set() else "success"
        except Exception as exc:  # noqa: BLE001
            log.exception("[fl %s] 训练失败", self.id)
            self.status = "failed"
            self.error = str(exc)


JOBS: dict[str, FlJob] = {}
JOBS_LOCK = threading.Lock()


# ---------------------------------------------------------------- 路由
@app.get(PREFIX + "/health")
def health():
    return {
        "status": "ok",
        "version": config.SERVICE_VERSION,
        "models": {"dqn": STATE["dqn_status"], "deepseek": deepseek.status()},
    }


@app.post(PREFIX + "/fl/train")
def fl_train(req: FlTrainReq):
    with JOBS_LOCK:
        old = JOBS.get(req.jobId)
        if old is not None and old.status in ("created", "running"):
            raise ApiError(409, f"任务 {req.jobId} 正在运行")
        job = FlJob(req)
        JOBS[req.jobId] = job
        job.status = "running"  # 立即返回 running，线程内真正开始
        job.thread = threading.Thread(target=job.run, name=f"fl-{req.jobId}", daemon=True)
        job.thread.start()
    return {"jobId": job.id, "status": job.status}


@app.get(PREFIX + "/fl/jobs/{job_id}")
def fl_job(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise ApiError(404, f"任务 {job_id} 不存在")
    with JOBS_LOCK:
        return job.to_dict()


@app.post(PREFIX + "/fl/jobs/{job_id}/cancel")
def fl_cancel(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise ApiError(404, f"任务 {job_id} 不存在")
    if job.status in ("created", "running"):
        job.cancel_flag.set()
        if job.thread is not None:
            job.thread.join(timeout=3.0)
        job.status = "cancelled"
    return {"jobId": job.id, "status": job.status}


@app.get(PREFIX + "/fl/jobs")
def fl_jobs():
    """扩展接口（契约外，便于排查）：列出全部任务摘要。"""
    with JOBS_LOCK:
        return {"items": [{"jobId": j.id, "status": j.status, "currentRound": len(j.rounds), "totalRounds": j.req.rounds} for j in JOBS.values()]}


def _hour_from_window(tw: str | None) -> int:
    """从 "2026-08-17T15:00~16:00+08:00" 解析起始小时，失败取当前小时。"""
    if tw and "T" in tw:
        try:
            return int(tw.split("T", 1)[1][:2]) % 24
        except ValueError:
            pass
    return time.localtime().tm_hour


@app.post(PREFIX + "/dqn/dispatch")
def dqn_dispatch(req: DqnReq):
    if STATE["dqn"] is None:
        raise ApiError(503, "DQN 模型尚未加载")
    hour = _hour_from_window(req.timeWindow)
    nodes = []
    for n in req.nodes:
        d = n.model_dump()
        d["hour"] = n.hour if n.hour is not None else hour
        nodes.append(d)
    out = STATE["dqn"].dispatch(nodes)
    return {"taskId": req.taskId, **out}


@app.post(PREFIX + "/deepseek/analyze")
def deepseek_analyze(req: DeepseekReq):
    return deepseek.analyze(req.scene, req.context, req.question)


@app.post(PREFIX + "/classify")
def classify(req: ClassifyReq):
    return classifier.classify(req.records)


@app.post(PREFIX + "/risk/assess")
def risk_assess(req: RiskReq):
    return risk.assess(req.nodeId, req.features)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=config.ALGO_PORT)
