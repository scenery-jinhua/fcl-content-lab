"""全局配置：所有开关都有默认值，保证「不配任何东西也能跑起来」。

为什么把配置集中在一个文件？
- 环境相关的东西（密钥、数据库地址、并发数）不写死在业务代码里，换环境只改 .env；
- 面试常问「你密钥怎么管理的」——答案就是：代码里永远不出现明文密钥，只读环境变量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass
class Settings:
    # ---- 服务 ----
    app_name: str = "FCL · Fabrique Content Lab"
    host: str = os.getenv("FCL_HOST", "127.0.0.1")
    port: int = _int("FCL_PORT", 8000)

    # ---- 模型 ----
    # mock: 离线假模型，不花钱、不需要密钥，用来演示全链路
    # openai_compat: 任何 OpenAI 兼容 API（DeepSeek / 通义 / 硅基流动 / 本地 Ollama）
    provider: str = os.getenv("FCL_PROVIDER", "mock")
    llm_base_url: str = os.getenv("FCL_LLM_BASE_URL", "https://api.deepseek.com/v1")
    llm_api_key: str = os.getenv("FCL_LLM_API_KEY", "")
    llm_model: str = os.getenv("FCL_LLM_MODEL", "deepseek-chat")
    llm_timeout_s: float = _float("FCL_LLM_TIMEOUT_S", 45.0)

    # 单价（元 / 百万 token），只用来估算成本，按供应商官网改
    price_in_per_mtok: float = _float("FCL_PRICE_IN", 1.0)
    price_out_per_mtok: float = _float("FCL_PRICE_OUT", 2.0)

    # ---- 队列 ----
    worker_concurrency: int = _int("FCL_WORKER_CONCURRENCY", 4)
    task_max_retries: int = _int("FCL_TASK_MAX_RETRIES", 3)
    task_timeout_s: float = _float("FCL_TASK_TIMEOUT_S", 60.0)
    retry_base_delay_s: float = _float("FCL_RETRY_BASE_DELAY_S", 1.0)

    # ---- 限流 ----
    rate_limit_per_min: int = _int("FCL_RATE_LIMIT_PER_MIN", 120)
    rate_limit_burst: int = _int("FCL_RATE_LIMIT_BURST", 20)

    # ---- 缓存 ----
    cache_ttl_s: int = _int("FCL_CACHE_TTL_S", 3600)
    cache_max_items: int = _int("FCL_CACHE_MAX_ITEMS", 2000)
    cache_similarity: float = _float("FCL_CACHE_SIMILARITY", 0.92)

    # ---- RAG ----
    rag_top_k: int = _int("FCL_RAG_TOP_K", 3)
    rag_chunk_size: int = _int("FCL_RAG_CHUNK_SIZE", 220)

    # ---- 质检 ----
    pass_threshold: float = _float("FCL_PASS_THRESHOLD", 78.0)
    max_rewrite_rounds: int = _int("FCL_MAX_REWRITE_ROUNDS", 1)

    # ---- 存储 ----
    db_url: str = os.getenv("FCL_DB_URL", "sqlite:///./data/fcl.db")
    redis_url: str = os.getenv("FCL_REDIS_URL", "")  # 为空 = 只用进程内内存实现

    # ---- 路径 ----
    base_dir: str = field(default_factory=lambda: os.path.dirname(os.path.abspath(__file__)))
    project_dir: str = field(default_factory=lambda: os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    @property
    def guidelines_path(self) -> str:
        return os.path.join(self.project_dir, "data", "brand_guidelines.md")

    @property
    def static_dir(self) -> str:
        return os.path.join(self.base_dir, "static")

    @property
    def use_real_llm(self) -> bool:
        return self.provider != "mock" and bool(self.llm_api_key)


settings = Settings()
