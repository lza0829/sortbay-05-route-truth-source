from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.routed_truth import truth_audit_report

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit():
    with db() as conn:
        return rows_to_list(conn.execute(
            "SELECT * FROM audit_events ORDER BY id DESC LIMIT 200"
        ).fetchall())


@router.get("/审计/已路由真源")
def routed_truth_audit():
    """分拣线审计重放：路由事件流水重放 vs 口门缓存列逐件对账。

    旁路改列、半截只落一侧、事件齐全列未动在此现形；装载条码目的地与
    现行规则口门同列对照（规则漂移仅作信息位，不算分叉）。
    """
    with db() as conn:
        return truth_audit_report(conn)
