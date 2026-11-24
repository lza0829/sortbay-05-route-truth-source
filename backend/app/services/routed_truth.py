"""已路由真源（全局唯一口径）：以路由事件流水重放为准。

架构抉择（全局只贯彻这一种，禁止与「以扫描口门列为准」混用）：

- route_events 是已路由结果的唯一真源；scans.chute_id / scans.routed_event_id
  只是加速缓存。任何展示面（扫描册、口门页、总览、封袋页、审计）与任何校验
  （封袋在口校验）都必须经本模块重放取真源，禁止绕过重放直接信列。
- 裁决写口 adjudicate() 是唯一允许同时写「事件 + 缓存列」的入口，两侧同事务；
  禁止只改口门数字（全局无任何端点单独写 scans.chute_id）。
- 并发双裁决：缓存列用条件更新兜底，败者整笔回滚（事件一并撤回），不漂一格。
- 旁路改列（库外直改缓存列）不得盖住重放结果：展示面一律显示真源并对照标分叉，
  校准 calibrate_scan() 把缓存整笔回滚到真源并留审计，绝不反向改事件。
- 半截裁决（只落一侧）由事务保证不发生；库外造成的半截由对账标出、校准修复，
  列与事件不得长期分叉。

陈旧标记（分叉分类，全局统一）：
- 事件缺失：列自称已路由/已入袋，流水无任何事件（纯假列、半截只落列侧）。
- 列未动：事件已落，缓存列/指针未跟进（半截只落事件侧、回流未清、指针滞后）。
- 旁路改列：缓存口门与重放口门直接冲突。
"""
from __future__ import annotations

from fastapi import HTTPException

from app.db import row_to_dict
from app.services.common import audit, now_str

ACTION_ROUTE = "路由落入"
ACTION_DIVERT = "错分回流"

STALE_NO_EVENT = "事件缺失"
STALE_CACHE = "列未动"
STALE_BYPASS = "旁路改列"

EMPTY_TRUTH = {"已路由": False, "口门": None, "最新事件号": None, "事件数": 0, "最新动作": None}

# 袋阶段状态以 scans.status 为准（封袋准入策略另立需求）；路由段状态以重放为准
_BAG_STAGES = ("已入袋", "已封袋")
_ROUTE_CLAIM_STATUSES = ("已路由", "已入袋", "已封袋")


# ---------------------------------------------------------------- 重放

def _fold(events) -> dict:
    """按事件号升序折叠：路由落入置口门，错分回流清口门。"""
    slot = dict(EMPTY_TRUTH)
    for e in events:
        if e["action"] == ACTION_ROUTE:
            slot["口门"] = e["chute_id"]
        elif e["action"] == ACTION_DIVERT:
            slot["口门"] = None
        else:
            continue
        slot["最新事件号"] = e["id"]
        slot["最新动作"] = e["action"]
        slot["事件数"] += 1
    slot["已路由"] = slot["口门"] is not None
    return slot


def replay_scan(conn, scan_id: int) -> dict:
    """单件重放：已路由结果由路由事件流水重放得出。"""
    rows = conn.execute(
        "SELECT * FROM route_events WHERE scan_id=? ORDER BY id", (scan_id,)
    ).fetchall()
    return _fold(rows)


def replay_all(conn) -> dict[int, dict]:
    """全量重放（单查询，避免 N+1），供列表/总览/对账同源使用。"""
    out: dict[int, dict] = {}
    rows = conn.execute("SELECT * FROM route_events ORDER BY id").fetchall()
    for e in rows:
        slot = out.setdefault(e["scan_id"], dict(EMPTY_TRUTH))
        if e["action"] == ACTION_ROUTE:
            slot["口门"] = e["chute_id"]
        elif e["action"] == ACTION_DIVERT:
            slot["口门"] = None
        else:
            continue
        slot["最新事件号"] = e["id"]
        slot["最新动作"] = e["action"]
        slot["事件数"] += 1
    for slot in out.values():
        slot["已路由"] = slot["口门"] is not None
    return out


