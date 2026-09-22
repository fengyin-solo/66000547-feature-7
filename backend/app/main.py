import re, math, time, random, json, threading
from pathlib import Path
import numpy as np
from collections import defaultdict, Counter
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Log Anomaly Detector")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---------------------------------------------------------------------------
# 判定规则存储（服务端为权威口径，持久化到文件，刷新/重启后仍然保留）
# ---------------------------------------------------------------------------
RULES_FILE = Path(__file__).resolve().parent / "rules_store.json"
RULES_LOCK = threading.Lock()

DEFAULT_RULES = [
    {"id": 1, "name": "高频ERROR", "type": "level", "threshold": 5, "enabled": True, "keywords": []},
    {"id": 2, "name": "异常流量", "type": "count", "threshold": 200, "enabled": False, "keywords": []},
    {"id": 3, "name": "关键词命中", "type": "keyword", "threshold": 0, "enabled": True,
     "keywords": ["timeout", "failed", "exhausted", "error"]},
]
VALID_RULE_TYPES = {"level", "count", "keyword"}
BATCH_ACTIONS = {"enable", "disable", "set_keywords"}
MAX_KEYWORDS = 50
MAX_KEYWORD_LEN = 32


def normalize_rule(r):
    r = r if isinstance(r, dict) else {}
    return {
        "id": int(r.get("id", 0)),
        "name": str(r.get("name", "")),
        "type": str(r.get("type", "")),
        "threshold": int(r.get("threshold", 0) or 0),
        "enabled": bool(r.get("enabled", False)),
        "keywords": [str(k) for k in r.get("keywords", []) if str(k).strip()],
    }


def load_rules():
    try:
        raw = json.loads(RULES_FILE.read_text(encoding="utf-8"))
        rules = [normalize_rule(r) for r in raw if isinstance(r, dict) and r.get("id")]
        if rules:
            return rules
    except Exception:
        pass
    return [normalize_rule(r) for r in DEFAULT_RULES]


