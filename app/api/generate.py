"""生成任务接口：提交、查进度、看内容、人工审核。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import session_scope
from ..models import Content, ContentStatus, Event, Product, Run, RunStatus
from ..schemas import ContentOut, ReviewIn, RunDetail, RunIn, RunOut
from ..services.queue import Job, pool

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("", response_model=RunOut, status_code=202)
async def create_run(payload: RunIn):
    """接单：只创建任务并入队，立刻返回。真正的生成在后台 worker 里跑。"""
    with session_scope() as db:
        product = db.get(Product, payload.product_id)
        if product is None:
            raise HTTPException(404, "商品不存在")
        run = Run(
            product_id=product.id,
            angle=payload.angle,
            variants=payload.variants,
            total=payload.variants,
            status=RunStatus.PENDING,
        )
        db.add(run)
        db.flush()
        run_id = run.id
        db.add(Event(type="run_created", payload={"run_id": run_id, "variants": payload.variants}))

    for i in range(payload.variants):
        await pool.enqueue(Job(run_id, payload.product_id, payload.angle, i))

    with session_scope() as db:
        run = db.get(Run, run_id)
        run.status = RunStatus.RUNNING
        db.flush()
        return run


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str):
    with session_scope() as db:
        run = db.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "任务不存在")
        product = db.get(Product, run.product_id)
        contents = (
            db.query(Content)
            .filter(Content.run_id == run_id)
            .order_by(Content.variant_index.asc())
            .all()
        )
        return RunDetail(
            run=RunOut.model_validate(run),
            product=product,  # type: ignore[arg-type]  Pydantic 会按 from_attributes 转换
            contents=[ContentOut.model_validate(c) for c in contents],
        )


@router.post("/contents/{content_id}/review", response_model=ContentOut)
def review(content_id: str, payload: ReviewIn):
    """人工终审：通过 / 驳回。这是三层质检的最后一道，也是「人不能被 AI 取代」的地方。"""
    with session_scope() as db:
        content = db.get(Content, content_id)
        if content is None:
            raise HTTPException(404, "内容不存在")
        content.status = ContentStatus.APPROVED if payload.action == "approve" else ContentStatus.REJECTED
        db.add(Event(type="review", payload={"content_id": content_id, "action": payload.action}))
        db.flush()
        return content


@router.get("", response_model=list[RunOut])
def list_runs(limit: int = 20):
    with session_scope() as db:
        return db.query(Run).order_by(Run.created_at.desc()).limit(min(limit, 100)).all()
