"""
qa 测试公共夹具。
- 设置 FL_ROUND_DELAY=0（测试不等待）、DEEPSEEK_API_KEY=（强制离线降级）
- sys.path 加入 algo-service 目录，使 `import main` / `import algorithms.*` 可用
- 提供 TestClient(main.app) 与轮询工具
"""
import os
import sys
import time
from pathlib import Path

import pytest

ALGO_DIR = Path(__file__).resolve().parent.parent
if str(ALGO_DIR) not in sys.path:
    sys.path.insert(0, str(ALGO_DIR))

# 必须在 import main / config 之前设置
os.environ["FL_ROUND_DELAY"] = "0"
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ.setdefault("DEEPSEEK_OFFLINE_FALLBACK", "true")

PREFIX = "/algo/v1"


def _load_app():
    from fastapi.testclient import TestClient

    import main  # noqa: WPS433  algo-service/main.py

    return TestClient(main.app)


@pytest.fixture(scope="session")
def client():
    """整个会话复用一个 TestClient（DQN checkpoint 只加载一次）"""
    c = _load_app()
    with c:
        yield c


@pytest.fixture
def trace_id():
    return "tr-20260821-qa000001"


def wait_job(client, job_id, timeout=60.0, until=("success", "failed", "cancelled")):
    """轮询 GET /fl/jobs/{id} 直到终态，返回最后一次响应 JSON"""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        r = client.get(f"{PREFIX}/fl/jobs/{job_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last.get("status") in until:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} 在 {timeout}s 内未到终态，最后状态 {last and last.get('status')}")


def start_job(client, job_id, rounds=10, nodes=None, dp=None, topk=None, extra=None, headers=None):
    body = {
        "jobId": job_id,
        "rounds": rounds,
        "nodes": nodes or [
            {"id": "Node-A", "samples": 480},
            {"id": "Node-B", "samples": 320},
            {"id": "Node-C", "samples": 400},
            {"id": "Node-D", "samples": 360},
        ],
        "dp": dp if dp is not None else {"enabled": False, "epsilon": 1.0, "delta": 1e-5},
        "topk": topk if topk is not None else {"enabled": False, "ratio": 0.1},
    }
    if extra:
        body.update(extra)
    return client.post(f"{PREFIX}/fl/train", json=body, headers=headers or {})


@pytest.fixture
def run_job(client):
    """启动并等待完成，返回最终 job JSON"""
    counter = {"n": 0}

    def _run(rounds=10, **kw):
        counter["n"] += 1
        job_id = f"fl-qa-{int(time.time()*1000)}-{counter['n']}"
        r = start_job(client, job_id, rounds=rounds, **kw)
        assert r.status_code == 200, r.text
        return wait_job(client, job_id)

    return _run
