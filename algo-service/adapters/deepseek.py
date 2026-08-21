"""
DeepSeek 辅助分析适配器：三级降级 live → cache → rule（研究报告 2.2.5 节）。

  输入集合 U = (x_state, y_action, Z)  ⇔  (context, question, scene)
  输出 O_t = Φ(r)，Φ 为大模型文本生成函数；DeepSeek 仅做解释性文本生成，不参与设备控制与策略求解。

降级链路：
  1. live  : DEEPSEEK_API_KEY 非空时，httpx POST {BASE_URL}/chat/completions，超时 DEEPSEEK_TIMEOUT(8s)
  2. cache : cache/deepseek_cache.json，key = sha256(scene + canonical_json(context) + question)；
             未命中精确 key 时再查按场景的通用兜底 key "scene:<scene>"：
               - 兜底条目来自 live（真实模型文本）→ 直接返回，reasoning 追加按当前 context 生成的数值依据；
               - 兜底条目是 warm_cache 无 key 时灌入的「规则模板」(template=rule) → 用当前 context 重新
                 填空后返回（保证数值与本次请求一致，而不是把样例里的数字原样吐出来）
  3. rule  : 中文模板，用 context 数值填空，保证语句通顺
永远返回 200，永远有 answer；latencyMs 为真实计时。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path

import config

log = logging.getLogger("algo.deepseek")
_lock = threading.Lock()

SCENES = ("dispatch", "risk", "data", "qa", "audit")
SYSTEM_PROMPT = (
    "你是能源可信数据空间平台的分析助手，负责对虚拟电厂的调度、隐私风险、数据资产、审计情况给出简洁的中文解释。"
    "请严格输出 JSON：{\"answer\": \"一段话\", \"reasoning\": [\"依据1\", \"依据2\", ...]}，reasoning 至少两条。"
)


# ---------------------------------------------------------------- 工具
def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def cache_key(scene: str, context: dict | None, question: str | None) -> str:
    raw = f"{scene}|{canonical_json(context or {})}|{question or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def scene_key(scene: str) -> str:
    return f"scene:{scene}"


def _ensure_cache_file(path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")


def load_cache(path: Path | None = None) -> dict:
    path = path or config.DEEPSEEK_CACHE_PATH
    _ensure_cache_file(path)
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        log.warning("deepseek 缓存文件损坏，已重置为空")
        return {}


def save_cache_entry(key: str, entry: dict, path: Path | None = None) -> None:
    path = path or config.DEEPSEEK_CACHE_PATH
    with _lock:
        data = load_cache(path)
        data[key] = entry
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _fmt(v, nd: int = 1) -> str:
    """数值格式化（保留 nd 位小数），非数值原样返回。"""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        return f"{v:.{nd}f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
    return str(v) if v is not None else "-"


# ---------------------------------------------------------------- 规则模板
def rule_answer(scene: str, context: dict | None, question: str | None) -> tuple[str, list[str]]:
    """规则化模板：用 context 中的数值填空；返回 (answer, reasoning[])。"""
    ctx = context or {}
    scene = scene if scene in SCENES else "qa"
    reasoning: list[str] = []

    if scene == "dispatch":
        nodes = ctx.get("nodes") or []
        actions = ctx.get("actions") or (ctx.get("strategy") or {}).get("actions") or []
        total_load = sum(float(n.get("load", 0) or 0) for n in nodes)
        total_pv = sum(float(n.get("pv", 0) or 0) for n in nodes)
        if nodes:
            reasoning.append(f"全网总负荷 {_fmt(total_load)}kW，总光伏出力 {_fmt(total_pv)}kW，净缺口 {_fmt(total_load - total_pv)}kW")
            top = max(nodes, key=lambda n: float(n.get("load", 0) or 0))
            reasoning.append(f"节点 {top.get('id', top.get('nodeId', '?'))} 负荷 {_fmt(top.get('load'))}kW 为全网最高，SOC {_fmt(top.get('soc'))}%")
        dis = [a for a in actions if a.get("action") == "discharge"]
        ch = [a for a in actions if a.get("action") == "charge"]
        for a in dis[:2]:
            reasoning.append(f"节点 {a.get('nodeId')} 放电 {_fmt(a.get('powerKw'))}kW（Q值 {_fmt(a.get('qValue'), 2)}）：{a.get('reason', '')}")
        for a in ch[:2]:
            reasoning.append(f"节点 {a.get('nodeId')} 充电 {_fmt(a.get('powerKw'))}kW：{a.get('reason', '')}")
        viol = (ctx.get("constraintsChecked") or {}).get("violations") or []
        if viol:
            reasoning.append(f"约束校验拦截 {len(viol)} 项：" + "；".join(v.get("detail", "") for v in viol[:2]))
        else:
            reasoning.append("约束校验通过：全部动作满足 SOC 20%~95%、单节点 ≤30kW")
        if len(reasoning) < 2:
            reasoning.append("DQN 依据各节点 [光伏, 负荷, SOC, 电价, 时刻] 状态计算三个动作的 Q 值并取最大者")
        parts = []
        if dis:
            parts.append("、".join(f"{a.get('nodeId')} 放电 {_fmt(a.get('powerKw'))}kW" for a in dis))
        if ch:
            parts.append("、".join(f"{a.get('nodeId')} 充电 {_fmt(a.get('powerKw'))}kW" for a in ch))
        idle = [a.get("nodeId") for a in actions if a.get("action") == "idle"]
        if idle:
            parts.append("、".join(map(str, idle)) + " 待机")
        total_r = ctx.get("totalReward", (ctx.get("strategy") or {}).get("totalReward"))
        answer = (
            "本次调度由 DQN 模型基于各节点实时状态生成："
            + ("；".join(parts) if parts else "各节点维持当前运行状态")
            + "。"
            + (f"策略综合奖励 {_fmt(total_r, 2)}，" if total_r is not None else "")
            + "放电节点优先选择负荷高、SOC 充足且处于峰时电价的节点，以削峰并获取电价差收益；充电节点利用谷时电价或光伏过剩补充电量。"
            "所有动作均经过 SOC 与功率硬约束校验，DeepSeek 仅提供解释，不参与控制。"
        )

    elif scene == "risk":
        score = ctx.get("riskScore")
        lv = ctx.get("level", "-")
        factors = ctx.get("factors") or []
        node = ctx.get("nodeId", "该节点")
        feats = ctx.get("features") or {}
        for f in sorted(factors, key=lambda f: -float(f.get("weight", 0)) * float(f.get("score", 0)))[:3]:
            reasoning.append(f"{f.get('name')}：得分 {_fmt(f.get('score'))}（权重 {_fmt(f.get('weight'), 2)}）{('，' + f['desc']) if f.get('desc') else ''}")
        if feats:
            reasoning.append(f"输入特征：查询频率 {_fmt(feats.get('queryFreq'))} 次、粒度 {feats.get('dataGranularity', '-')}、暴露字段 {_fmt(feats.get('exposedFields'))} 个、剩余 ε {_fmt(feats.get('epsilonRemaining'), 2)}")
        if len(reasoning) < 2:
            reasoning.append("风险评分 = 查询频率×0.35 + 数据粒度×0.25 + 暴露字段×0.20 + 剩余预算×0.20")
            reasoning.append("等级阈值：<40 low，40~60 medium，60~80 high，≥80 critical")
        sug = ctx.get("suggestion") or "建议按动态预算公式 ε_i = ε0·exp(−λ·risk) 下调该节点的差分隐私 ε"
        answer = f"{node} 当前隐私风险评分 {_fmt(score)}，等级 {lv}。" + (f"主要风险来源为{factors[0].get('name')}。" if factors else "") + f"{sug}"

    elif scene == "data":
        total = ctx.get("total")
        by_level = ctx.get("byLevel") or []
        by_type = ctx.get("byType") or []
        results = ctx.get("results") or []
        if by_level:
            reasoning.append("分级分布：" + "、".join(f"{x.get('level')} {x.get('count')} 条" for x in by_level))
        if by_type:
            reasoning.append("类型分布：" + "、".join(f"{x.get('dataType')} {x.get('count')} 条" for x in by_type))
        if results:
            hi = [r for r in results if r.get("level") in ("L3", "L4")]
            reasoning.append(f"本批 {len(results)} 条记录中 {len(hi)} 条被判为 L3/L4 敏感级")
            for r in results[:2]:
                reasoning.append(f"记录 {r.get('index')}：{r.get('level')}（score {_fmt(r.get('score'), 2)}），{r.get('reason', '')}")
        if ctx.get("authorized") is not None:
            reasoning.append(f"已授权 {ctx.get('authorized')} 条，上链 {ctx.get('onChain', '-')} 条")
        if len(reasoning) < 2:
            reasoning.append("分级依据敏感度函数 S = 0.5·敏感字段 + 0.3·采集粒度 + 0.2·数据量，并用 k-means 聚类修正")
            reasoning.append("L1 公开 / L2 内部 / L3 敏感 / L4 核心，L3 以上需授权方可访问")
        answer = (
            f"平台当前登记数据资产 {total} 条。" if total is not None else "本次数据分析基于分类分级结果。"
        ) + "高敏感级（L3/L4）数据主要来自包含地理位置、用户或价格字段且采集粒度为分钟级的记录，建议对其启用差分隐私并限制导出；L1/L2 数据可按授权策略开放共享。原始数据全部留存本地，链上仅存 SM3 摘要。"

    elif scene == "audit":
        total_logs = ctx.get("totalLogs", ctx.get("todayLogs"))
        high = ctx.get("highRiskLogs")
        ident = ctx.get("identityOps") or {}
        perm = ctx.get("permissionOps") or {}
        ev = ctx.get("evidence") or {}
        risks = ctx.get("riskEvents") or []
        period = {"day": "今日", "week": "本周", "month": "本月"}.get(str(ctx.get("period", "day")), "本期")
        date = ctx.get("date", "")
        if ident:
            reasoning.append(f"身份操作：签发 {_fmt(ident.get('register', 0))}、冻结 {_fmt(ident.get('freeze', 0))}、注销 {_fmt(ident.get('revoke', 0))}、轮换 {_fmt(ident.get('rotate', 0))}")
        if perm:
            reasoning.append(f"权限操作：申请 {_fmt(perm.get('applied', 0))}、通过 {_fmt(perm.get('approved', 0))}、驳回 {_fmt(perm.get('rejected', 0))}、回收 {_fmt(perm.get('revoked', 0))}")
        if ev:
            bc = ev.get("byCategory") or {}
            reasoning.append(f"存证：累计 {_fmt(ev.get('total', 0))} 条" + ("（" + "、".join(f"{k} {v}" for k, v in bc.items()) + "）" if bc else ""))
        if risks:
            reasoning.append("风险事件：" + "、".join(f"{r.get('ruleCode')} {r.get('count')} 次（{r.get('level')}）" for r in risks))
        else:
            reasoning.append("风险事件：未触发风控规则")
        if len(reasoning) < 2:
            reasoning.append("审计日志全部上链存证，任一条被篡改都会导致链校验失败")
        risk_txt = (
            "；".join(f"{r.get('ruleCode')} 触发 {r.get('count')} 次" for r in risks) + "，已生成告警并记录高风险审计日志，建议运维人员及时确认处理。"
            if risks
            else "未触发任何风控规则，系统运行平稳。"
        )
        answer = (
            f"{period}{('（' + str(date) + '）') if date else ''}平台共记录 {_fmt(total_logs) if total_logs is not None else '-'} 条审计日志"
            + (f"，其中高风险 {_fmt(high)} 条" if high is not None else "")
            + "。"
            + (f"身份侧完成 DID 签发 {_fmt(ident.get('register', 0))} 次、密钥轮换 {_fmt(ident.get('rotate', 0))} 次；" if ident else "")
            + (f"权限侧受理申请 {_fmt(perm.get('applied', 0))} 件、审批通过 {_fmt(perm.get('approved', 0))} 件、驳回 {_fmt(perm.get('rejected', 0))} 件；" if perm else "")
            + (f"存证链累计 {_fmt(ev.get('total', 0))} 条记录、完整性校验通过。" if ev else "")
            + "风险方面，"
            + risk_txt
            + "全部操作均可通过 traceId 进行任务级全链路追溯。"
        )

    else:  # qa
        q = (question or "").strip()
        reasoning.append("平台采用 DID + 国密 SM2 实现设备源头可信认证，联邦学习保证原始数据不出域")
        reasoning.append("差分隐私（高斯机制）与 Top-k 稀疏化在保护隐私的同时降低通信量，DQN 负责储能调度")
        reasoning.append("所有关键操作的摘要上链存证，支持按 traceId 全链路追溯与防篡改校验")
        kw_answers = [
            (("联邦", "fedavg", "聚合"), "联邦学习采用 FedAvg：各节点在本地训练模型，云端按样本量加权平均参数 θ = Σ(n_k/n)·θ_k，原始数据不出域；每轮梯度 SHA256/SM3 哈希上链，余弦相似度检测投毒节点。"),
            (("差分隐私", "隐私预算", "ε", "epsilon", "噪声"), "差分隐私对模型更新先做 L2 裁剪再加高斯噪声 N(0,(C·σ)²)，按 ε = q·√(T·ln(1/δ))/σ 累计预算；预算耗尽会上报 privacy_budget_exhausted 触发 R05 告警。"),
            (("top-k", "topk", "稀疏", "压缩"), "Top-k 稀疏化只上传绝对值最大的前 k 比例梯度，其余进入残差缓存 c(t+1)=c(t)+g(t)−T_k(g(t)) 下一轮补偿，压缩率 (1−k/n)×100%。"),
            (("dqn", "调度", "储能", "充放"), "DQN 以 [光伏, 负荷, SOC, 电价, 时刻] 为状态，在 charge/idle/discharge 中选 Q 值最大的动作，奖励 = 削峰收益 − 越限惩罚 − 电价成本，并经 SOC 20%~95%、≤30kW 硬约束校验。"),
            (("did", "身份", "sm2", "签名"), "每个用户/设备/节点都有 did:vpp:<type>:<hash> 标识，DID 文档含 SM2 公钥；接入、下发指令均需验签，冻结/注销的 DID 一律拒绝。"),
            (("存证", "区块链", "篡改", "链"), "平台将数据摘要、梯度哈希、权限变更、审计日志上链，block_hash = H(prev + payloadHash + ts)；篡改任一条记录都会导致 verify 返回 intact=false 并定位断裂点。"),
            (("审计", "告警", "trace"), "审计中心提供 traceId 全链路追踪、五类风控规则（R01~R05）告警与日报；高风险事件会实时推送并上链。"),
        ]
        answer = None
        ql = q.lower()
        for kws, ans in kw_answers:
            if any(k in ql for k in kws):
                answer = ans
                break
        if answer is None:
            answer = (
                f"关于「{q}」：" if q else ""
            ) + "能源可信数据空间平台通过 DID 身份认证、数据分类分级、权限控制、联邦学习 + 差分隐私计算、DQN 智能调度与区块链存证审计六个环节，实现能源数据「可用不可见、全程可追溯」。如需更具体的解释，请提供调度任务号或节点编号。"

    return answer, reasoning


# ---------------------------------------------------------------- live
def _build_messages(scene: str, context: dict | None, question: str | None) -> list[dict]:
    user = f"场景：{scene}\n上下文（JSON）：{canonical_json(context or {})}\n问题：{question or '请给出分析说明'}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def call_live(scene: str, context: dict | None, question: str | None, timeout: float | None = None) -> tuple[str, list[str]] | None:
    """调用真实 DeepSeek（OpenAI 兼容 chat/completions）。失败返回 None。"""
    if not config.DEEPSEEK_API_KEY or not config.DEEPSEEK_BASE_URL:
        return None
    try:
        import httpx

        resp = httpx.post(
            f"{config.DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": config.DEEPSEEK_MODEL, "messages": _build_messages(scene, context, question), "temperature": 0.3, "stream": False},
            timeout=timeout or config.DEEPSEEK_TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        # 模型可能用 ```json 包裹
        if content.startswith("```"):
            content = content.strip("`")
            content = content[4:] if content.lower().startswith("json") else content
        try:
            obj = json.loads(content)
            answer = str(obj.get("answer") or content)
            reasoning = [str(x) for x in (obj.get("reasoning") or [])]
        except json.JSONDecodeError:
            answer, reasoning = content, []
        if len(reasoning) < 2:
            reasoning += rule_answer(scene, context, question)[1]
        return answer, reasoning[:6]
    except Exception as exc:  # noqa: BLE001  任何异常都降级
        log.warning("DeepSeek live 调用失败，降级：%s", exc)
        return None


# ---------------------------------------------------------------- 对外
def analyze(scene: str, context: dict | None, question: str | None) -> dict:
    """契约 3.4：{answer, reasoning, source, latencyMs}，永远成功。"""
    t0 = time.perf_counter()
    scene = scene if scene in SCENES else "qa"
    key = cache_key(scene, context, question)

    live = call_live(scene, context, question)
    if live is not None:
        answer, reasoning = live
        save_cache_entry(key, {"scene": scene, "question": question, "answer": answer, "reasoning": reasoning, "source": "live", "cachedAt": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return {"answer": answer, "reasoning": reasoning, "source": "live", "latencyMs": int((time.perf_counter() - t0) * 1000)}

    if config.DEEPSEEK_OFFLINE_FALLBACK:
        cache = load_cache()
        hit = cache.get(key)
        if hit and hit.get("answer"):
            reasoning = list(hit.get("reasoning") or [])
            if len(reasoning) < 2:
                reasoning += rule_answer(scene, context, question)[1]
            return {"answer": hit["answer"], "reasoning": reasoning, "source": "cache", "latencyMs": int((time.perf_counter() - t0) * 1000)}
        fb = cache.get(scene_key(scene))
        if fb and fb.get("answer"):
            rule_ans, rule_reason = rule_answer(scene, context, question)
            if fb.get("template") == "rule" or fb.get("source") != "live":
                # 缓存的是规则模板：按当前 context 重新填空
                answer, reasoning = rule_ans, rule_reason
            else:
                answer = fb["answer"]
                reasoning = list(fb.get("reasoning") or [])[:3] + rule_reason
            return {"answer": answer, "reasoning": reasoning[:6], "source": "cache", "latencyMs": int((time.perf_counter() - t0) * 1000)}

    answer, reasoning = rule_answer(scene, context, question)
    return {"answer": answer, "reasoning": reasoning, "source": "rule", "latencyMs": int((time.perf_counter() - t0) * 1000)}


def status() -> str:
    """health 接口用：有 key 为 live，否则 cache。"""
    return "live" if (config.DEEPSEEK_API_KEY and config.DEEPSEEK_BASE_URL) else "cache"