def save_rules():
    try:
        tmp = RULES_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(RULES, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(RULES_FILE)
    except Exception:
        pass  # 持久化失败不影响当次内存中的生效结果


RULES = load_rules()

# batchId -> {"fingerprint": str, "response": dict}，保证同一批次重复提交只生效一次
BATCH_TOKENS: dict = {}


class GenerateRequest(BaseModel):
    type: str = "nginx"
    count: int = 1000
    rules: list = []


class DetectRequest(BaseModel):
    logs: list
    rules: list = []
    query: str = ""


class BatchRequest(BaseModel):
    batch_id: str
    action: str
    rule_ids: list
    keywords: list = []


@app.get("/api/rules")
def get_rules():
    with RULES_LOCK:
        return {"rules": [dict(r) for r in RULES]}


@app.post("/api/rules/batch")
def batch_update_rules(req: BatchRequest):
    batch_id = (req.batch_id or "").strip()
    if not batch_id:
        raise HTTPException(status_code=400, detail="缺少 batch_id")
    action = req.action
    if action not in BATCH_ACTIONS:
        raise HTTPException(status_code=400, detail=f"不支持的批量操作: {action}")
    raw_ids = req.rule_ids if isinstance(req.rule_ids, list) else []
    try:
        ids = [int(x) for x in raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="rule_ids 必须是整数数组")
    if not ids:
        raise HTTPException(status_code=400, detail="至少选择一条规则")

    # 归一化关键词词表
    norm_keywords, invalid_keywords = [], []
    for k in req.keywords if isinstance(req.keywords, list) else []:
        k = str(k).strip().lower()
        if not k:
            continue
        if len(k) > MAX_KEYWORD_LEN:
            invalid_keywords.append(k)
        elif k not in norm_keywords:
            norm_keywords.append(k)
    if len(norm_keywords) > MAX_KEYWORDS:
        raise HTTPException(status_code=400, detail=f"关键词数量不能超过 {MAX_KEYWORDS} 个")

    # 同一批次的幂等指纹：操作 + 去重后的规则集合 + 词表内容
    fingerprint = json.dumps({
        "action": action,
        "rule_ids": sorted(set(ids)),
        "keywords": sorted(norm_keywords),
    }, ensure_ascii=False)

    with RULES_LOCK:
        cached = BATCH_TOKENS.get(batch_id)
        if cached is not None:
            if cached["fingerprint"] != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="该批次已提交过不同的操作内容，请取消后用新批次重新下发",
                )
            resp = dict(cached["response"])
            resp["idempotent"] = True  # 重复提交：直接回放结果，不再改动规则
            return resp

        index = {r["id"]: r for r in RULES}
        results = []
        seen = set()
        duplicated_ids = set()
        for rid in ids:
            if rid in seen:
                duplicated_ids.add(rid)
                continue
            seen.add(rid)
            rule = index.get(rid)
            if rule is None:
                results.append({
                    "ruleId": rid, "ruleName": "", "success": False, "changed": False,
                    "reason": "规则不存在（可能已被删除）",
                })
                continue
            if action == "enable":
                changed = not rule["enabled"]
                rule["enabled"] = True
                results.append(_ok(rule, changed))
            elif action == "disable":
                changed = rule["enabled"]
                rule["enabled"] = False
                results.append(_ok(rule, changed))
            else:  # set_keywords
                if rule["type"] != "keyword":
                    results.append({
                        "ruleId": rid, "ruleName": rule["name"], "success": False, "changed": False,
                        "reason": f"规则类型为 {rule['type']}，不是关键词规则，不能改命中词表",
                    })
                elif invalid_keywords:
                    results.append({
                        "ruleId": rid, "ruleName": rule["name"], "success": False, "changed": False,
                        "reason": f"关键词超长（>{MAX_KEYWORD_LEN}字符），未改动: {', '.join(invalid_keywords[:3])}",
                    })
                elif not norm_keywords:
                    results.append({
                        "ruleId": rid, "ruleName": rule["name"], "success": False, "changed": False,
                        "reason": "词表不能为空",
                    })
                else:
                    changed = rule["keywords"] != norm_keywords
                    rule["keywords"] = list(norm_keywords)
                    results.append(_ok(rule, changed))

        save_rules()
        success_count = sum(1 for r in results if r["success"])
        response = {
            "batchId": batch_id,
            "action": action,
            "idempotent": False,
            "results": results,
            "successCount": success_count,
            "failureCount": len(results) - success_count,
            "duplicatedIds": sorted(duplicated_ids),
            "rules": [dict(r) for r in RULES],
        }
        BATCH_TOKENS[batch_id] = {"fingerprint": fingerprint, "response": response}
        return dict(response)


def _ok(rule, changed):
    return {
        "ruleId": rule["id"], "ruleName": rule["name"],
        "success": True, "changed": changed,
        "reason": "" if changed else "目标状态与当前一致，无需改动",
    }


@app.delete("/api/rules/batch/{batch_id}")
def release_batch(batch_id: str):
    with RULES_LOCK:
        existed = BATCH_TOKENS.pop(batch_id, None) is not None
    # 取消后同一 batchId 若再次提交将重新生效；前端通常直接换发新批次
    return {"released": existed}


