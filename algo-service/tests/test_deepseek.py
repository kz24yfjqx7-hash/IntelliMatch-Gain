"""
DeepSeek 三级降级：live → cache → rule。无 key 时永远 200 且 source ∈ {cache, rule}。
"""
import pytest

from conftest import PREFIX

SCENES = ["dispatch", "risk", "data", "qa", "audit"]
CONTEXTS = {
    "dispatch": {"taskId": "dp-000009", "actions": [{"nodeId": "Node-C", "action": "discharge", "powerKw": 24.0}], "totalReward": 15.7},
    "risk": {"nodeId": "Node-A", "riskScore": 72.4, "level": "high"},
    "data": {"total": 85, "byLevel": [{"level": "L1", "count": 12}, {"level": "L3", "count": 20}]},
    "qa": {},
    "audit": {"period": "day", "date": "2026-08-21", "todayLogs": 420, "highRiskLogs": 6, "openAlerts": 2, "riskEvents": [{"ruleCode": "R01_UNAUTHORIZED", "count": 3, "level": "high"}]},
}


@pytest.mark.parametrize("scene", SCENES)
def test_offline_source_is_cache_or_rule(client, scene):
    r = client.post(f"{PREFIX}/deepseek/analyze", json={"scene": scene, "context": CONTEXTS[scene], "question": "请分析"})
    assert r.status_code == 200
    b = r.json()
    assert b["source"] in ("cache", "rule"), b["source"]
    assert b["answer"].strip()
    assert isinstance(b["reasoning"], list) and len(b["reasoning"]) >= 1, b
    assert all(isinstance(x, str) and x.strip() for x in b["reasoning"])


def test_audit_narrative_is_chinese_and_uses_context(client):
    """审计日报叙述需通顺并引用统计数字"""
    ctx = CONTEXTS["audit"]
    r = client.post(f"{PREFIX}/deepseek/analyze", json={"scene": "audit", "context": ctx, "question": "生成日报"})
    b = r.json()
    assert len(b["answer"]) >= 30
    assert any("一" <= ch <= "鿿" for ch in b["answer"]), "应为中文叙述"
    assert "420" in b["answer"] or "6" in b["answer"], b["answer"]


def test_dispatch_explanation_mentions_node(client):
    ctx = CONTEXTS["dispatch"]
    r = client.post(f"{PREFIX}/deepseek/analyze", json={"scene": "dispatch", "context": ctx, "question": "为什么选择节点C放电？"})
    b = r.json()
    assert "Node-C" in b["answer"] or "节点C" in b["answer"] or "C" in b["answer"], b["answer"]


def test_empty_question_and_context_still_ok(client):
    r = client.post(f"{PREFIX}/deepseek/analyze", json={"scene": "qa"})
    assert r.status_code == 200
    assert r.json()["answer"]


def test_health_reports_deepseek_cache_without_key(client):
    r = client.get(f"{PREFIX}/health")
    assert r.json()["models"]["deepseek"] == "cache"


def test_live_timeout_falls_back(client, monkeypatch):
    """模拟配置了 key 但 live 超时/异常 → 必须降级到 cache/rule 且 200"""
    import config
    from adapters import deepseek

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-fake-for-test")
    monkeypatch.setattr(config, "DEEPSEEK_BASE_URL", "http://127.0.0.1:9")  # 不可达端口
    monkeypatch.setattr(config, "DEEPSEEK_TIMEOUT", 0.2)

    import httpx

    def boom(*a, **k):
        raise httpx.TimeoutException("simulated timeout")

    monkeypatch.setattr(httpx, "post", boom)
    r = client.post(f"{PREFIX}/deepseek/analyze", json={"scene": "dispatch", "context": CONTEXTS["dispatch"], "question": "why"})
    assert r.status_code == 200
    b = r.json()
    assert b["source"] in ("cache", "rule"), b
    assert b["answer"]
    # 直接调适配器也应降级
    out = deepseek.analyze("risk", CONTEXTS["risk"], "q")
    assert out["source"] in ("cache", "rule")


def test_live_success_is_marked_live(client, monkeypatch):
    """模拟 live 成功：source=live，answer 来自响应"""
    import config

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-fake-for-test")
    monkeypatch.setattr(config, "DEEPSEEK_BASE_URL", "http://127.0.0.1:9")

    import httpx

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"answer":"模拟LIVE回答","reasoning":["a","b"]}'}}]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    r = client.post(f"{PREFIX}/deepseek/analyze", json={"scene": "qa", "context": {"k": "v-live-test"}, "question": "live?"})
    assert r.status_code == 200
    b = r.json()
    assert b["source"] == "live"
    assert "模拟LIVE回答" in b["answer"]


def test_cache_file_exists_and_is_json():
    import json

    from conftest import ALGO_DIR

    p = ALGO_DIR / "cache" / "deepseek_cache.json"
    assert p.exists(), p
    data = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
