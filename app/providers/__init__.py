"""Provider 工厂：业务代码只依赖抽象，不依赖具体实现。"""

from __future__ import annotations

from .base import LLMProvider, ProviderError
from .mock import MockProvider
from .openai_compat import OpenAICompatProvider

_instance: LLMProvider | None = None


def get_provider() -> LLMProvider:
    global _instance
    if _instance is not None:
        return _instance
    from ..config import settings

    if settings.provider == "mock":
        _instance = MockProvider()
    else:
        try:
            _instance = OpenAICompatProvider()
        except ProviderError:
            # 配了真实模型但没给 key：优雅降级到 mock，服务照样能起
            _instance = MockProvider()
    return _instance


def set_provider(provider: LLMProvider) -> None:
    """测试用：注入假 provider。"""
    global _instance
    _instance = provider