LOG_TEMPLATES = {
    "nginx": {
        "pattern": r'(?P<timestamp>\S+ \+\d{4}) (?P<source>\S+) (?P<level>\w+) (?P<message>.+)',
        "generator": lambda: {
            "timestamp": f"{random.randint(1,28):02d}/{'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()[random.randint(0,11)]}/{2024}:{random.randint(0,23):02d}:{random.randint(0,59):02d}:{random.randint(0,59):02d} +0000",
            "source": random.choice(["nginx", "api-gateway", "load-balancer"]),
            "level": random.choices(["INFO", "WARN", "ERROR", "DEBUG"], weights=[50, 15, 5, 30])[0],
            "message": random.choice([
                'GET /api/users 200 0.032s', 'POST /api/orders 201 0.145s', 'GET /api/products 304 0.008s',
                'GET /static/main.js 200 0.002s', 'POST /api/login 401 0.023s', 'GET /admin 403 0.005s',
                'GET /api/health 200 0.001s', 'GET /api/orders?page=2 200 0.056s', 'connection timeout upstream',
                'SSL handshake failed', 'worker process exited on signal 9', 'upstream server unavailable'
            ])
        }
    },
    "apache": {
        "pattern": r'\[(?P<timestamp>[^\]]+)\] \[(?P<level>\w+)\] \[(?P<source>\S+)\] (?P<message>.+)',
        "generator": lambda: {
            "timestamp": f"{'Sun Mon Tue Wed Thu Fri Sat'.split()[random.randint(0,6)]} {'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()[random.randint(0,11)]} {random.randint(1,28):02d} {random.randint(0,23):02d}:{random.randint(0,59):02d}:{random.randint(0,59):02d} {2024}",
            "source": random.choice(["httpd", "mod_ssl", "mod_rewrite"]),
            "level": random.choices(["notice", "warn", "error", "info"], weights=[40, 15, 5, 40])[0],
            "message": random.choice(["server configured", "caught SIGTERM", "resuming normal ops", "request exceeded limit",
                        "file does not exist", "client denied by server", "Invalid method in request"])
        }
    },
    "json_app": {
        "pattern": None,
        "generator": lambda: {
            "timestamp": f"{2024}-{random.randint(1,12):02d}-{random.randint(1,28):02d}T{random.randint(0,23):02d}:{random.randint(0,59):02d}:{random.randint(0,59):02d}.{random.randint(0,999):03d}Z",
            "source": random.choice(["user-service", "order-service", "payment-service", "auth-service"]),
            "level": random.choices(["INFO", "WARN", "ERROR", "DEBUG"], weights=[45, 20, 5, 30])[0],
            "message": random.choice([
                'User login successful user_id=10' + str(random.randint(100, 999)),
                'Order created order_id=ORD-' + str(random.randint(10000, 99999)),
                'Payment processed amount=' + str(random.randint(10, 999)),
                'Database connection pool exhausted',
                'Cache miss for key user_session_' + str(random.randint(100, 999)),
                'Circuit breaker opened for service payment',
                'Request latency exceeds threshold 5000ms',
                'NullPointerException at com.app.controller.UserController.getProfile'
            ])
        }
    },
    "custom": {
        "pattern": None,
        "generator": lambda: {
            "timestamp": str(int(time.time() - random.randint(0, 86400))),
            "source": random.choice(["cron", "systemd", "kernel", "docker"]),
            "level": random.choices(["info", "warning", "error", "debug"], weights=[40, 20, 5, 35])[0],
            "message": random.choice(["OOM killer invoked", "disk usage above 90%", "container restarted", "NTP sync lost",
                        "process oom_score_adj=500", "firewall rule updated", "mount point not found"])
        }
    }
}


@app.post("/api/generate")
def generate_logs(req: GenerateRequest):
    tmpl = LOG_TEMPLATES.get(req.type, LOG_TEMPLATES["nginx"])
    logs = []
    for i in range(req.count):
        entry = tmpl["generator"]()
        logs.append({
            "id": i + 1,
            "timestamp": entry["timestamp"],
            "level": entry["level"],
            "source": entry["source"],
            "message": entry["message"],
            "raw": f"[{entry['timestamp']}] [{entry['level']}] [{entry['source']}] {entry['message']}"
        })
    return analyze_logs(logs, req.rules, "")


@app.post("/api/detect")
def detect_anomalies(req: DetectRequest):
    return analyze_logs(req.logs, req.rules, req.query)


