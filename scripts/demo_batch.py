"""批量压测 / 指标采集脚本：跑 N 条，把真实指标打出来并落一份 JSON。

用法（在 project 目录下）：
    .venv\\Scripts\\python.exe scripts\\demo_batch.py --count 100
    .venv\\Scripts\\python.exe scripts\\demo_batch.py --count 100 --concurrency 8

它会：
1. 清空旧数据（可选 --keep），种入商品；
2. 起 worker 池，把所有任务入队；
3. 等全部跑完，算出成功率、P50/P95 延迟、缓存命中、token、成本、一次通过率、规则拦截率；
4. 打印一份可直接抄进简历的指标摘要，并写 data/metrics.json。

⚠️ 简历上的数字只能来自这个脚本的真实输出，不许手写。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import Content, Product, Run  # noqa: E402
from app.services.queue import Job, pool  # noqa: E402
from seed import PRODUCTS  # noqa: E402


async def run_pass(count: int, concurrency: int, tag: str = "") -> dict:
    """跑一轮 count 条，返回指标。多轮之间共享进程内缓存，用来测「缓存到底省了多少」。"""
    from app.models import Product as P

    with session_scope() as db:
        products = db.query(P).all()
        if not products:
            for item in PRODUCTS:
                db.add(P(**item))
            db.flush()
            products = db.query(P).all()
        product_ids = [p.id for p in products]

    angles = ["通勤场景", "约会场景", "松弛感", "旅行度假", "职场面试"]

    with session_scope() as db:
        run_row = Run(product_id=product_ids[0], angle=f"压测{tag}", variants=0, total=0, status="running")
        db.add(run_row)
        db.flush()
        run_id = run_row.id

    jobs = [
        Job(run_id=run_id, product_id=product_ids[i % len(product_ids)], angle=angles[i % len(angles)], variant=i)
        for i in range(count)
    ]

    started = time.perf_counter()
    for job in jobs:
        await pool.enqueue(job)

    deadline = time.time() + 900
    while not pool.queue.empty() or pool.active > 0:
        if time.time() > deadline:
            print("[demo] 超时，停止等待")
            break
        await asyncio.sleep(0.2)
    elapsed = time.perf_counter() - started

    with session_scope() as db:
        rows = (
            db.query(Content).filter(Content.run_id == run_id, Content.error == "").all()
        )
        all_rows = db.query(Content).filter(Content.run_id == run_id).all()
        contents = [
            {
                "score": c.score_total,
                "latency_ms": c.latency_ms,
                "cached": c.cached,
                "tokens": c.tokens_in + c.tokens_out,
                "cost": c.cost_yuan,
                "violations": len(c.violations),
                "verdict": c.judge_verdict,
                "rewrite": c.rewrite_rounds,
                "scores": (c.score_detail or {}).get("scores", {}),
            }
            for c in rows
        ]

    if not contents:
        print("[demo] 没有生成成功的内容")
        return {}

    lat = sorted(c["latency_ms"] for c in contents)
    p50 = lat[len(lat) // 2]
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
    cache_hits = sum(1 for c in contents if c["cached"])
    tokens = sum(c["tokens"] for c in contents)
    cost = sum(c["cost"] for c in contents)
    blocked = sum(1 for c in contents if c["violations"] > 0)
    first_pass = sum(1 for c in contents if c["verdict"] == "pass" and c["violations"] == 0)
    rewrites = sum(1 for c in contents if c["rewrite"] > 0)

    dims = {}
    for dim in ["brand_tone", "platform_fit", "info_density", "compliance"]:
        vals = [c["scores"].get(dim, 0) for c in contents if c["scores"].get(dim)]
        dims[dim] = round(sum(vals) / len(vals), 1) if vals else 0

    return {
        "count": len(contents),
        "elapsed_s": round(elapsed, 1),
        "throughput_per_s": round(len(contents) / elapsed, 2) if elapsed else 0,
        "p50_latency_ms": p50,
        "p95_latency_ms": p95,
        "avg_latency_ms": round(statistics.mean(lat), 0),
        "cache_hit_rate": round(cache_hits / len(contents), 3),
        "total_tokens": tokens,
        "avg_tokens": round(tokens / len(contents), 0),
        "estimated_cost_yuan": round(cost, 4),
        "avg_cost_yuan": round(cost / len(contents), 5),
        "rule_block_rate": round(blocked / len(contents), 3),
        "first_pass_rate": round(first_pass / len(contents), 3),
        "rewrite_rate": round(rewrites / len(contents), 3),
        "avg_score": round(sum(c["score"] for c in contents) / len(contents), 1),
        "by_dim": dims,
        "failed_rows": len(all_rows) - len(contents),
    }


async def main(count: int, concurrency: int, keep: bool, passes: int) -> None:
    settings.worker_concurrency = concurrency
    init_db()

    if not keep:
        with session_scope() as db:
            db.query(Content).delete()
            db.query(Run).delete()
            db.query(Product).delete()
        # 造商品
        with session_scope() as db:
            for item in PRODUCTS:
                db.add(Product(**item))

    await pool.start()
    results = []
    for i in range(passes):
        tag = f"-{i + 1}"
        print(f"[demo] 第 {i + 1} 轮，目标 {count} 条…")
        res = await run_pass(count, concurrency, tag)
        if res:
            res["pass"] = i + 1
            results.append(res)
    await pool.stop()

    if not results:
        return

    summary = {
        "requested_per_pass": count,
        "passes": results,
        "queue": pool.snapshot(),
        "provider": settings.provider,
        "concurrency": concurrency,
    }
    os.makedirs(os.path.join(settings.project_dir, "data"), exist_ok=True)
    out = os.path.join(settings.project_dir, "data", "metrics.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 66)
    print("  FCL 批量压测结果（可直接用于简历 / 面试口述）")
    print("=" * 66)
    for r in results:
        print(f"  ── 第 {r['pass']} 轮 ──")
        print(f"  生成条数        {r['count']} 条")
        print(f"  总耗时          {r['elapsed_s']}s，吞吐 {r['throughput_per_s']} 条/秒")
        print(f"  P50 / P95 延迟  {r['p50_latency_ms']}ms / {r['p95_latency_ms']}ms")
        print(f"  缓存命中率      {r['cache_hit_rate'] * 100:.1f}%")
        print(f"  一次通过率      {r['first_pass_rate'] * 100:.1f}%")
        print(f"  规则拦截率      {r['rule_block_rate'] * 100:.1f}%")
        print(f"  触发重写        {r['rewrite_rate'] * 100:.1f}%")
        print(f"  平均分          {r['avg_score']}（" + "、".join(f"{k} {v}" for k, v in r['by_dim'].items()) + "）")
        print(f"  token / 成本    {r['total_tokens']} / ¥{r['estimated_cost_yuan']:.4f}（单条 ¥{r['avg_cost_yuan']:.5f}）")
        print(f"  失败条数        {r['failed_rows']}")
    print(f"\n  指标已写入      {out}")
    print("=" * 66)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--keep", action="store_true", help="保留已有数据")
    parser.add_argument("--passes", type=int, default=2, help="跑几轮，用来观察缓存效果")
    args = parser.parse_args()
    asyncio.run(main(args.count, args.concurrency, args.keep, args.passes))

