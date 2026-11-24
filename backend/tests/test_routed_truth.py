"""已路由真源夹具：旁路改列 / 双裁决半截 / 事件齐全列未动 / 假列入袋。

全局架构（唯一，不混用）：以路由事件流水重放为准，口门列只是加速缓存。
"""
from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException

from conftest import chute_map, make_scan, query, sql


# ---------------------------------------------------------------- 正常链路

def test_正常裁决_全链路同源(env):
    client, db_file = env
    chutes = chute_map(db_file)
    scan_id = make_scan(client, "BC-OK-1", "110")

    resp = client.post(f"/api/扫描/{scan_id}/路由")
    assert resp.status_code == 200, resp.text
    回执 = resp.json()
    # 裁决回执同刷：扫描行、事件、真源、口门在口、总览分布一次发回
    assert 回执["扫描"]["真源口门"] == chutes["A01"]
    assert 回执["扫描"]["分叉"] is False
    assert 回执["事件"]["action"] == "路由落入"
    assert 回执["扫描"]["routed_event_id"] == 回执["事件"]["id"]
    在口 = {c["口门码"]: c for c in 回执["口门在口"]}
    assert 在口["A01"]["在口件数"] == 1 and 在口["A01"]["分叉"] is False
    assert 回执["总览"]["分布"]["已路由"] == 1
    assert 回执["总览"]["分叉数"] == 0

    # 扫描册：真源与缓存一致，不亮分叉
    row = next(r for r in client.get("/api/扫描").json() if r["id"] == scan_id)
    assert row["有效状态"] == "已路由" and row["真源口门"] == chutes["A01"]
    assert row["分叉"] is False and row["陈旧标记"] is None

    # 口门页：在口件数真源=缓存=1
    a01 = next(c for c in client.get("/api/口门").json() if c["code"] == "A01")
    assert a01["在口件数"] == 1 and a01["缓存件数"] == 1 and a01["分叉"] is False

    # 总览：已路由桶=1（另有种子件待路由）
    总览 = client.get("/api/总览").json()
    assert 总览["分布"]["已路由"] == 1 and 总览["分叉数"] == 0

    # 审计重放：对账一致，无分叉
    报告 = client.get("/api/审计/已路由真源").json()
    行 = next(r for r in 报告["对账"] if r["扫描编号"] == scan_id)
    assert 行["分叉"] is False and 行["真源口门码"] == "A01" and 行["规则漂移"] is False
    assert 报告["汇总"]["分叉数"] == 0

    # 重复裁决：已被裁决的件不得二次落入
    again = client.post(f"/api/扫描/{scan_id}/路由")
    assert again.status_code == 409
    assert query(db_file, "SELECT COUNT(*) c FROM route_events WHERE scan_id=?", (scan_id,))[0]["c"] == 1


# ---------------------------------------------------------------- 夹具一：旁路改口门列

def test_旁路改口门列_不得伪装成裁决成功(env):
    client, db_file = env
    chutes = chute_map(db_file)
    scan_id = make_scan(client, "BC-BYPASS-1", "110")
    assert client.post(f"/api/扫描/{scan_id}/路由").status_code == 200

    # 库外旁路：只改口门数字 A01→A02，不动事件流水
    sql(db_file, "UPDATE scans SET chute_id=? WHERE id=?", (chutes["A02"], scan_id))

    # 扫描册：真源仍是 A01，缓存列标旁路改列，不得亮已对齐
    row = next(r for r in client.get("/api/扫描").json() if r["id"] == scan_id)
    assert row["真源口门"] == chutes["A01"]
    assert row["chute_id"] == chutes["A02"]
    assert row["分叉"] is True and row["陈旧标记"] == "旁路改列"

    # 口门页/总览：在口件数认真源——A01=1，A02 真源=0（假列不计数），缓存对照现形
    口门 = {c["code"]: c for c in client.get("/api/口门").json()}
    assert 口门["A01"]["在口件数"] == 1
    assert 口门["A02"]["在口件数"] == 0 and 口门["A02"]["缓存件数"] == 1 and 口门["A02"]["分叉"] is True
    总览 = client.get("/api/总览").json()
    assert 总览["分布"]["已路由"] == 1 and 总览["分叉数"] == 1

    # 审计重放：分叉现形
    报告 = client.get("/api/审计/已路由真源").json()
    行 = next(r for r in 报告["对账"] if r["扫描编号"] == scan_id)
    assert 行["分叉"] is True and 行["陈旧标记"] == "旁路改列"
    assert 行["真源口门码"] == "A01" and 行["缓存口门码"] == "A02"

    # 校准：缓存整笔回滚到真源（旁路被覆盖），留审计，此后列与事件不再分叉
    修复 = client.post("/api/口门/校准").json()
    assert 修复["修复数"] == 1 and 修复["修复"][0]["真源口门"] == chutes["A01"]
    row = query(db_file, "SELECT * FROM scans WHERE id=?", (scan_id,))[0]
    assert row["chute_id"] == chutes["A01"]
    assert query(db_file, "SELECT COUNT(*) c FROM route_events WHERE scan_id=?", (scan_id,))[0]["c"] == 1
    assert client.get("/api/总览").json()["分叉数"] == 0
    审计 = client.get("/api/审计").json()
    assert any(a["kind"] == "真源" and a["action"] == "校准" and a["ref_id"] == scan_id for a in 审计)