def truth_of(truths: dict[int, dict], scan_id: int) -> dict:
    return truths.get(scan_id, EMPTY_TRUTH)


# ---------------------------------------------------------------- 对照

def classify(scan, truth: dict) -> tuple[bool, str | None]:
    """缓存列 vs 真源重放：返回 (是否分叉, 陈旧标记)。"""
    cache_chute = scan["chute_id"]
    cache_ptr = scan["routed_event_id"]
    status = scan["status"]
    if truth["事件数"] == 0:
        if cache_chute is not None or status in _ROUTE_CLAIM_STATUSES:
            return True, STALE_NO_EVENT
        return False, None
    if truth["已路由"]:
        if cache_chute is None or status in ("待路由", "错分回流"):
            return True, STALE_CACHE
        if cache_chute != truth["口门"]:
            return True, STALE_BYPASS
        if cache_ptr != truth["最新事件号"]:
            return True, STALE_CACHE
        return False, None
    # 真源当前未路由（回流之后 / 列侧假写）
    if cache_chute is not None or status == "已路由":
        return True, STALE_CACHE
    if cache_ptr not in (None, truth["最新事件号"]):
        return True, STALE_CACHE
    return False, None


def effective_status(scan, truth: dict) -> str:
    """真源校正后的有效状态：袋阶段信 scans.status，路由段信重放。

    假列（列称已路由但流水无事件）回落为待路由，不得亮已对齐。
    """
    status = scan["status"]
    if status in _BAG_STAGES:
        return status
    if truth["已路由"]:
        return "已路由"
    if status == "错分回流":
        return "错分回流"
    return "待路由"


def truth_view(scan, truth: dict) -> dict:
    """扫描册行：原始缓存列 + 真源对照，展示以真源为准。"""
    分叉, 标记 = classify(scan, truth)
    row = dict(scan)
    row.update({
        "真源已路由": truth["已路由"],
        "真源口门": truth["口门"],
        "有效状态": effective_status(scan, truth),
        "最新事件号": truth["最新事件号"],
        "分叉": 分叉,
        "陈旧标记": 标记,
    })
    return row


# ---------------------------------------------------------------- 同源计数

def in_gate_counts(conn) -> dict[int, dict]:
    """每口门在口件数：真源重放值为准，缓存件数仅作对照。"""
    truths = replay_all(conn)
    out = {
        r["id"]: {"在口件数": 0, "缓存件数": 0, "分叉": False}
        for r in conn.execute("SELECT id FROM chutes").fetchall()
    }
    for s in conn.execute("SELECT * FROM scans").fetchall():
        t = truth_of(truths, s["id"])
        if effective_status(s, t) not in ("已路由", "已入袋"):
            continue
        if t["口门"] is not None:
            out.setdefault(t["口门"], {"在口件数": 0, "缓存件数": 0, "分叉": False})
            out[t["口门"]]["在口件数"] += 1
        if s["chute_id"] is not None:
            out.setdefault(s["chute_id"], {"在口件数": 0, "缓存件数": 0, "分叉": False})
            out[s["chute_id"]]["缓存件数"] += 1
    for slot in out.values():
        slot["分叉"] = slot["在口件数"] != slot["缓存件数"]
    return out


def overview_buckets(conn, shift_id: int | None = None) -> dict:
    """总览分布：已路由桶按真源计（假列回落待路由），另报分叉数。"""
    truths = replay_all(conn)
    sql = "SELECT * FROM scans"
    args: tuple = ()
    if shift_id is not None:
        sql += " WHERE shift_id=?"
        args = (shift_id,)
    buckets = {"待路由": 0, "已路由": 0, "错分回流": 0, "已入袋": 0, "已封袋": 0}
    分叉数 = 0
    for s in conn.execute(sql, args).fetchall():
        t = truth_of(truths, s["id"])
        buckets[effective_status(s, t)] += 1
        if classify(s, t)[0]:
            分叉数 += 1
    return {"分布": buckets, "分叉数": 分叉数}


