from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, rows_to_list, row_to_dict
from app.services.common import audit, now_str
from app.services.routed_truth import (
    effective_status,
    overview_buckets,
    replay_all,
    truth_of,
)

router = APIRouter(tags=["班次"])


class ShiftIn(BaseModel):
    营业日: str = Field(min_length=8)
    班次名: str = Field(min_length=1)


@router.get("/班次")
def list_shifts():
    with db() as conn:
        return rows_to_list(conn.execute("SELECT * FROM shifts ORDER BY id DESC").fetchall())


@router.get("/班次/对账")
def reconcile(shift_id: int | None = None):
    """班次对账：已路由及以上按真源重放计，分叉单列，禁止假列假齐。"""
    with db() as conn:
        if shift_id is None:
            shift = conn.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT 1").fetchone()
        else:
            shift = conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        if not shift:
            raise HTTPException(404, "班次不存在")
        sid = shift["id"]
        scan_n = conn.execute("SELECT COUNT(*) c FROM scans WHERE shift_id=?", (sid,)).fetchone()["c"]
        buckets = overview_buckets(conn, shift_id=sid)
        bag_n = conn.execute("SELECT COUNT(*) c FROM bags WHERE shift_id=?", (sid,)).fetchone()["c"]
        sealed_bag = conn.execute(
            "SELECT COUNT(*) c FROM bags WHERE shift_id=? AND status='已封袋'", (sid,)
        ).fetchone()["c"]
        divert_n = conn.execute(
            "SELECT COUNT(*) c FROM divert_batches WHERE shift_id=?", (sid,)
        ).fetchone()["c"]
        分布 = buckets["分布"]
        return {
            "班次": row_to_dict(shift),
            "扫描总数": scan_n,
            "已路由及以上": 分布["已路由"] + 分布["已入袋"] + 分布["已封袋"],
            "袋数": bag_n,
            "已封袋数": sealed_bag,
            "回流批次数": divert_n,
            "未清件数": 分布["待路由"] + 分布["错分回流"] + 分布["已路由"] + 分布["已入袋"],
            "真源分布": 分布,
            "分叉数": buckets["分叉数"],
        }


@router.post("/班次")
def create_shift(body: ShiftIn):
    with db() as conn:
        open_one = conn.execute("SELECT id FROM shifts WHERE sealed=0").fetchone()
        if open_one:
            raise HTTPException(400, "仍有未封班班次，先封班再开新班")
        try:
            conn.execute(
                "INSERT INTO shifts(business_day, name, sealed) VALUES (?, ?, 0)",
                (body.营业日, body.班次名),
            )
        except Exception as e:
            raise HTTPException(400, f"开班失败: {e}")
        row = conn.execute(
            "SELECT * FROM shifts WHERE business_day=? AND name=?",
            (body.营业日, body.班次名),
        ).fetchone()
        audit(conn, "班次", row["id"], "开班", body.班次名)
        return row_to_dict(row)


@router.post("/班次/{shift_id}/封班")
def seal_shift(shift_id: int):
    """封班：未清件按真源有效状态计——假列（事件缺失）按待路由拦下，禁止假齐。"""
    with db() as conn:
        shift = conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        if not shift:
            raise HTTPException(404, "班次不存在")
        if shift["sealed"]:
            raise HTTPException(400, "已封班")
        truths = replay_all(conn)
        pending = 0
        for s in conn.execute("SELECT * FROM scans WHERE shift_id=?", (shift_id,)).fetchall():
            if effective_status(s, truth_of(truths, s["id"])) in ("待路由", "错分回流", "已路由", "已入袋"):
                pending += 1
        if pending:
            raise HTTPException(400, f"仍有未封袋/未路由件 {pending}，禁止封班")
        conn.execute(
            "UPDATE shifts SET sealed=1, sealed_at=? WHERE id=?",
            (now_str(), shift_id),
        )
        audit(conn, "班次", shift_id, "封班", "")
        return row_to_dict(conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone())
