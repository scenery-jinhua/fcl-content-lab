"""异步任务队列：asyncio.Queue + 固定数量 worker。

为什么不能「用户一点生成，就同步 for 循环调 100 次模型」：
1. HTTP 请求会超时（网关一般 30-60s），100 条内容根本等不完；
2. 串行执行太慢，并发又不加限制会把模型供应商打挂、触发 429；
3. 中途失败没有重试、没有断点续跑，前面的钱全白花。

队列把「接单」和「干活」拆开：
- API 只负责创建任务、入队，立刻返回 run_id（用户拿到进度条）；
- worker 按并发上限慢慢消费，失败重试，进度实时落库。

生产上会换成 Celery/RQ + Redis：好处是能多机横向扩、任务持久化不丢。
本项目用 asyncio 队列，是想用最小代码把「队列到底解决什么问题」讲清楚。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from ..config import settings
from ..db import session_scope
from ..models import Content, ContentStatus, Event, Product, Run, RunStatus
from ..providers.base import ProviderError, call_with_retry
from .pipeline import generate_one, persist_result
from .ratelimit import TokenBucket


@dataclass
class Job:
    run_id: str
    product_id: str
    angle: str
    variant: int


class WorkerPool:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=10_000)
        self.bucket = TokenBucket(rate_per_min=settings.rate_limit_per_min, burst=settings.rate_limit_burst)
        self._workers: list[asyncio.Task] = []
        self._running = False
        self.processed = 0
        self.failed = 0
        self.retried = 0
        self.active = 0

    # ---------------- 生命周期 ----------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._workers = [asyncio.create_task(self._worker(i), name=f"fcl-worker-{i}") for i in range(settings.worker_concurrency)]
        await self._recover_pending()

    async def stop(self) -> None:
        self._running = False
        for t in self._workers:
            t.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def _recover_pending(self) -> None:
        """重启恢复：把进程挂掉时没跑完的 run 重新入队。

        真实队列（Celery/RQ）自带持久化和重投；asyncio 队列在内存里，进程一挂就没了。
        所以这里用数据库状态做兜底补偿——这是「消息丢失」最朴素的解法，面试可以直接讲。
        """
        with session_scope() as db:
            runs = db.query(Run).filter(Run.status.in_([RunStatus.PENDING, RunStatus.RUNNING])).all()
            for run in runs:
                done = db.query(Content).filter(Content.run_id == run.id, Content.error == "").count()
                for i in range(done, run.variants):
                    await self.enqueue(Job(run.id, run.product_id, run.angle, i))
                run.status = RunStatus.RUNNING
                db.add(Event(type="recover", payload={"run_id": run.id, "requeued": run.variants - done}))

    # ---------------- 入队 ----------------

    async def enqueue(self, job: Job) -> None:
        await self.queue.put(job)

    # ---------------- 消费 ----------------

    async def _worker(self, worker_id: int) -> None:
        while self._running:
            job = await self.queue.get()
            self.active += 1
            try:
                await self._handle(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - worker 绝不能因为单条任务挂掉
                self.failed += 1
                self._mark_failed(job, f"{type(exc).__name__}: {exc}")
            finally:
                self.active -= 1
                self.queue.task_done()

    async def _handle(self, job: Job) -> None:
        # 幂等：已经成功的不重复跑（队列可能重投，别让用户多花钱）
        with session_scope() as db:
            existing = (
                db.query(Content)
                .filter(Content.run_id == job.run_id, Content.variant_index == job.variant, Content.error == "")
                .first()
            )
            if existing is not None:
                return
            product_row = db.get(Product, job.product_id)
            if product_row is None:
                return
            product = {
                "id": product_row.id,
                "name": product_row.name,
                "category": product_row.category,
                "fabric": product_row.fabric,
                "highlights": product_row.highlights,
                "price_band": product_row.price_band,
            }
            content = (
                db.query(Content).filter(Content.run_id == job.run_id, Content.variant_index == job.variant).first()
            )
            if content is None:
                content = Content(run_id=job.run_id, variant_index=job.variant)
                db.add(content)
                db.flush()
            content_id = content.id

        # 限流：取不到令牌就等，等太久直接判失败（保护上游）
        if not await self.bucket.acquire(timeout=30):
            raise ProviderError("限流等待超时，任务被拒绝", retryable=True)

        started = time.perf_counter()
        result = await call_with_retry(
            lambda: generate_one(product, job.angle, job.variant),
            max_retries=settings.task_max_retries,
            base_delay=settings.retry_base_delay_s,
            timeout=settings.task_timeout_s,
            on_retry=lambda n, delay, err: self._on_retry(job, n, delay, err),
        )
        latency = int((time.perf_counter() - started) * 1000)

        with session_scope() as db:
            content = db.get(Content, content_id)
            if content is None:
                return
            persist_result(db, content, product, job.angle, job.variant, result)
            content.latency_ms = latency
            run = db.get(Run, job.run_id)
            if run is not None:
                run.done = db.query(Content).filter(Content.run_id == run.id, Content.error == "").count()
                if run.done + run.failed >= run.total:
                    run.status = RunStatus.SUCCEEDED if run.failed == 0 else RunStatus.FAILED
                    from datetime import datetime, timezone

                    run.finished_at = datetime.now(timezone.utc)
            db.add(Event(type="generated", payload={"content_id": content.id, "cached": result.cached}))
        self.processed += 1

    def _on_retry(self, job: Job, attempt: int, delay: float, err: Exception | None) -> None:
        self.retried += 1
        with session_scope() as db:
            db.add(
                Event(
                    type="retry",
                    payload={
                        "run_id": job.run_id,
                        "variant": job.variant,
                        "attempt": attempt,
                        "delay_s": round(delay, 2),
                        "error": str(err)[:200] if err else "",
                    },
                )
            )

    def _mark_failed(self, job: Job, error: str) -> None:
        try:
            with session_scope() as db:
                content = (
                    db.query(Content)
                    .filter(Content.run_id == job.run_id, Content.variant_index == job.variant)
                    .first()
                )
                if content is None:
                    content = Content(run_id=job.run_id, variant_index=job.variant)
                    db.add(content)
                content.error = error[:500]
                content.status = ContentStatus.REJECTED
                run = db.get(Run, job.run_id)
                if run is not None:
                    run.failed += 1
                    run.done = db.query(Content).filter(Content.run_id == run.id, Content.error == "").count()
                    if run.done + run.failed >= run.total:
                        run.status = RunStatus.FAILED
                db.add(Event(type="task_failed", payload={"run_id": job.run_id, "variant": job.variant, "error": error[:200]}))
        except Exception:  # noqa: BLE001
            pass

    def snapshot(self) -> dict:
        return {
            "queued": self.queue.qsize(),
            "active": self.active,
            "processed": self.processed,
            "failed": self.failed,
            "retried": self.retried,
            "concurrency": settings.worker_concurrency,
            "rate_limiter": self.bucket.snapshot(),
        }

    @property
    def running(self) -> bool:
        return self._running


pool = WorkerPool()