# ---------------------------------------------------------------- 夹具二：双裁决半截只落一侧

def test_双裁决半截只落一侧_事件压列(env):
    """两笔裁决各落半侧：事件落在 A01、列被写成 A02 —— 真源以事件为准。"""
    client, db_file = env
    chutes = chute_map(db_file)
    scan_id = make_scan(client, "BC-HALF-1", "110")
    sql(
        db_file,
        "INSERT INTO route_events(scan_id, chute_id, action, created_at, note) VALUES (?, ?, '路由落入', '2026-11-24 08:00:00', '半截事件侧')",
        (scan_id, chutes["A01"]),
    )
    sql(
        db_file,
        "UPDATE scans SET status='已路由', chute_id=? WHERE id=?",
        (chutes["A02"], scan_id),
    )

    # 各面以事件重放为准：A01 计数，A02 不计，列标旁路改列
    row = next(r for r in client.get("/api/扫描").json() if r["id"] == scan_id)
    assert row["真源口门"] == chutes["A01"] and row["陈旧标记"] == "旁路改列"
    口门 = {c["code"]: c for c in client.get("/api/口门").json()}
    assert 口门["A01"]["在口件数"] == 1 and 口门["A02"]["在口件数"] == 0

    # 半截回滚：校准把列整笔拉回事件侧，列与事件不得长期分叉
    client.post("/api/口门/校准")
    row = query(db_file, "SELECT * FROM scans WHERE id=?", (scan_id,))[0]
    assert row["chute_id"] == chutes["A01"] and row["status"] == "已路由"
    assert client.get("/api/总览").json()["分叉数"] == 0


def test_并发双裁决_不漂一格(env):
    """两个裁决写口并发：条件更新兜底，一胜一败，败者整笔回滚。"""
    client, db_file = env
    scan_id = make_scan(client, "BC-RACE-1", "110")
    from app.db import db
    from app.services.routed_truth import adjudicate

    结果 = []
    栅栏 = threading.Barrier(2)

    def worker():
        栅栏.wait()
        try:
            with db() as conn:
                adjudicate(conn, scan_id)
            结果.append("成功")
        except HTTPException as e:
            结果.append(e.status_code)

    t1, t2 = threading.Thread(target=worker), threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert sorted(结果, key=str) == [409, "成功"], 结果
    # 只落一格：事件一条、在口一件、已路由桶一件
    assert query(db_file, "SELECT COUNT(*) c FROM route_events WHERE scan_id=?", (scan_id,))[0]["c"] == 1
    总览 = client.get("/api/总览").json()
    assert 总览["分布"]["已路由"] == 1
    a01 = next(c for c in 总览["口门"] if c["code"] == "A01")
    assert a01["在口件数"] == 1


