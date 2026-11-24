from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from app.db import db, row_to_dict
from app.services.common import audit, require_unsealed
from app.services.routed_truth import calibrate_all, calibrate_scan, in_gate_counts

router = APIRouter(tags=["口门"])


class ChuteIn(BaseModel):
    分拣线编号: int
    口门码: str = Field(min_length=1)
    标题: str = ""
    软容量: int = 120


@router.get("/口门")
def list_chutes():
    """口门页：在口件数以真源重放为准，缓存件数仅对照，分叉必标。"""
    with db() as conn:
        counts = in_gate_counts(conn)
        rows = conn.execute(
            "SELECT c.*, l.name AS 分拣线名 FROM chutes c JOIN lines l ON l.id=c.line_id ORDER BY c.code"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d.update(counts.get(r["id"], {"在口件数": 0, "缓存件数": 0, "分叉": False}))
            out.append(d)
        return out


@router.post("/口门")
def create_chute(body: ChuteIn):
    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO chutes(line_id, code, title, status, soft_cap) VALUES (?, ?, ?, '口门开', ?)",
                (body.分拣线编号, body.口门码, body.标题, body.软容量),
            )
        except Exception as e:
            raise HTTPException(400, f"创建失败: {e}")
        row = conn.execute(
            "SELECT * FROM chutes WHERE line_id=? AND code=?", (body.分拣线编号, body.口门码)
        ).fetchone()
        audit(conn, "口门", row["id"], "新建", body.口门码)
        return row_to_dict(row)


@router.post("/口门/{chute_id}/开闭")
def set_chute(chute_id: int, 状态: str = Query(...)):
    if 状态 not in ("口门开", "口门闭"):
        raise HTTPException(400, "状态仅允许口门开或口门闭")
    with db() as conn:
        cur = conn.execute("UPDATE chutes SET status=? WHERE id=?", (状态, chute_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "口门不存在")
        audit(conn, "口门", chute_id, "开闭", 状态)
        return row_to_dict(conn.execute("SELECT * FROM chutes WHERE id=?", (chute_id,)).fetchone())


@router.post("/口门/校准")
def calibrate(扫描编号: int | None = None):
    """真源校准写口：把缓存口门列整笔回滚到事件重放结果。

    旁路改列被覆盖、半截分叉被修复，每笔修复留审计；绝不反向改事件。
    """
    with db() as conn:
        require_unsealed(conn)
        if 扫描编号 is not None:
            r = calibrate_scan(conn, 扫描编号, 来源="口门校准")
            repairs = [r] if r else []
        else:
            repairs = calibrate_all(conn, 来源="口门校准")
        return {"修复数": len(repairs), "修复": repairs}
