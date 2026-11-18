# -*- coding: utf-8 -*-
"""接口契约测试：防止「接口少返回一个字段 → 前端整块白掉」这类事故再次发生。

背景：Dashboard 的 Pydantic 响应模型漏声明了 runs / queue / cache_stats，
FastAPI 静默把它们过滤掉，前端读 d.runs.total 抛异常，整个看板渲染中断。
这类 bug 单测函数是测不出来的，必须测「接口返回的字段集合」。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_dashboard_contract_fields(client):
    """看板接口必须返回前端用到的全部字段。"""
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    data = r.json()
    required = {
        "total_contents",
        "first_pass_rate",
        "cache_hit_rate",
        "p95_latency_ms",
        "avg_latency_ms",
        "rule_block_rate",
        "avg_score",
        "total_tokens",
        "estimated_cost_yuan",
        "saved_cost_yuan",
        "by_dim",
        "recent_events",
        "trend",
        "queue",
        "cache_stats",
        "runs",
    }
    missing = required - set(data)
    assert not missing, f"看板接口缺字段：{missing}"


def test_system_contract_fields(client):
    r = client.get("/api/system")
    assert r.status_code == 200
    data = r.json()
    assert {"provider", "model", "rag", "cache", "thresholds"} <= set(data)
    assert {"chunks", "indexed"} <= set(data["rag"])
    assert {"backend", "hit_rate", "items"} <= set(data["cache"])


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_index_serves_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "FCL" in r.text
