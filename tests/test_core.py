"""核心逻辑单测：不依赖网络、不花一分钱。

跑法（project 目录下）：
    .venv\\Scripts\\python.exe -m pytest -q

覆盖四块最容易出 bug、也最容易被面试官追问的地方：
1. 规则引擎能不能抓到违禁词、字数、标签数量；
2. 令牌桶的速率是否真的被限制住；
3. TTL + LRU 缓存的命中、过期、淘汰；
4. RAG 的切块与检索是否返回「相关的那一段」，JSON 修复能不能兜底。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.providers.base import extract_json
from app.services.cache import TTLCache, cache_key
from app.services.quality import check_rules, normalize_tags
from app.services.rag import Retriever
from app.services.ratelimit import TokenBucket


# ---------------- 规则引擎 ----------------

def test_rule_catches_banned_word():
    r = check_rules("今年最显气质的大衣", "面料是双面呢，版型利落。", ["#通勤穿搭", "#大衣", "#质感"])
    assert not r["passed"]
    assert any(v["word"] == "最显气质" for v in r["violations"])
    assert r["rule_score"] < 100


def test_rule_reports_tag_count_and_length():
    r = check_rules("通勤大衣怎么选", "短", ["#a"])
    types = {v["type"] for v in r["violations"]}
    assert "tag_count" in types
    assert "body_too_short" in types


def test_clean_content_passes_rules():
    body = (
        "早上七点五十的闹钟，八点二十的地铁，留给穿搭的时间只有五分钟。"
        "这件灰色双面呢大衣面料有重量，但不压肩，里面配高领针织和直筒裤就能出门。"
        "袖口是手工锁边，肩线落在自然肩点，坐一整天会议室也不会起皱。"
        "价格在三千出头，是那种穿三年还在穿的类型，通勤和周末都能接住。"
    )
    r = check_rules("早八人的五分钟出门公式", body, ["#通勤穿搭", "#大衣", "#质感生活"])
    assert r["passed"], r["violations"]


def test_normalize_tags():
    assert normalize_tags(["通勤", "#通勤", "OOTD 穿搭", "#", 123]) == ["#通勤", "#OOTD穿搭", "#123"]
    assert len(normalize_tags([f"#t{i}" for i in range(20)])) == 8


# ---------------- 限流 ----------------

def test_token_bucket_limits_rate():
    async def run():
        bucket = TokenBucket(rate_per_min=600, burst=5)  # 10/s，突发 5
        started = time.monotonic()
        for _ in range(15):
            assert await bucket.acquire(timeout=5)
        elapsed = time.monotonic() - started
        # 15 个令牌：5 个突发 + 10 个按 10/s 补充 ≈ 1s
        return elapsed

    elapsed = asyncio.run(run())
    assert 0.8 <= elapsed < 2.5, f"限流未生效，耗时 {elapsed:.2f}s"


# ---------------- 缓存 ----------------

def test_cache_hit_miss_and_ttl():
    async def run():
        c = TTLCache(max_items=10, default_ttl=60)
        assert await c.get("k") is None
        await c.set("k", "v", ttl=1)
        assert await c.get("k") == "v"
        await asyncio.sleep(1.1)
        assert await c.get("k") is None
        assert c.stats.hits == 1 and c.stats.misses >= 2

    asyncio.run(run())


def test_cache_lru_eviction():
    async def run():
        c = TTLCache(max_items=2, default_ttl=60)
        await c.set("a", "1")
        await c.set("b", "2")
        await c.get("a")  # a 变成最近使用
        await c.set("c", "3")  # 淘汰 b
        assert await c.get("a") == "1"
        assert await c.get("b") is None

    asyncio.run(run())


def test_cache_key_includes_prompt_version():
    assert cache_key("p1", "角度", 0, "v1.3") != cache_key("p1", "角度", 0, "v1.4")


# ---------------- RAG ----------------

def test_retriever_splits_and_retrieves():
    r = Retriever()
    r.load()
    assert len(r.chunks) > 5, "品牌规范应被切成多块"
    hits = r.retrieve("通勤 大衣 面料 压肩", k=3)
    assert hits, "应能召回相关片段"
    joined = "".join(c.text for c in hits)
    assert any(k in joined for k in ["大衣", "通勤", "面料"]), joined[:200]


# ---------------- JSON 修复 ----------------

def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_fence_and_chatter():
    text = '好的，结果如下：\n```json\n{"title": "x", "tags": ["#a"]}\n```\n希望有帮助'
    assert extract_json(text)["title"] == "x"


def test_extract_json_raises_on_garbage():
    with pytest.raises(Exception):
        extract_json("完全不是 JSON")
