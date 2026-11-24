from __future__ import annotations
from fastapi import APIRouter
from app.db import db, row_to_dict
from app.services.routed_truth import in_gate_counts, overview_buckets

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    """总览：已路由桶与口门在口件数均按真源重放计，假列不得亮已对齐。"""
    with db() as conn:
        shift = conn.execute(
            "SELECT * FROM shifts ORDER BY id DESC LIMIT 1"
        ).fetchone()
        buckets = overview_buckets(conn)
        counts = in_gate_counts(conn)
        chutes = []
        for r in conn.execute("SELECT * FROM chutes ORDER BY code").fetchall():
            d = dict(r)
            d.update(counts.get(r["id"], {"在口件数": 0, "缓存件数": 0, "分叉": False}))
            chutes.append(d)
        return {
            "班次": row_to_dict(shift),
            "分布": buckets["分布"],
            "分叉数": buckets["分叉数"],
            "口门": chutes,
        }
