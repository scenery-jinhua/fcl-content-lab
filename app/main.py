"""FastAPI 入口：装配路由、生命周期、静态页面。

启动顺序很关键：
1. 建表（幂等）
2. 加载品牌规范 + 建向量索引（失败就退化为关键词检索）
3. 起 worker 池（会顺带恢复上次没跑完的任务）
4. 关闭时优雅停 worker

「启动时做重活、跑起来就快」是常见做法；生产上索引构建一般拆成独立任务，避免拖慢启动。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import dashboard, generate, products
from .config import settings
from .db import init_db
from .providers import get_provider
from .services.queue import pool
from .services.rag import retriever


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    retriever.load()
    await retriever.build_vectors(get_provider())
    await pool.start()
    print(f"[FCL] 启动完成 provider={get_provider().name} chunks={retriever.stats()['chunks']} workers={settings.worker_concurrency}")
    yield
    await pool.stop()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="设计师品牌 AIGC 内容生产中台：批量生成 + 规则质检 + 四维打分 + 人工审核 + 成本看板",
    lifespan=lifespan,
)

# 本地开发放开 CORS：前端如果单独用 Vite 跑在 5173，不加这个会被浏览器拦
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(products.router)
app.include_router(generate.router)
app.include_router(dashboard.router)

if os.path.isdir(settings.static_dir):
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(settings.static_dir, "index.html"))


@app.get("/health", tags=["system"])
def health():
    from .services.prompts import PROMPT_VERSION

    return {"ok": True, "provider": get_provider().name, "prompt_version": PROMPT_VERSION}
