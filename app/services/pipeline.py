"""生成编排：一次「写一条内容」的完整流水线。

这是整个项目的主干，面试时画的白板图就是它：

  缓存命中? ──是──► 直接复用（cached=True，省掉 Writer + Judge 两次调用）
      │否
      ▼
  RAG 检索品牌规范 ──► Writer 写初稿（JSON）
                        │
                        ▼
                  解析失败? ──是──► 修复重试一次
                        │
                        ▼
              Critic 硬规则 + Judge 四维打分
                        │
             总分/合规不过且还有重写次数? ──是──► 带违规明细让 Writer 重写
                        │
                        ▼
                  落库 + 记账（token / 成本 / 延迟）

一个设计细节：缓存里存的是「正文 + 质检结论 + 本轮 token」，不是只存正文。
否则命中缓存后还得再调一次 Judge 打分，缓存就只省了一半钱。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Content, ContentStatus, EvalRecord, Event
from ..providers import get_provider
from ..providers.base import ProviderError, extract_json
from . import quality
from .cache import SingleFlight, build_cache, cache_key
from .prompts import PROMPT_VERSION, build_writer_messages
from .rag import retriever

cache = build_cache()
singleflight = SingleFlight()


@dataclass
class GenResult:
    data: dict
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cached: bool = False
    rewrite_rounds: int = 0
    judged: dict = field(default_factory=dict)
    rule: dict = field(default_factory=dict)


def _cost(tokens_in: int, tokens_out: int) -> float:
    return round(
        tokens_in / 1_000_000 * settings.price_in_per_mtok
        + tokens_out / 1_000_000 * settings.price_out_per_mtok,
        6,
    )


def _payload(result: GenResult) -> str:
    return json.dumps(
        {
            "data": result.data,
            "judged": result.judged,
            "rule": result.rule,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "rewrite_rounds": result.rewrite_rounds,
        },
        ensure_ascii=False,
    )


async def _write_once(product: dict, angle: str, variant: int, guidelines: str, feedback: str) -> tuple[dict, int, int]:
    provider = get_provider()
    messages = build_writer_messages(product, angle, variant, guidelines, feedback)
    result = await provider.chat(messages, temperature=0.85, json_mode=True)
    try:
        data = extract_json(result.text)
    except ProviderError:
        # JSON 修复：把模型自己的输出和错误一起丢回去，让它重新整理成合法 JSON
        repair_messages = messages + [
            {"role": "assistant", "content": result.text[:2000]},
            {"role": "user", "content": "上面的输出不是合法 JSON。请只输出一个合法 JSON 对象，不要任何其他文字。"},
        ]
        repaired = await provider.chat(repair_messages, temperature=0.0, json_mode=True)
        data = extract_json(repaired.text)
        result = type(result)(
            text=repaired.text,
            tokens_in=result.tokens_in + repaired.tokens_in,
            tokens_out=result.tokens_out + repaired.tokens_out,
            model=repaired.model,
        )
    if not isinstance(data.get("title"), str) or not data.get("title").strip():
        raise ProviderError("模型没有返回标题", retryable=True)
    return data, result.tokens_in, result.tokens_out


async def _build(product: dict, angle: str, variant: int, started: float) -> GenResult:
    """真正生成一条内容：RAG → Writer → Critic → Judge →（不达标则重写）。"""
    query = f"{product.get('category','')} {angle} {product.get('highlights','')}"
    chunks = retriever.retrieve(query)
    guidelines = "\n\n".join(f"【{c.title}】{c.text}" for c in chunks)

    feedback = ""
    result = GenResult(data={})
    total_in = total_out = 0

    for round_no in range(settings.max_rewrite_rounds + 1):
        data, tin, tout = await _write_once(product, angle, variant, guidelines, feedback)
        total_in += tin
        total_out += tout
        title = str(data.get("title", ""))[:120]
        body = str(data.get("body", ""))
        tags = quality.normalize_tags(data.get("tags", []))

        rule = quality.check_rules(title, body, tags)
        judged = await quality.judge(get_provider(), title, body, tags)
        merged = quality.merge_scores(rule, judged)

        data.update({"title": title, "body": body, "tags": tags})
        result = GenResult(
            data=data,
            tokens_in=total_in,
            tokens_out=total_out,
            rewrite_rounds=round_no,
            latency_ms=int((time.perf_counter() - started) * 1000),
            judged=merged,
            rule=rule,
        )
        if merged["verdict"] == "pass" or round_no >= settings.max_rewrite_rounds:
            break
        # 不通过：把具体违规点变成反馈，进入下一轮重写
        feedback = quality.violations_to_feedback(rule["violations"]) or "整体调性偏硬推销，请更克制、更具体。"
        await asyncio.sleep(0)  # 让出事件循环，避免长任务饿死其他协程

    return result


async def generate_one(product: dict, angle: str, variant: int) -> GenResult:
    """生成一条内容（不含落库）。带缓存与单飞，可被并发调用。"""
    started = time.perf_counter()
    key = cache_key(product["id"], product.get("name"), angle, variant, PROMPT_VERSION)

    cached_raw = await cache.get(key)
    if cached_raw:
        payload = json.loads(cached_raw)
        return GenResult(
            data=payload["data"],
            judged=payload.get("judged", {}),
            rule=payload.get("rule", {}),
            # 命中缓存 = 这一轮没有真的调模型，token 记 0。
            # 但看板要用「如果没命中要花多少」来算节省，所以原始消耗留在缓存里备查。
            tokens_in=0,
            tokens_out=0,
            rewrite_rounds=payload.get("rewrite_rounds", 0),
            cached=True,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def _factory() -> GenResult:
        result = await _build(product, angle, variant, started)
        await cache.set(key, _payload(result))
        return result

    # 单飞：并发的同 key 请求只真正调用一次模型，避免缓存击穿
    return await singleflight.do(key, _factory)


def persist_result(db: Session, content: Content, product: dict, angle: str, variant: int, result: GenResult) -> None:
    """把生成结果写进数据库：先写 content，再写 eval_records（需要 content.id）。"""
    judged = result.judged or {}
    rule = result.rule or {}
    data = result.data

    content.title = data.get("title", "")
    content.body = data.get("body", "")
    content.tags = data.get("tags", [])
    content.image_prompt = data.get("image_prompt", "")
    content.prompt_version = PROMPT_VERSION
    content.tokens_in = result.tokens_in
    content.tokens_out = result.tokens_out
    content.cost_yuan = 0.0 if result.cached else _cost(result.tokens_in, result.tokens_out)
    content.latency_ms = result.latency_ms
    content.cached = result.cached
    content.rewrite_rounds = result.rewrite_rounds
    content.violations = rule.get("violations", [])
    content.score_detail = {
        "scores": judged.get("scores", {}),
        "reasons": judged.get("reasons", {}),
        "labels": quality.describe_dimensions(judged.get("scores", {})),
        "fallback": judged.get("fallback", False),
        "rule_score": rule.get("rule_score", 100),
    }
    content.score_total = float(judged.get("total", 0))
    content.judge_verdict = judged.get("verdict", "")
    content.status = (
        ContentStatus.NEEDS_REWORK
        if content.judge_verdict != "pass" or content.violations
        else ContentStatus.DRAFT
    )

    db.add(content)
    db.flush()  # 拿到 content.id

    for dim, score in (judged.get("scores") or {}).items():
        db.add(
            EvalRecord(
                content_id=content.id,
                dim=dim,
                score=float(score),
                reason=(judged.get("reasons") or {}).get(dim, ""),
                judge_model="mock-judge-v1" if settings.provider == "mock" else settings.llm_model,
            )
        )
    if result.cached:
        db.add(Event(type="cache_hit", payload={"product": product.get("name"), "variant": variant}))