def test_半截裁决_整笔回滚后列与事件不分叉(env, monkeypatch):
    """裁决中途宕机（缓存写入失败）：事务整笔回滚，事件不得单边残留。"""
    client, db_file = env
    scan_id = make_scan(client, "BC-CRASH-1", "110")
    import app.services.routed_truth as rt

    def 宕机(*args, **kwargs):
        raise RuntimeError("模拟裁决半截宕机")

    原写缓存 = rt._sync_cache_on_route
    monkeypatch.setattr(rt, "_sync_cache_on_route", 宕机)
    resp = client.post(f"/api/扫描/{scan_id}/路由")
    assert resp.status_code == 500

    # 列与事件都未动：无单边残留，重试可正常落成
    row = query(db_file, "SELECT * FROM scans WHERE id=?", (scan_id,))[0]
    assert row["status"] == "待路由" and row["chute_id"] is None
    assert query(db_file, "SELECT COUNT(*) c FROM route_events WHERE scan_id=?", (scan_id,))[0]["c"] == 0
    assert client.get("/api/总览").json()["分叉数"] == 0
    rt._sync_cache_on_route = 原写缓存  # 手动恢复：undo() 会连带撤销 env 夹具的库补丁
    assert client.post(f"/api/扫描/{scan_id}/路由").status_code == 200


# ---------------------------------------------------------------- 夹具三：事件齐全列未动