def analyze_logs(logs_data, rules, query):
    logs = logs_data
    n = len(logs)

    # Time windows (1min each for demonstration)
    window_size = 20
    windows = []
    for i in range(0, n, window_size):
        chunk = logs[i:i + window_size]
        levels = Counter(l["level"] for l in chunk)
        sources = Counter(l["source"] for l in chunk)
        windows.append({
            "start": i, "end": min(i + window_size, n),
            "count": len(chunk),
            "levels": dict(levels),
            "sources": dict(sources)
        })

    # 3-sigma + IQR anomaly detection
    counts = [w["count"] for w in windows]
    mean = float(np.mean(counts))
    std = float(np.std(counts)) if len(counts) > 1 else 1.0
    q1 = float(np.percentile(counts, 25)) if len(counts) > 3 else mean - std
    q3 = float(np.percentile(counts, 75)) if len(counts) > 3 else mean + std
    iqr = q3 - q1 if q3 > q1 else 1.0

    anomalies = []
    for i, w in enumerate(windows):
        sigma_score = abs(w["count"] - mean) / max(std, 1e-5)
        iqr_low = q1 - 1.5 * iqr
        iqr_high = q3 + 1.5 * iqr
        iqr_score = 0.0
        if w["count"] < iqr_low or w["count"] > iqr_high:
            iqr_score = min(10.0, abs(w["count"] - (mean)) / max(iqr, 1e-5))
        anomalies.append({
            "windowIndex": i,
            "sigmaScore": round(sigma_score, 2),
            "iqrScore": round(iqr_score, 2),
            "isAnomaly": sigma_score > 2.5 or iqr_score > 3.0,
            "timestamp": logs[i * window_size]["timestamp"] if i * window_size < len(logs) else ""
        })

    # Alert rules
    alerts = []
    for i, rule in enumerate(rules):
        rule = rule if isinstance(rule, dict) else {}
        for w in windows:
            if rule.get("type") == "level" and w["levels"].get("ERROR", 0) > rule.get("threshold", 5):
                alerts.append({
                    "id": len(alerts) + 1, "ruleName": rule.get("name", "高频ERROR"),
                    "severity": "high", "message": f"窗口{w['start']}内ERROR日志{w['levels']['ERROR']}条超过阈值{rule.get('threshold',5)}",
                    "timestamp": time.strftime("%H:%M:%S")
                })
            if rule.get("type") == "count" and w["count"] > rule.get("threshold", 200):
                alerts.append({
                    "id": len(alerts) + 1, "ruleName": rule.get("name", "异常流量"),
                    "severity": "medium", "message": f"窗口{w['start']}日志量{w['count']}超过阈值",
                    "timestamp": time.strftime("%H:%M:%S")
                })
            if rule.get("type") == "keyword":
                keywords = [str(k).lower() for k in rule.get("keywords", []) if str(k).strip()]
                if keywords:
                    chunk = logs[w["start"]:w["end"]]
                    hit_keys = set()
                    hit_count = 0
                    for l in chunk:
                        text = str(l.get("raw", "")).lower()
                        hits = [k for k in keywords if k in text]
                        if hits:
                            hit_count += 1
                            hit_keys.update(hits)
                    if hit_count > rule.get("threshold", 0):
                        alerts.append({
                            "id": len(alerts) + 1, "ruleName": rule.get("name", "关键词命中"),
                            "severity": "medium",
                            "message": f"窗口{w['start']}关键词命中{hit_count}条（{', '.join(sorted(hit_keys))}）",
                            "timestamp": time.strftime("%H:%M:%S")
                        })

    # Full-text search with TF-IDF
    if query:
        query_terms = query.lower().split()
        scored = []
        for log in logs:
            raw_lower = log["raw"].lower()
            score = sum(1 for t in query_terms if t in raw_lower)
            if score > 0:
                scored.append((score, log))
        logs = [l for _, l in sorted(scored, key=lambda x: x[0], reverse=True)]

    # Add non-rule alerts for high anomaly windows
    for a in anomalies:
        if a["isAnomaly"]:
            alerts.append({
                "id": len(alerts) + 1, "ruleName": "统计异常检测",
                "severity": "critical" if a["sigmaScore"] > 4 else "high",
                "message": f"窗口{a['windowIndex']}: 3-sigma={a['sigmaScore']}, IQR={a['iqrScore']}",
                "timestamp": a["timestamp"]
            })

    return {
        "logs": logs[:200],
        "windows": windows,
        "anomalies": anomalies,
        "alerts": alerts[:20],
        "totalLogs": n
    }
