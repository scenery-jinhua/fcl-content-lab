"""OpenAI 兼容 Provider：一套代码接 DeepSeek / 通义千问 / 硅基流动 / 本地 Ollama。

为什么是「OpenAI 兼容」而不是每家写一个：
现在国内主流模型厂商都提供 /v1/chat/completions 接口，协议一致，换供应商只改 base_url 和 model 名。
真实接入时唯一要额外考虑的是：429 限流要退避、上下文超长要截断、返回可能被内容安全拦截。
"""

from __future__ import annotations

import time

import httpx

from ..config import settings
from .base import LLMProvider, LLMResult, ProviderError, approx_tokens


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(self) -> None:
        if not settings.llm_api_key:
            raise ProviderError("未配置 FCL_LLM_API_KEY，无法使用真实模型", retryable=False)
        self._client = httpx.AsyncClient(
            base_url=settings.llm_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=settings.llm_timeout_s,
        )

    async def chat(self, messages: list[dict], *, temperature: float = 0.7, json_mode: bool = False) -> LLMResult:
        payload = {
            "model": settings.llm_model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            # 让模型尽量只吐 JSON；但别指望它百分百听话，所以 extract_json 的兜底必须留着
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"请求超时：{exc}", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"网络错误：{exc}", retryable=True) from exc

        if resp.status_code == 429:
            raise ProviderError("触发供应商限流 429", retryable=True)
        if resp.status_code >= 500:
            raise ProviderError(f"上游 5xx：{resp.status_code}", retryable=True)
        if resp.status_code >= 400:
            # 400/401/403 属于「重试也没用」的错误，直接抛不可重试，避免白白烧钱
            raise ProviderError(f"请求被拒绝 {resp.status_code}：{resp.text[:200]}", retryable=False)

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            tokens_in=int(usage.get("prompt_tokens") or approx_tokens(str(messages))),
            tokens_out=int(usage.get("completion_tokens") or approx_tokens(text)),
            model=data.get("model") or settings.llm_model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # 只有供应商提供 /embeddings 时才可用；不可用就让 RAG 自动退化
        resp = await self._client.post("/embeddings", json={"model": "text-embedding-3-small", "input": texts})
        if resp.status_code >= 400:
            raise ProviderError("当前供应商未提供 embedding 接口", retryable=False)
        return [item["embedding"] for item in resp.json()["data"]]

    async def aclose(self) -> None:
        await self._client.aclose()