def test_事件齐全列未动_真源计数并入袋校准(env):
    client, db_file = env
    chutes = chute_map(db_file)
    scan_id = make_scan(client, "BC-STALE-1", "110")
    # 半截只落事件侧：流水齐全，列仍是待路由
    sql(
        db_file,
        "INSERT INTO route_events(scan_id, chute_id, action, created_at, note) VALUES (?, ?, '路由落入', '2026-11-24 08:00:00', '半截事件侧')",
        (scan_id, chutes["A01"]),
    )

    # 扫描册：真源已路由到 A01，列标「列未动」
    row = next(r for r in client.get("/api/扫描").json() if r["id"] == scan_id)
    assert row["真源已路由"] is True and row["真源口门"] == chutes["A01"]
    assert row["有效状态"] == "已路由" and row["陈旧标记"] == "列未动"

    # 总览已路由桶按真源计入
    assert client.get("/api/总览").json()["分布"]["已路由"] == 1

    # 封袋在口校验：真源放行，同事务顺手校准缓存（不得长期分叉）
    bag = client.post("/api/封袋/开袋", json={"口门编号": chutes["A01"]}).json()
    resp = client.post(f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": scan_id})
    assert resp.status_code == 200, resp.text
    row = query(db_file, "SELECT * FROM scans WHERE id=?", (scan_id,))[0]
    event = query(db_file, "SELECT * FROM route_events WHERE scan_id=?", (scan_id,))[0]
    assert row["status"] == "已入袋" and row["chute_id"] == chutes["A01"]
    assert row["routed_event_id"] == event["id"]
    审计 = client.get("/api/审计").json()
    assert any(a["kind"] == "真源" and a["action"] == "校准" and a["ref_id"] == scan_id for a in 审计)
    assert client.get("/api/总览").json()["分叉数"] == 0


# ---------------------------------------------------------------- 夹具四：假列仍入袋成功（必须被拦）

def test_旁路假列_入袋被拒并审计(env):
    client, db_file = env
    chutes = chute_map(db_file)
    scan_id = make_scan(client, "BC-FAKEBAG-1", "110")
    assert client.post(f"/api/扫描/{scan_id}/路由").status_code == 200  # 真源 A01

    # 旁路把列改成 A02，企图混入 A02 的袋
    sql(db_file, "UPDATE scans SET chute_id=? WHERE id=?", (chutes["A02"], scan_id))
    bag = client.post("/api/封袋/开袋", json={"口门编号": chutes["A02"]}).json()
    resp = client.post(f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": scan_id})
    assert resp.status_code == 409 and "真源口门" in resp.json()["detail"]

    # 袋仍为空，拒因已审计
    袋 = next(b for b in client.get("/api/封袋").json() if b["id"] == bag["id"])
    assert 袋["件数"] == 0
    审计 = client.get("/api/审计").json()
    assert any(a["action"] == "入袋拒绝" and a["ref_id"] == bag["id"] for a in 审计)


def test_纯假列_无事件不得入袋也不得亮已对齐(env):
    client, db_file = env
    chutes = chute_map(db_file)
    scan_id = make_scan(client, "BC-FAKE-1", "110")
    # 纯假列：只写列自称已路由，流水无任何事件
    sql(db_file, "UPDATE scans SET status='已路由', chute_id=? WHERE id=?", (chutes["A01"], scan_id))

    # 扫描册/总览：假列不亮已对齐，回落待路由并标事件缺失
    row = next(r for r in client.get("/api/扫描").json() if r["id"] == scan_id)
    assert row["真源已路由"] is False and row["有效状态"] == "待路由"
    assert row["分叉"] is True and row["陈旧标记"] == "事件缺失"
    总览 = client.get("/api/总览").json()
    assert 总览["分布"]["已路由"] == 0 and 总览["分叉数"] == 1

    # 假列入袋：拒绝并审计
    bag = client.post("/api/封袋/开袋", json={"口门编号": chutes["A01"]}).json()
    resp = client.post(f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": scan_id})
    assert resp.status_code == 409 and "无路由真源" in resp.json()["detail"]
    袋 = next(b for b in client.get("/api/封袋").json() if b["id"] == bag["id"])
    assert 袋["件数"] == 0

    # 班次对账：假列不计入已路由，封班被拦（禁止假齐）
    对账 = client.get("/api/班次/对账").json()
    assert 对账["已路由及以上"] == 0 and 对账["分叉数"] == 1
    班次 = 对账["班次"]
    assert client.post(f"/api/班次/{班次['id']}/封班").status_code == 400

    # 校准后假列清回待路由，分叉清零
    client.post("/api/口门/校准")
    row = query(db_file, "SELECT * FROM scans WHERE id=?", (scan_id,))[0]
    assert row["status"] == "待路由" and row["chute_id"] is None
    assert client.get("/api/班次/对账").json()["分叉数"] == 0


# ---------------------------------------------------------------- 同源联动

def test_回流再路由_事件链同源(env):
    client, db_file = env
    chutes = chute_map(db_file)
    scan_id = make_scan(client, "BC-DIVERT-1", "110")
    assert client.post(f"/api/扫描/{scan_id}/路由").status_code == 200

    resp = client.post("/api/回流批次", json={"扫描编号": scan_id, "备注": "错分"})
    assert resp.status_code == 200, resp.text
    # 缓存随事件同步清空：真源未路由，在口归零
    row = query(db_file, "SELECT * FROM scans WHERE id=?", (scan_id,))[0]
    assert row["status"] == "错分回流" and row["chute_id"] is None
    总览 = client.get("/api/总览").json()
    assert 总览["分布"]["错分回流"] == 1 and 总览["分叉数"] == 0
    a01 = next(c for c in 总览["口门"] if c["code"] == "A01")
    assert a01["在口件数"] == 0

    # 再路由：事件链 落入→回流→落入，真源回到 A01
    assert client.post(f"/api/扫描/{scan_id}/路由").status_code == 200
    事件 = query(db_file, "SELECT action FROM route_events WHERE scan_id=? ORDER BY id", (scan_id,))
    assert [e["action"] for e in 事件] == ["路由落入", "错分回流", "路由落入"]
    assert client.get("/api/总览").json()["分布"]["已路由"] == 1


def test_规则漂移_只是信息位不算分叉(env):
    client, db_file = env
    chutes = chute_map(db_file)
    scan_id = make_scan(client, "BC-DRIFT-1", "110")
    assert client.post(f"/api/扫描/{scan_id}/路由").status_code == 200  # 真源 A01

    # 装载目的地 110 的现行规则改指 A02：历史裁决真源不动，仅标规则漂移
    resp = client.post("/api/路由表", json={"目的地码": "110", "口门编号": chutes["A02"], "备注": "改规则"})
    assert resp.status_code == 200
    报告 = client.get("/api/审计/已路由真源").json()
    行 = next(r for r in 报告["对账"] if r["扫描编号"] == scan_id)
    assert 行["真源口门码"] == "A01" and 行["规则口门码"] == "A02"
    assert 行["规则漂移"] is True and 行["分叉"] is False
    assert client.get("/api/总览").json()["分叉数"] == 0


def test_口门已闭_禁止落入(env):
    client, db_file = env
    chutes = chute_map(db_file)
    scan_id = make_scan(client, "BC-CLOSED-1", "110")
    assert client.post(f"/api/口门/{chutes['A01']}/开闭?状态=口门闭").status_code == 200
    resp = client.post(f"/api/扫描/{scan_id}/路由")
    assert resp.status_code == 400 and "口门已闭" in resp.json()["detail"]
    # 未落任何一侧：列与事件都不动
    assert query(db_file, "SELECT COUNT(*) c FROM route_events WHERE scan_id=?", (scan_id,))[0]["c"] == 0
    row = query(db_file, "SELECT * FROM scans WHERE id=?", (scan_id,))[0]
    assert row["status"] == "待路由" and row["chute_id"] is None
