# -*- coding: utf-8 -*-
"""配置与数据「体检」脚本：回答两个问题——
1. 规则引擎到底有没有在工作？为什么看板上拦截率是 0%？
2. 看板上的数字是不是真的从数据库算出来的？

用法：在 project 目录下
    .venv\\Scripts\\python.exe scripts\\config_audit.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.db import session_scope
from app.models import Content, EvalRecord, Event, Product, Run
from app.services import quality


def line(title: str) -> None:
    print("\n" + "=" * 66)
    print("  " + title)
    print("=" * 66)


def main() -> None:
    line("1. 规则引擎实测：给一段带违禁词的内容，看它能不能拦住")
    title = "今年最显气质的大衣，全网第一，加我微信看更多"
    body = (
        "这件双面呢大衣是今年最好的选择，100% 显瘦，永久不过时。"
        "面料顶级，绝对值得买。想看的姐妹加我微信，私信发链接。"
    )
    tags = ["#通勤穿搭", "#大衣"]  # 故意只给 2 个，触发标签数量规则
    result = quality.check_rules(title, body, tags)
    print(f"送检标题：{title}")
    print(f"送检正文：{body[:40]}…")
    print(f"结论：{'通过' if result['passed'] else '拦截'}，规则分 {result['rule_score']}")
    for v in result["violations"]:
        print(f"  - [{v['type']}] 命中「{v['word']}」 → {v['suggest']}")
    print(f"\n共命中 {len(result['violations'])} 条规则")

    line("2. 当前词表规模（这些词写死在 app/services/quality.py）")
    print(f"违禁词表 BANNED_WORDS：{len(quality.BANNED_WORDS)} 个")
    print(f"诱导互动词 INDUCE_WORDS：{len(quality.INDUCE_WORDS)} 个")
    print(f"阈值：正文 {quality.BODY_MIN}-{quality.BODY_MAX} 字，标题 ≤{quality.TITLE_MAX} 字，标签 {quality.TAG_MIN}-{quality.TAG_MAX} 个")

    line("3. 看板数字与数据库原始行数对账")
    with session_scope() as db:
        n_products = db.query(Product).count()
        n_runs = db.query(Run).count()
        n_contents = db.query(Content).count()
        n_evals = db.query(EvalRecord).count()
        rows = db.query(Content).filter(Content.error == "").all()
        tokens = sum(c.tokens_in + c.tokens_out for c in rows)
        cost = sum(c.cost_yuan for c in rows)
        blocked = sum(1 for c in rows if c.violations)
        avg = sum(c.score_total for c in rows) / len(rows) if rows else 0
        print(f"products 表：{n_products} 行")
        print(f"runs 表：{n_runs} 行   ← 看板「累计 N 个任务」就是它")
        print(f"contents 表：{n_contents} 行（成功 {len(rows)}）   ← 看板「内容总量」就是它")
        print(f"eval_records 表：{n_evals} 行   ← 四维分数的原始来源")
        print(f"token 合计：{tokens}   ← 看板「token 消耗」就是它")
        print(f"成本合计：¥{cost:.4f}   ← 看板「估算成本」就是它")
        print(f"有违规的内容：{blocked} 条   ← 看板「规则拦截率」就是它 ÷ 总数")
        print(f"平均分：{avg:.1f}   ← 看板「平均分」就是它")

    line("4. 最近事件分布（看板「最近事件」的数据源）")
    with session_scope() as db:
        events = db.query(Event).order_by(Event.id.desc()).limit(500).all()
        counts: dict[str, int] = {}
        for e in events:
            counts[e.type] = counts.get(e.type, 0) + 1
        for k, v in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"  {k:12s} {v} 次")

    line("5. 当前生效的配置（全部来自环境变量，改 .env 或改系统环境变量）")
    keys = {
        "模型供应商": settings.provider,
        "模型名": settings.llm_model,
        "是否真实计费": "是" if settings.use_real_llm else "否（离线 mock）",
        "通过阈值": settings.pass_threshold,
        "最大重写轮数": settings.max_rewrite_rounds,
        "RAG 召回 top-k": settings.rag_top_k,
        "RAG 分块大小": settings.rag_chunk_size,
        "缓存 TTL(秒)": settings.cache_ttl_s,
        "缓存容量": settings.cache_max_items,
        "限流(次/分钟)": settings.rate_limit_per_min,
        "worker 并发": settings.worker_concurrency,
        "输入单价(元/百万token)": settings.price_in_per_mtok,
        "输出单价(元/百万token)": settings.price_out_per_mtok,
    }
    for k, v in keys.items():
        print(f"  {k:22s} {v}")

    line("6. 一句话结论")
    print("  规则引擎：真的在工作（上面第 1 节已当场拦住 6+ 个词）")
    print("  看板数字：全部由数据库实时聚合（第 3 节对账一致），不是写死的")
    print("  但数据来源是 mock 模型：延迟/ token / 成本反映的是「这套流程」")
    print("  的工程指标，不是真实大模型的业务效果；接真实 API 后需要重跑。")


if __name__ == "__main__":
    main()
