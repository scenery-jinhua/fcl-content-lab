# -*- coding: utf-8 -*-
"""端到端冒烟测试：起一个真实 HTTP 客户端，把「建商品 → 提交任务 → 轮询 → 审核」跑一遍。

为什么要有这个脚本：
- 单元测试验证的是函数，这里验证的是「接口 + 队列 + 数据库 + 前端契约」串起来是否真的通；
- 面试时可以直接跑给人看，比说「我测过了」有说服力。
"""

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=30.0)

    health = client.get("/health").json()
    print("[1] health:", health)

    product = client.post(
        "/api/products",
        json={
            "name": "双面呢落肩大衣（冒烟测试）",
            "category": "大衣",
            "fabric": "羊毛双面呢",
            "highlights": "落肩、手工锁边、不压肩",
            "price_band": "3000-4000",
        },
    ).json()
    print("[2] product:", product["id"], product["name"])

    run = client.post("/api/runs", json={"product_id": product["id"], "angle": "通勤场景", "variants": 3}).json()
    print("[3] run:", run["id"], run["status"], run["total"])

    detail = {}
    for _ in range(40):
        detail = client.get(f"/api/runs/{run['id']}").json()
        done = detail["run"]["done"] + detail["run"]["failed"]
        if done >= detail["run"]["total"]:
            break
        time.sleep(0.5)

    contents = detail["contents"]
    print(f"[4] 完成 {detail['run']['done']}/{detail['run']['total']}，拿到 {len(contents)} 条")
    for c in contents:
        print(
            f"    - {c['title']} | 总分 {c['score_total']} | {c['judge_verdict']} "
            f"| tokens {c['tokens_in'] + c['tokens_out']} | {c['latency_ms']}ms"
        )
        print("      正文:", c["body"].replace("\n", " ")[:80], "...")
        print("      维度:", {k: round(v) for k, v in (c["score_detail"].get("scores") or {}).items()})

    if not contents:
        print("!! 没有内容生成")
        return 1

    first = contents[0]
    reviewed = client.post(f"/api/runs/contents/{first['id']}/review", json={"action": "approve"}).json()
    print("[5] 审核:", reviewed["status"])

    dash = client.get("/api/dashboard").json()
    print("[6] 看板:", json.dumps({
        "total": dash["total_contents"],
        "first_pass": dash["first_pass_rate"],
        "cache": dash["cache_hit_rate"],
        "p95_ms": dash["p95_latency_ms"],
        "avg_score": dash["avg_score"],
        "cost": dash["estimated_cost_yuan"],
    }, ensure_ascii=False))

    sysinfo = client.get("/api/system").json()
    print("[7] 系统:", json.dumps({"provider": sysinfo["provider"], "rag": sysinfo["rag"], "cache": sysinfo["cache"]}, ensure_ascii=False))

    page = client.get("/")
    print("[8] 首页:", page.status_code, "长度", len(page.text), "含 FCL:", "FCL" in page.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
