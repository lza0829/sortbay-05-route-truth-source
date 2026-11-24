from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, rows_to_list, row_to_dict
from app.services.common import audit, current_shift, now_str, require_unsealed
from app.services.routed_truth import calibrate_scan, classify, replay_scan

router = APIRouter(tags=["封袋"])


class BagOpenIn(BaseModel):
    口门编号: int
    备注: str = ""


class BagAddIn(BaseModel):
    扫描编号: int


@router.get("/封袋")
def list_bags():
    with db() as conn:
        return rows_to_list(conn.execute(
            """
            SELECT b.*, c.code AS 口门码,
              (SELECT COUNT(*) FROM scans s WHERE s.bag_id=b.id) AS 件数
            FROM bags b JOIN chutes c ON c.id=b.chute_id
            ORDER BY b.id DESC LIMIT 100
            """
        ).fetchall())


@router.post("/封袋/开袋")
def open_bag(body: BagOpenIn):
    with db() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (body.口门编号,)).fetchone()
        if not chute:
            raise HTTPException(404, "口门不存在")
        code = f"袋-{chute['code']}-{shift['id']}-{now_str().replace(':','').replace(' ', '')[-10:]}"
        conn.execute(
            "INSERT INTO bags(shift_id, chute_id, code, status, note) VALUES (?, ?, ?, '集包中', ?)",
            (shift["id"], body.口门编号, code, body.备注),
        )
        row = conn.execute("SELECT * FROM bags WHERE code=?", (code,)).fetchone()
        audit(conn, "封袋", row["id"], "开袋", code)
        return row_to_dict(row)


@router.post("/封袋/{bag_id}/入袋")
def add_to_bag(bag_id: int, body: BagAddIn):
    """封袋在口校验：只认真源重放口门。

    - 无路由真源（假列/事件缺失）→ 拒绝并审计，假列不得入袋；
    - 真源口门≠袋口门（含旁路改列伪装）→ 拒绝并审计；
    - 真源放行时缓存若陈旧，本事务内顺手校准（列未动不得长期分叉）。
    - 拒因审计与业务写入分离：先提交拒因审计，再抛错，保证拒因可查。
    """
    拒因 = None
    with db() as conn:
        require_unsealed(conn)
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        if not bag:
            raise HTTPException(404, "袋不存在")
        if bag["status"] != "集包中":
            raise HTTPException(400, "袋已封，禁止再入")
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        if scan["bag_id"] is not None or scan["status"] in ("已入袋", "已封袋"):
            raise HTTPException(400, "该件已入袋")
        truth = replay_scan(conn, body.扫描编号)
        if not truth["已路由"]:
            audit(conn, "封袋", bag_id, "入袋拒绝", f"扫描{body.扫描编号} 无路由真源，假列不得入袋")
            拒因 = "入袋拒绝：无路由真源，口门列不得冒充裁决"
        elif truth["口门"] != bag["chute_id"]:
            audit(
                conn, "封袋", bag_id, "入袋拒绝",
                f"扫描{body.扫描编号} 真源口门{truth['口门']}≠袋口门{bag['chute_id']}",
            )
            拒因 = "入袋拒绝：真源口门与袋口门不一致"
        else:
            if classify(scan, truth)[0]:
                calibrate_scan(conn, body.扫描编号, 来源="入袋")
            conn.execute(
                "UPDATE scans SET status='已入袋', bag_id=? WHERE id=?",
                (bag_id, body.扫描编号),
            )
            audit(conn, "封袋", bag_id, "入袋", str(body.扫描编号))
            return row_to_dict(conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone())
    # 拒因审计已随事务提交；此处再抛错，避免回滚吞掉拒因
    raise HTTPException(409, 拒因)


@router.post("/封袋/{bag_id}/封口")
def seal_bag(bag_id: int):
    with db() as conn:
        require_unsealed(conn)
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        if not bag:
            raise HTTPException(404, "袋不存在")
        if bag["status"] != "集包中":
            raise HTTPException(400, "袋状态不可封口")
        n = conn.execute(
            "SELECT COUNT(*) c FROM scans WHERE bag_id=?", (bag_id,)
        ).fetchone()["c"]
        if n == 0:
            raise HTTPException(400, "空袋不可封口")
        conn.execute(
            "UPDATE bags SET status='已封袋', sealed_at=? WHERE id=?",
            (now_str(), bag_id),
        )
        conn.execute(
            "UPDATE scans SET status='已封袋' WHERE bag_id=?",
            (bag_id,),
        )
        audit(conn, "封袋", bag_id, "封口", f"件数{n}")
        return row_to_dict(conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone())
