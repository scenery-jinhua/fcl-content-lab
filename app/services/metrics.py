"""看板聚合：把一堆裸数据算成人能看懂的指标。

为什么单独一层：指标口径会变（比如 P95 用怎样的分位、成功率算不算被驳回的），
把口径写死在一个文件里，别散落在 SQL 和前端里，改口径只改一处。

⚠️ 简历上的数字只能来自这里（或 demo_batch.py 的真实输出），不许手写。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Content, Event, Run

DIMS = ["brand_tone", "platform_fit", "info_density", "compliance"]


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
    return round(s[idx], 1)


def dashboard(db: Session) -> dict:
    rows = db.query(Content).filter(Content.error == "").all()
    total = len(rows)
    if total == 0:
        return {
            "total_contents": 0, "succeeded": 0, "success_rate": 0, "avg_latency_ms": 0,
            "p95_latency_ms": 0, "cache_hit_rate": 0, "total_tokens": 0,
            "estimated_cost_yuan": 0, "saved_cost_yuan": 0, "rule_block_rate": 0,
            "first_pass_rate": 0, "avg_score": 0, "by_dim": {}, "recent_events": [],
            "trend": [], "queue": {}, "runs": {"total": 0, "finished": 0},
        }

    latencies = [c.latency_ms for c in rows]
    cached = sum(1 for c in rows if c.cached)
    tokens = sum(c.tokens_in + c.tokens_out for c in rows)
    cost = sum(c.cost_yuan for c in rows)
    # 命中缓存的内容如果真去调模型，大概要花这么多钱 —— 这就是「省下来的」
    avg_cost = (cost / max(1, total - cached)) if total > cached else 0.0
    saved = round(avg_cost * cached, 4)
    blocked = sum(1 for c in rows if c.violations)
    first_pass = sum(1 for c in rows if c.judge_verdict == "pass" and not c.violations)

    by_dim = {}
    for dim in DIMS:
        vals = [float((c.score_detail or {}).get("scores", {}).get(dim, 0) or 0) for c in rows]
        vals = [v for v in vals if v > 0]
        by_dim[dim] = round(sum(vals) / len(vals), 1) if vals else 0.0

    runs = db.query(Run).all()
    recent = (
        db.query(Event).order_by(Event.id.desc()).limit(8).all()
    )
    trend = _trend(db)

    from .pipeline import cache
    from .queue import pool

    return {
        "total_contents": total,
        "succeeded": total,
        "success_rate": round(total / max(1, db.query(Content).count()), 3),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1),
        "p95_latency_ms": _p95(latencies),
        "cache_hit_rate": round(cached / total, 3),
        "total_tokens": tokens,
        "estimated_cost_yuan": round(cost, 4),
        "saved_cost_yuan": saved,
        "rule_block_rate": round(blocked / total, 3),
        "first_pass_rate": round(first_pass / total, 3),
        "avg_score": round(sum(c.score_total for c in rows) / total, 1),
        "by_dim": by_dim,
        "recent_events": [
            {"type": e.type, "payload": e.payload, "at": e.created_at.isoformat()} for e in recent
        ],
        "trend": trend,
        "queue": pool.snapshot(),
        "cache_stats": cache.stats.as_dict() if hasattr(cache.stats, "as_dict") else {},
        # 前端 KPI 里会用到「累计任务数」，接口必须给出，缺字段会让整个看板渲染中断
        "runs": {"total": len(runs), "finished": sum(1 for r in runs if r.finished_at)},
    }


def _trend(db: Session, days: int = 7) -> list[dict]:
    """按天聚合，给前端画一根小趋势线。SQLite 的日期函数和 MySQL 不同，这里在 Python 里聚。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.query(Content).filter(Content.created_at >= since).all()
    buckets: dict[str, dict] = {}
    for c in rows:
        day = c.created_at.strftime("%m-%d")
        b = buckets.setdefault(day, {"day": day, "count": 0, "cost": 0.0})
        b["count"] += 1
        b["cost"] = round(b["cost"] + c.cost_yuan, 4)
    return sorted(buckets.values(), key=lambda x: x["day"])
