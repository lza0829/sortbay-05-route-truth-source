from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, row_to_dict
from app.services.common import audit, current_shift, now_str, require_unsealed
from app.services.routed_truth import adjudicate, replay_all, truth_of, truth_view

router = APIRouter(tags=["过机扫描"])


class ScanIn(BaseModel):
    条码: str = Field(min_length=1)
    目的地码: str = ""
    分拣线编号: int | None = None
    备注: str = ""


@router.get("/扫描")
def list_scans(状态: str | None = None):
    """扫描册：口门以真源重放为准，缓存列仅对照，分叉必标。"""
    with db() as conn:
        truths = replay_all(conn)
        if 状态:
            rows = conn.execute(
                "SELECT * FROM scans WHERE status=? ORDER BY id DESC LIMIT 200", (状态,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 200").fetchall()
        return [truth_view(r, truth_of(truths, r["id"])) for r in rows]


@router.post("/扫描")
def create_scan(body: ScanIn):
    with db() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        line = None
        if body.分拣线编号:
            line = conn.execute("SELECT * FROM lines WHERE id=?", (body.分拣线编号,)).fetchone()
        else:
            line = conn.execute("SELECT * FROM lines WHERE active=1 ORDER BY id LIMIT 1").fetchone()
        if not line:
            raise HTTPException(400, "无可用分拣线")
        if not line["active"]:
            raise HTTPException(400, "分拣线已停用")
        dup = conn.execute(
            "SELECT id FROM scans WHERE barcode=? AND shift_id=?",
            (body.条码, shift["id"]),
        ).fetchone()
        if dup:
            raise HTTPException(400, "本班次条码已过机")
        conn.execute(
            """
            INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, scanned_at, note)
            VALUES (?, ?, ?, ?, '待路由', ?, ?)
            """,
            (body.条码, body.目的地码, shift["id"], line["id"], now_str(), body.备注),
        )
        row = conn.execute(
            "SELECT * FROM scans WHERE barcode=? AND shift_id=?",
            (body.条码, shift["id"]),
        ).fetchone()
        audit(conn, "扫描", row["id"], "过机装载", body.条码)
        return row_to_dict(row)


@router.post("/扫描/{scan_id}/路由")
def route_scan(scan_id: int):
    """路由裁决入口：事件与缓存同源落库，回执同刷扫描册/口门在口/总览分布。"""
    with db() as conn:
        require_unsealed(conn)
        return adjudicate(conn, scan_id)
