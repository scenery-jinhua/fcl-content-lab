"""接口契约（Pydantic）。

为什么要单独一层 schema：
- 请求进来先校验，脏数据在门口就被挡住，不会流到数据库；
- 响应只暴露该暴露的字段（比如绝不把 api_key 带出去）；
- /docs 自动生成接口文档，前端不用再问「这个字段叫啥」。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ProductIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category: str = "女装"
    fabric: str = ""
    highlights: str = Field(default="", max_length=2000)
    price_band: str = ""
    image_path: str = ""


class ProductOut(ProductIn):
    id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RunIn(BaseModel):
    product_id: str
    angle: Literal["通勤场景", "约会场景", "松弛感", "旅行度假", "职场面试"] = "通勤场景"
    variants: int = Field(default=3, ge=1, le=20)

    @field_validator("variants")
    @classmethod
    def _cap(cls, v: int) -> int:
        # 限流的第一道防线：单次任务别让用户一次点 500 条把额度烧光
        return min(v, 20)


class RunOut(BaseModel):
    id: str
    product_id: str
    angle: str
    variants: int
    status: str
    total: int
    done: int
    failed: int
    created_at: datetime
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class ContentOut(BaseModel):
    id: str
    run_id: str
    variant_index: int
    title: str
    body: str
    tags: list
    image_prompt: str
    status: str
    score_total: float
    score_detail: dict
    violations: list
    judge_verdict: str
    rewrite_rounds: int
    tokens_in: int
    tokens_out: int
    cost_yuan: float
    latency_ms: int
    cached: bool
    error: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewIn(BaseModel):
    action: Literal["approve", "reject"]


class RunDetail(BaseModel):
    run: RunOut
    product: ProductOut
    contents: list[ContentOut]


class Dashboard(BaseModel):
    total_contents: int
    succeeded: int
    success_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    cache_hit_rate: float
    total_tokens: int
    estimated_cost_yuan: float
    saved_cost_yuan: float
    rule_block_rate: float
    first_pass_rate: float
    avg_score: float
    by_dim: dict[str, float]
    recent_events: list[dict]
    trend: list[dict]
