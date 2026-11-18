"""缓存：进程内 TTL + LRU，可选接 Redis。

缓存解决的是什么：同样的输入反复调模型，既是浪费钱，也是浪费延迟。
本项目的缓存 key 是 (商品, 角度, 变体序号, 提示词版本) 的哈希——
只要提示词版本变了，key 就变了，旧缓存自动失效，不会拿旧结果骗人。

三个必须知道的概念（面试高频）：
- 缓存击穿：某个热点 key 失效瞬间，大量请求同时打到后端 → 用「单飞 / 互斥锁」只放一个请求去重建；
- 缓存穿透：查一个根本不存在的 key，每次都穿透到后端 → 缓存空值 / 布隆过滤器；
- 缓存雪崩：大批 key 同一时刻集体失效 → TTL 加随机抖动。
本项目用 TTL 抖动 + 单飞锁处理了击穿和雪崩，穿透场景不突出（key 来自有界输入），但要能讲清楚。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    sets: int = 0
    coalesced: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else 0.0

    def as_dict(self) -> dict:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "sets": self.sets,
            "coalesced": self.coalesced,
            "hit_rate": self.hit_rate,
        }


@dataclass
class _Entry:
    value: str
    expire_at: float


class TTLCache:
    """线程/协程安全的最小实现：OrderedDict 做 LRU，时间戳做 TTL。"""

    def __init__(self, max_items: int = 2000, default_ttl: int = 3600, jitter: float = 0.1):
        self.max_items = max_items
        self.default_ttl = default_ttl
        self.jitter = jitter
        self._data: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()
        self.stats = CacheStats()

    async def get(self, key: str) -> str | None:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.stats.misses += 1
                return None
            if entry.expire_at < time.time():
                del self._data[key]
                self.stats.misses += 1
                return None
            self._data.move_to_end(key)  # LRU：命中就挪到队尾
            self.stats.hits += 1
            return entry.value

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        async with self._lock:
            ttl = ttl or self.default_ttl
            # TTL 抖动：避免大批 key 同时过期造成雪崩
            expire = time.time() + ttl * (1 + random.uniform(-self.jitter, self.jitter))
            self._data[key] = _Entry(value, expire)
            self._data.move_to_end(key)
            self.stats.sets += 1
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)  # 淘汰最久未使用的
                self.stats.evictions += 1

    def __len__(self) -> int:
        return len(self._data)


class SingleFlight:
    """单飞：同一个 key 同时只有一个请求去重建，其余等它的结果。

    这就是缓存击穿的标准解法。没有它，100 个并发生成同一个商品会同时打 100 次模型。
    """

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Future] = {}

    async def do(self, key: str, coro_factory):
        existing = self._inflight.get(key)
        if existing is not None:
            return await existing
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            result = await coro_factory()
            if not fut.done():
                fut.set_result(result)
            return result
        except Exception as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)


class RedisCache:
    """生产用 Redis 实现。没装 redis 包或没配 URL 时自动回落到内存实现。"""

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis  # 延迟导入：没装也不影响离线演示

        self._client = aioredis.from_url(url, decode_responses=True)
        self.stats = CacheStats()

    async def get(self, key: str) -> str | None:
        value = await self._client.get(key)
        if value is None:
            self.stats.misses += 1
        else:
            self.stats.hits += 1
        return value

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        ttl = ttl or settings_ttl()
        await self._client.set(key, value, ex=ttl)
        self.stats.sets += 1


def settings_ttl() -> int:
    from ..config import settings

    return settings.cache_ttl_s


def cache_key(*parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return "fcl:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_cache():
    from ..config import settings

    if settings.redis_url:
        try:
            return RedisCache(settings.redis_url)
        except Exception:  # noqa: BLE001 - Redis 连不上就降级，服务不能倒
            pass
    return TTLCache(max_items=settings.cache_max_items, default_ttl=settings.cache_ttl_s)
