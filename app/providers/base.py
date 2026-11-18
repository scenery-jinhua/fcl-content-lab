"""LLM Provider 抽象层。

为什么要有这层抽象？
- 今天用 mock 离线演示，明天换成 DeepSeek / 通义 / 本地 Ollama，业务代码不用改；
- 超时、重试、熔断、token 记账这些「横切关注点」在这里统一做，不要在业务里到处写 try/except；
- 测试时可以注入假 provider，不产生真实费用——这是工程上「可测试性」的基础。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    """模型调用失败。retryable 决定上层要不要重试。"""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class LLMResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    latency_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


class LLMProvider:
    name = "base"

    async def chat(self, messages: list[dict], *, temperature: float = 0.7, json_mode: bool = False) -> LLMResult:
        raise NotImplementedError

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """默认不支持向量化；不支持时 RAG 自动退化为关键词检索。"""
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class CircuitBreaker:
    """熔断器：连续失败到阈值就「跳闸」，冷却期内直接快速失败。

    为什么需要：模型厂商挂了的时候，如果每个请求还傻等 45 秒超时，
    几百个任务会把 worker 全部占满（雪崩）。跳闸后立刻失败、稍后重试，系统才活得下来。
    """

    def __init__(self, failure_threshold: int = 5, cooldown_s: float = 30.0):
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._failures = 0
        self._opened_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
        if self._opened_at == 0:
            return False
        if time.monotonic() - self._opened_at > self.cooldown_s:
            # 冷却结束，进入半开：放一个请求过去试探
            self._opened_at = 0.0
            self._failures = 0
            return False
        return True

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._opened_at = 0.0

    async def record_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()


async def call_with_retry(
    fn,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    timeout: float = 60.0,
    breaker: CircuitBreaker | None = None,
    on_retry=None,
):
    """带超时、指数退避重试、熔断的通用调用包装。

    指数退避 = 第 1 次失败等 1s，第 2 次等 2s，第 3 次等 4s。
    为什么不是固定间隔：下游可能是短时抖动，退避给系统恢复时间；
    为什么加随机抖动：避免几百个任务同一时刻一起重试，再次把下游打挂。
    """
    import random

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        if breaker and breaker.is_open:
            raise ProviderError("provider 熔断中，快速失败", retryable=True)
        try:
            result = await asyncio.wait_for(fn(), timeout=timeout)
            if breaker:
                await breaker.record_success()
            return result
        except asyncio.TimeoutError as exc:
            last_error = ProviderError(f"调用超时（>{timeout}s）", retryable=True)
        except ProviderError as exc:
            last_error = exc
            if not exc.retryable:
                raise
        except Exception as exc:  # noqa: BLE001 - 网络/解析等未知错误统一视为可重试
            last_error = ProviderError(f"{type(exc).__name__}: {exc}", retryable=True)

        if breaker:
            await breaker.record_failure()
        if attempt >= max_retries:
            break
        delay = base_delay * (2**attempt) + random.uniform(0, 0.3)
        if on_retry:
            on_retry(attempt + 1, delay, last_error)
        await asyncio.sleep(delay)
    raise last_error or ProviderError("未知调用失败")


def extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON。

    真实模型很爱干两件事：外面包 ```json 代码块；前后加一句「好的，结果如下」。
    所以这里做三层兜底：直接 parse → 去代码围栏 → 暴力取第一个 { 到最后一个 }。
    这就是所谓的「结构化输出自修复」，不修的话线上会有 5%-15% 的解析失败率。
    """
    text = (text or "").strip()
    if not text:
        raise ProviderError("模型返回为空", retryable=True)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    cleaned = text
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                cleaned = candidate
                break
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ProviderError(f"JSON 解析失败：{text[:120]}", retryable=True)


def approx_tokens(text: str) -> int:
    """粗略估 token：中文约 1 字 ≈ 0.6-0.7 token，这里按 0.6 估，够用来做成本趋势。"""
    return max(1, int(len(text) * 0.6))
