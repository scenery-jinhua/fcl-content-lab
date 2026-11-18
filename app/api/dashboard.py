"""看板接口：聚合指标 + 系统运行状态。"""

from __future__ import annotations

from fastapi import APIRouter

from ..db import session_scope
from ..schemas import Dashboard
from ..services.metrics import dashboard as build_dashboard
from ..services.rag import retriever

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard", response_model=Dashboard)
def dashboard():
    with session_scope() as db:
        return build_dashboard(db)


@router.get("/system")
def system():
    """系统自检：模型供应商、队列、限流、RAG 索引状态。面试时可以现场打开给人看。"""
    from ..config import settings
    from ..providers import get_provider
    from ..services.pipeline import cache

    provider = get_provider()
    return {
        "provider": provider.name,
        "model": settings.llm_model if provider.name != "mock" else "mock-writer-v1",
        "real_llm": settings.use_real_llm,
        "prompt_version": __import__("app.services.prompts", fromlist=["PROMPT_VERSION"]).PROMPT_VERSION,
        "rag": retriever.stats(),
        "cache": {
            "backend": type(cache).__name__,
            "items": len(cache),
            **cache.stats.as_dict(),
        },
        "thresholds": {"pass": settings.pass_threshold, "max_rewrite": settings.max_rewrite_rounds},
    }