# ---------------------------------------------------------------- 写口

def append_route_event(conn, scan_id: int, chute_id: int, action: str, note: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO route_events(scan_id, chute_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
        (scan_id, chute_id, action, now_str(), note),
    )
    return cur.lastrowid


def _sync_cache_on_route(conn, scan_id: int, chute_id: int, event_id: int):
    """缓存列跟随裁决：条件更新防并发双裁决（败者 rowcount=0 → 整笔回滚）。"""
    return conn.execute(
        "UPDATE scans SET status='已路由', chute_id=?, routed_event_id=? "
        "WHERE id=? AND status IN ('待路由','错分回流')",
        (chute_id, event_id, scan_id),
    )


def adjudicate(conn, scan_id: int) -> dict:
    """路由裁决唯一写口：事件与缓存同事务落两侧，回执同刷各展示面。"""
    scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    if not scan:
        raise HTTPException(404, "扫描不存在")
    if scan["status"] not in ("待路由", "错分回流"):
        raise HTTPException(409, f"当前状态不可裁决: {scan['status']}")
    if not scan["dest_code"]:
        raise HTTPException(400, "缺少目的地码")
    rule = conn.execute(
        "SELECT * FROM route_rules WHERE dest_code=?", (scan["dest_code"],)
    ).fetchone()
    if not rule:
        raise HTTPException(400, "无匹配路由")
    chute = conn.execute("SELECT * FROM chutes WHERE id=?", (rule["chute_id"],)).fetchone()
    if not chute:
        raise HTTPException(400, "口门不存在")
    if chute["status"] != "口门开":
        raise HTTPException(400, "口门已闭，禁止落入")
    eid = append_route_event(conn, scan_id, chute["id"], ACTION_ROUTE, chute["code"])
    cur = _sync_cache_on_route(conn, scan_id, chute["id"], eid)
    if cur.rowcount == 0:
        raise HTTPException(409, "并发裁决冲突：该件已被裁决，本笔已整笔回滚")
    audit(conn, "扫描", scan_id, "路由裁决", chute["code"])
    return receipt(conn, scan_id, eid)


def receipt(conn, scan_id: int, event_id: int) -> dict:
    """裁决回执：扫描册行、事件、真源、受影响口门在口、总览分布一并发回，前端同刷。"""
    scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    truth = replay_scan(conn, scan_id)
    event = conn.execute("SELECT * FROM route_events WHERE id=?", (event_id,)).fetchone()
    counts = in_gate_counts(conn)
    touched = sorted({scan["chute_id"], truth["口门"]} - {None})
    chutes = {}
    if touched:
        marks = ",".join("?" * len(touched))
        chutes = {
            r["id"]: r
            for r in conn.execute(f"SELECT * FROM chutes WHERE id IN ({marks})", tuple(touched)).fetchall()
        }
    return {
        "扫描": truth_view(scan, truth),
        "事件": row_to_dict(event),
        "真源": truth,
        "口门在口": [
            {
                "口门编号": cid,
                "口门码": chutes[cid]["code"] if cid in chutes else str(cid),
                **counts.get(cid, {"在口件数": 0, "缓存件数": 0, "分叉": False}),
            }
            for cid in touched
        ],
        "总览": overview_buckets(conn),
    }


# ---------------------------------------------------------------- 校准

def calibrate_scan(conn, scan_id: int, *, 来源: str = "校准") -> dict | None:
    """把缓存列整笔回滚到真源重放结果（旁路改列被覆盖，绝不反向改事件）。"""
    scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    if not scan:
        raise HTTPException(404, "扫描不存在")
    truth = replay_scan(conn, scan_id)
    分叉, 标记 = classify(scan, truth)
    if not 分叉:
        return None
    if truth["已路由"]:
        status = scan["status"] if scan["status"] in _BAG_STAGES else "已路由"
        conn.execute(
            "UPDATE scans SET chute_id=?, routed_event_id=?, status=? WHERE id=?",
            (truth["口门"], truth["最新事件号"], status, scan_id),
        )
    else:
        if scan["status"] in _BAG_STAGES:
            raise HTTPException(409, f"扫描{scan_id} 已入袋却无路由真源，禁止自动校准，需人工核查")
        status = "错分回流" if truth["最新动作"] == ACTION_DIVERT else "待路由"
        conn.execute(
            "UPDATE scans SET chute_id=NULL, routed_event_id=?, status=? WHERE id=?",
            (truth["最新事件号"], status, scan_id),
        )
    audit(
        conn, "真源", scan_id, "校准",
        f"{来源}:{标记} 列{scan['chute_id']}/{scan['status']}→真源{truth['口门']}",
    )
    return {
        "扫描编号": scan_id,
        "陈旧标记": 标记,
        "修复前口门": scan["chute_id"],
        "修复前状态": scan["status"],
        "真源口门": truth["口门"],
    }


def calibrate_all(conn, *, 来源: str = "校准") -> list[dict]:
    repairs = []
    truths = replay_all(conn)
    ids = [
        s["id"]
        for s in conn.execute("SELECT * FROM scans ORDER BY id").fetchall()
        if classify(s, truth_of(truths, s["id"]))[0]
    ]
    for scan_id in ids:
        r = calibrate_scan(conn, scan_id, 来源=来源)
        if r:
            repairs.append(r)
    return repairs


# ---------------------------------------------------------------- 审计重放

def truth_audit_report(conn) -> dict:
    """分拣线审计重放：逐件对照 装载目的地/现行规则口门/缓存列/真源重放。"""
    truths = replay_all(conn)
    rules = {r["dest_code"]: r["chute_id"] for r in conn.execute("SELECT * FROM route_rules").fetchall()}
    chute_code = {r["id"]: r["code"] for r in conn.execute("SELECT * FROM chutes").fetchall()}
    rows = []
    分叉数 = 0
    真源已路由数 = 0
    scans = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 500").fetchall()
    for s in scans:
        t = truth_of(truths, s["id"])
        分叉, 标记 = classify(s, t)
        if t["事件数"] == 0 and not 分叉:
            continue  # 未裁决的干净件不进对账表
        规则口门 = rules.get(s["dest_code"])
        规则漂移 = bool(t["已路由"] and 规则口门 is not None and 规则口门 != t["口门"])
        if 分叉:
            分叉数 += 1
        if t["已路由"]:
            真源已路由数 += 1
        rows.append({
            "扫描编号": s["id"],
            "条码": s["barcode"],
            "目的地码": s["dest_code"],
            "列状态": s["status"],
            "缓存口门": s["chute_id"],
            "缓存口门码": chute_code.get(s["chute_id"]),
            "缓存事件号": s["routed_event_id"],
            "真源已路由": t["已路由"],
            "真源口门": t["口门"],
            "真源口门码": chute_code.get(t["口门"]),
            "最新事件号": t["最新事件号"],
            "规则口门": 规则口门,
            "规则口门码": chute_code.get(规则口门),
            "规则漂移": 规则漂移,
            "分叉": 分叉,
            "陈旧标记": 标记,
        })
    事件流水 = [
        dict(r)
        for r in conn.execute("SELECT * FROM route_events ORDER BY id DESC LIMIT 100").fetchall()
    ]
    事件总数 = conn.execute("SELECT COUNT(*) c FROM route_events").fetchone()["c"]
    return {
        "对账": rows,
        "事件流水": 事件流水,
        "汇总": {"真源已路由数": 真源已路由数, "分叉数": 分叉数, "事件总数": 事件总数},
    }
