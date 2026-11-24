"""已路由真源夹具基座：每用例独立临时库 + 种子数据。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """独立临时库：打补丁到 app.db.DB_PATH（connect 时读取），返回 (client, db_path)。"""
    db_file = tmp_path / "sortbay-test.db"
    monkeypatch.setenv("SORTBAY_DB", str(db_file))
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", db_file)
    import app.db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", db_file)
    from app.seed import seed_if_empty
    seed_if_empty()
    from fastapi.testclient import TestClient
    import app.main as main
    client = TestClient(main.app, raise_server_exceptions=False)
    return client, db_file


def sql(db_file, statement, args=()):
    """库外直改：模拟旁路改列/半截写等越过了裁决写口的操作。"""
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(statement, args)
        conn.commit()
        return cur
    finally:
        conn.close()


def query(db_file, statement, args=()):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(statement, args).fetchall()]
    finally:
        conn.close()


def chute_map(db_file):
    return {r["code"]: r["id"] for r in query(db_file, "SELECT * FROM chutes")}


def make_scan(client, barcode, dest="110"):
    resp = client.post("/api/扫描", json={"条码": barcode, "目的地码": dest})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]
