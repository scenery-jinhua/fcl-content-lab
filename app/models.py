"""数据模型：五张表，覆盖「商品 → 任务 → 内容 → 评测 → 埋点」全链路。

设计原则：
1. 每一行都可以被追溯（谁、什么时候、花了多少 token、为什么被驳回）；
2. 会变的字段不塞进主表（评测明细单独一张表，方便一个内容多个评委、多轮打分）；
3. 状态用字符串常量而不是魔法数字，日志里一眼能看懂。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ContentStatus:
    DRAFT = "draft"          # 生成完，没被审核
    NEEDS_REWORK = "rework"  # 打分没过 / 命中硬规则
    APPROVED = "approved"    # 运营通过
    REJECTED = "rejected"    # 运营驳回


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(60), default="女装")
    fabric: Mapped[str] = mapped_column(String(120), default="")
    highlights: Mapped[str] = mapped_column(Text, default="")
    price_band: Mapped[str] = mapped_column(String(40), default="")
    image_path: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    runs: Mapped[list["Run"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class Run(Base):
    """一次批量生成任务：一个商品 × 一个内容角度 × N 个变体。"""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    angle: Mapped[str] = mapped_column(String(40), default="通勤场景")
    variants: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.PENDING, index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    product: Mapped[Product] = relationship(back_populates="runs")
    contents: Mapped[list["Content"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Content(Base):
    """一条生成出来的小红书笔记草稿。"""

    __tablename__ = "contents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    variant_index: Mapped[int] = mapped_column(Integer, default=0)

    title: Mapped[str] = mapped_column(String(120), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    image_prompt: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(20), default=ContentStatus.DRAFT)
    score_total: Mapped[float] = mapped_column(Float, default=0.0)
    score_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    violations: Mapped[list] = mapped_column(JSON, default=list)
    judge_verdict: Mapped[str] = mapped_column(String(20), default="")
    rewrite_rounds: Mapped[int] = mapped_column(Integer, default=0)

    prompt_version: Mapped[str] = mapped_column(String(20), default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_yuan: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    run: Mapped[Run] = relationship(back_populates="contents")
    evals: Mapped[list["EvalRecord"]] = relationship(back_populates="content", cascade="all, delete-orphan")


class EvalRecord(Base):
    """评测明细：哪一维、多少分、为什么。分开存是为了可追溯、可审计、可做人工一致性校验。"""

    __tablename__ = "eval_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    content_id: Mapped[str] = mapped_column(ForeignKey("contents.id"), index=True)
    dim: Mapped[str] = mapped_column(String(40))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    judge_model: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    content: Mapped[Content] = relationship(back_populates="evals")


class Event(Base):
    """埋点表：失败、重试、限流、缓存命中…… 出问题先查它。"""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


Index("ix_contents_run_cached", Content.run_id, Content.cached)
