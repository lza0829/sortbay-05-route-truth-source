from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, rows_to_list, row_to_dict
from app.services.common import audit, current_shift, now_str, require_unsealed
from app.services.routed_truth import ACTION_DIVERT, append_route_event, replay_scan

router = APIRouter(tags=["错分回流"])


class DivertIn(BaseModel):
    扫描编号: int
    备注: str = ""


@router.get("/回流批次")
def list_batches():
    with db() as conn:
        return rows_to_list(conn.execute(
            "SELECT * FROM divert_batches ORDER BY id DESC LIMIT 100"
        ).fetchall())


@router.post("/回流批次")
def create_divert(body: DivertIn):
    """错分回流：与裁决同一写口纪律——事件与缓存同事务，缓存同步清空。"""
    with db() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        truth = replay_scan(conn, body.扫描编号)
        if not truth["已路由"] and scan["status"] != "已入袋":
            raise HTTPException(400, "仅真源已路由或已入袋可标错分回流")
        chute_id = truth["口门"] or scan["chute_id"]
        if chute_id is None:
            raise HTTPException(400, "无口门真源，禁止回流")
        code = f"回流-{shift['id']}-{now_str().replace(' ', '').replace(':', '')[-8:]}"
        conn.execute(
            "INSERT INTO divert_batches(shift_id, code, status, created_at, note) VALUES (?, ?, '回流中', ?, ?)",
            (shift["id"], code, now_str(), body.备注),
        )
        bid = conn.execute("SELECT id FROM divert_batches WHERE code=?", (code,)).fetchone()["id"]
        eid = append_route_event(conn, body.扫描编号, chute_id, ACTION_DIVERT, code)
        conn.execute(
            "UPDATE scans SET status='错分回流', divert_batch_id=?, bag_id=NULL, "
            "chute_id=NULL, routed_event_id=? WHERE id=?",
            (bid, eid, body.扫描编号),
        )
        audit(conn, "回流", bid, "建回流批次", str(body.扫描编号))
        return {
            "批次": row_to_dict(conn.execute("SELECT * FROM divert_batches WHERE id=?", (bid,)).fetchone()),
            "扫描": row_to_dict(conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()),
        }
