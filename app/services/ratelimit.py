"""限流：令牌桶算法。

为什么不用「固定窗口计数」：
固定窗口在窗口切换的瞬间会放过 2 倍流量（比如 60 秒限 60 次，59 秒来 60 次、
下一秒又来 60 次，瞬间 120 次），令牌桶可以把突发流量平滑掉。

令牌桶原理：
- 桶里最多装 burst 个令牌，按 rate 个/秒匀速补充；
- 每来一个请求取 1 个令牌，取不到就等（或直接拒绝）。
好处：允许一定突发（burst），但长期速率被 rate 卡死，正好对应「模型 API 的 QPS 限制」。
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate_per_min: int = 120, burst: int = 20):
        self.rate_per_s = rate_per_min / 60.0
        self.capacity = max(1, burst)
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()
        self.total_waited_s = 0.0
        self.total_throttled = 0

    async def acquire(self, tokens: int = 1, timeout: float = 30.0) -> bool:
        """取令牌。取不到就睡到攒够为止；等待超过 timeout 则拒绝（保护上游）。"""
        deadline = time.monotonic() + timeout
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_s)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                deficit = tokens - self._tokens
                wait = deficit / self.rate_per_s
            if time.monotonic() + wait > deadline:
                self.total_throttled += 1
                return False
            self.total_waited_s += wait
            await asyncio.sleep(min(wait, 1.0))

    def snapshot(self) -> dict:
        return {
            "rate_per_min": round(self.rate_per_s * 60, 1),
            "capacity": self.capacity,
            "tokens": round(self._tokens, 2),
            "waited_s": round(self.total_waited_s, 2),
            "throttled": self.total_throttled,
        }
