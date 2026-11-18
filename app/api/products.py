"""商品接口：最小 CRUD。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import session_scope
from ..models import Product
from ..schemas import ProductIn, ProductOut

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("", response_model=list[ProductOut])
def list_products():
    with session_scope() as db:
        return db.query(Product).order_by(Product.created_at.desc()).all()


@router.post("", response_model=ProductOut)
def create_product(payload: ProductIn):
    with session_scope() as db:
        product = Product(**payload.model_dump())
        db.add(product)
        db.flush()
        db.refresh(product)
        return product


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: str):
    with session_scope() as db:
        product = db.get(Product, product_id)
        if product is None:
            raise HTTPException(404, "商品不存在")
        return product


@router.delete("/{product_id}")
def delete_product(product_id: str):
    with session_scope() as db:
        product = db.get(Product, product_id)
        if product is None:
            raise HTTPException(404, "商品不存在")
        db.delete(product)
        return {"ok": True}
