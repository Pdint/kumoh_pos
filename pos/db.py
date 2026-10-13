"""SQLite 데이터베이스 연결, 스키마 생성, 초기 데이터."""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime
from typing import Callable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE,
    sort  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS menus (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    category_id    INTEGER NOT NULL REFERENCES categories(id),
    price          INTEGER NOT NULL,
    cost           INTEGER NOT NULL DEFAULT 0,
    discount_rate  INTEGER NOT NULL DEFAULT 0,
    manual_soldout INTEGER NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ingredients (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    unit       TEXT NOT NULL DEFAULT '개',
    stock      REAL NOT NULL DEFAULT 0,
    threshold  REAL NOT NULL DEFAULT 10
);
CREATE TABLE IF NOT EXISTS recipes (
    menu_id        INTEGER NOT NULL REFERENCES menus(id) ON DELETE CASCADE,
    ingredient_id  INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
    qty            REAL NOT NULL,
    PRIMARY KEY (menu_id, ingredient_id)
);
CREATE TABLE IF NOT EXISTS stock_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ingredient_id  INTEGER NOT NULL,
    delta          REAL NOT NULL,
    reason         TEXT NOT NULL,
    at             TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tables_ (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    seats   INTEGER NOT NULL DEFAULT 4,
    active  INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS members (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    phone        TEXT NOT NULL UNIQUE,
    points       INTEGER NOT NULL DEFAULT 0,
    total_spent  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS point_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id  INTEGER NOT NULL,
    delta      INTEGER NOT NULL,
    reason     TEXT NOT NULL,
    order_id   INTEGER,
    at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coupons (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('RATE','AMOUNT')),
    value       INTEGER NOT NULL,
    min_amount  INTEGER NOT NULL DEFAULT 0,
    expires     TEXT,
    max_uses    INTEGER NOT NULL DEFAULT 0,
    used_count  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS time_discounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL,
    rate        INTEGER NOT NULL,
    weekdays    TEXT NOT NULL DEFAULT '0123456',
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS orders (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no         INTEGER NOT NULL,
    table_id         INTEGER,
    order_type       TEXT NOT NULL DEFAULT 'DINE_IN',
    status           TEXT NOT NULL DEFAULT 'OPEN',
    created_at       TEXT NOT NULL,
    completed_at     TEXT,
    paid_at          TEXT,
    cancelled_at     TEXT,
    member_id        INTEGER,
    coupon_code      TEXT,
    points_used      INTEGER NOT NULL DEFAULT 0,
    points_earned    INTEGER NOT NULL DEFAULT 0,
    locked           INTEGER NOT NULL DEFAULT 0,
    subtotal         INTEGER NOT NULL DEFAULT 0,
    menu_discount    INTEGER NOT NULL DEFAULT 0,
    time_discount    INTEGER NOT NULL DEFAULT 0,
    coupon_discount  INTEGER NOT NULL DEFAULT 0,
    total            INTEGER NOT NULL DEFAULT 0,
    reservation_id   INTEGER,
    cancel_reason    TEXT,
    picked_up        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS order_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    menu_id         INTEGER NOT NULL,
    name            TEXT NOT NULL,
    unit_price      INTEGER NOT NULL,
    unit_cost       INTEGER NOT NULL,
    discount_rate   INTEGER NOT NULL DEFAULT 0,
    qty             INTEGER NOT NULL,
    paid_qty        INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'WAITING',
    ordered_at      TEXT NOT NULL,
    done_at         TEXT
);
CREATE TABLE IF NOT EXISTS payments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     INTEGER NOT NULL REFERENCES orders(id),
    method       TEXT NOT NULL,
    amount       INTEGER NOT NULL,
    received     INTEGER NOT NULL DEFAULT 0,
    change       INTEGER NOT NULL DEFAULT 0,
    paid_at      TEXT NOT NULL,
    refunded_at  TEXT,
    memo         TEXT
);
CREATE TABLE IF NOT EXISTS staff (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('FULL','PART')),
    hourly_wage     INTEGER NOT NULL DEFAULT 0,
    monthly_salary  INTEGER NOT NULL DEFAULT 0,
    phone           TEXT,
    active          INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS work_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id   INTEGER NOT NULL REFERENCES staff(id),
    clock_in   TEXT NOT NULL,
    clock_out  TEXT
);
CREATE TABLE IF NOT EXISTS reservations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name  TEXT NOT NULL,
    phone          TEXT NOT NULL,
    reserve_at     TEXT NOT NULL,
    order_type     TEXT NOT NULL DEFAULT 'TAKEOUT',
    table_id       INTEGER,
    status         TEXT NOT NULL DEFAULT 'WAITING',
    note           TEXT,
    order_id       INTEGER,
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservation_items (
    reservation_id  INTEGER NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
    menu_id         INTEGER NOT NULL,
    qty             INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS closings (
    business_date  TEXT PRIMARY KEY,
    closed_at      TEXT NOT NULL,
    report         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    mode    TEXT NOT NULL,
    action  TEXT NOT NULL,
    detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_paid_at ON orders(paid_at);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);
"""


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    salt, _ = stored.split("$", 1)
    return secrets.compare_digest(hash_password(password, salt), stored)


class Database:
    """sqlite3 연결 래퍼. 모든 서비스가 하나의 Database 인스턴스를 공유한다."""

    def __init__(self, path: str = ":memory:", now: Callable[[], datetime] | None = None):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._now = now or datetime.now
        self.mode = "USER"
        self.conn.executescript(SCHEMA)
        if not self.get_setting("admin_password"):
            self.set_setting("admin_password", hash_password(config.DEFAULT_ADMIN_PASSWORD))

    # ---- 시간 ----
    def now(self) -> datetime:
        return self._now().replace(microsecond=0)

    def now_str(self) -> str:
        return self.now().strftime(config.DATETIME_FMT)

    # ---- 쿼리 헬퍼 ----
    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: tuple | list = ()):
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        self.conn.close()

    # ---- 설정 ----
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.commit()

    # ---- 감사 로그 ----
    def log(self, action: str, detail: str = "") -> None:
        self.execute(
            "INSERT INTO audit_logs(at, mode, action, detail) VALUES(?,?,?,?)",
            (self.now_str(), self.mode, action, detail),
        )

    def is_empty(self) -> bool:
        return not self.scalar("SELECT COUNT(*) FROM menus")


def seed_initial_data(db: Database) -> None:
    """처음 실행 시 분식집 기본 메뉴/재료/테이블/직원 데이터를 넣는다."""
    if not db.is_empty():
        return
    categories = ["분식", "김밥", "면류", "튀김/사이드", "음료"]
    for i, name in enumerate(categories):
        db.execute("INSERT INTO categories(name, sort) VALUES(?, ?)", (name, i))
    cat = {r["name"]: r["id"] for r in db.query("SELECT id, name FROM categories")}

    ingredients = [
        # 이름, 단위, 재고, 경고 기준
        ("떡", "인분", 80, 15), ("어묵", "장", 120, 20), ("떡볶이소스", "인분", 100, 15),
        ("순대", "인분", 40, 8), ("김", "장", 150, 20), ("밥", "공기", 100, 20),
        ("참치", "인분", 30, 6), ("치즈", "장", 40, 8), ("라면사리", "개", 60, 10),
        ("쫄면사리", "개", 30, 6), ("계란", "개", 60, 12), ("튀김", "개", 120, 20),
        ("야채", "인분", 80, 15), ("콜라", "캔", 48, 12), ("사이다", "캔", 48, 12),
        ("생수", "병", 30, 6),
    ]
    for name, unit, stock, th in ingredients:
        db.execute(
            "INSERT INTO ingredients(name, unit, stock, threshold) VALUES(?,?,?,?)",
            (name, unit, stock, th),
        )
    ing = {r["name"]: r["id"] for r in db.query("SELECT id, name FROM ingredients")}

    menus = [
        # 이름, 분류, 가격, 원가, 메뉴할인율, 레시피
        ("떡볶이", "분식", 4500, 1500, 0, {"떡": 1, "어묵": 1, "떡볶이소스": 1}),
        ("치즈떡볶이", "분식", 5500, 2000, 0, {"떡": 1, "어묵": 1, "떡볶이소스": 1, "치즈": 1}),
        ("라볶이", "분식", 5500, 1900, 0, {"떡": 1, "라면사리": 1, "떡볶이소스": 1, "계란": 1}),
        ("순대", "분식", 4500, 1600, 0, {"순대": 1}),
        ("어묵탕", "분식", 4000, 1200, 0, {"어묵": 3}),
        ("야채김밥", "김밥", 3500, 1100, 0, {"김": 1, "밥": 1, "야채": 1, "계란": 1}),
        ("참치김밥", "김밥", 4500, 1600, 10, {"김": 1, "밥": 1, "야채": 1, "참치": 1}),
        ("치즈김밥", "김밥", 4500, 1500, 0, {"김": 1, "밥": 1, "야채": 1, "치즈": 1}),
        ("라면", "면류", 4000, 1100, 0, {"라면사리": 1, "계란": 1}),
        ("치즈라면", "면류", 4500, 1400, 0, {"라면사리": 1, "계란": 1, "치즈": 1}),
        ("쫄면", "면류", 6000, 1900, 0, {"쫄면사리": 1, "야채": 1, "계란": 1}),
        ("모듬튀김", "튀김/사이드", 5000, 1700, 0, {"튀김": 5}),
        ("공기밥", "튀김/사이드", 1000, 300, 0, {"밥": 1}),
        ("콜라", "음료", 2000, 700, 0, {"콜라": 1}),
        ("사이다", "음료", 2000, 700, 0, {"사이다": 1}),
        ("생수", "음료", 1000, 300, 0, {"생수": 1}),
    ]
    for name, c, price, cost, rate, recipe in menus:
        cur = db.execute(
            "INSERT INTO menus(name, category_id, price, cost, discount_rate) VALUES(?,?,?,?,?)",
            (name, cat[c], price, cost, rate),
        )
        for ing_name, qty in recipe.items():
            db.execute(
                "INSERT INTO recipes(menu_id, ingredient_id, qty) VALUES(?,?,?)",
                (cur.lastrowid, ing[ing_name], qty),
            )

    for i in range(1, 9):
        db.execute("INSERT INTO tables_(name, seats) VALUES(?, ?)", (f"T{i}", 2 if i > 6 else 4))

    db.execute(
        "INSERT INTO time_discounts(name, start_time, end_time, rate, weekdays) VALUES(?,?,?,?,?)",
        ("오후 한산시간 할인", "15:00", "17:00", 10, "01234"),
    )
    db.execute(
        "INSERT INTO coupons(code, name, kind, value, min_amount, expires, max_uses) "
        "VALUES(?,?,?,?,?,?,?)",
        ("WELCOME1000", "신규 방문 1,000원 할인", "AMOUNT", 1000, 8000, None, 0),
    )
    db.execute(
        "INSERT INTO coupons(code, name, kind, value, min_amount, expires, max_uses) "
        "VALUES(?,?,?,?,?,?,?)",
        ("KUMOH10", "금오인 10% 할인", "RATE", 10, 10000, None, 0),
    )
    db.execute(
        "INSERT INTO staff(name, kind, hourly_wage, monthly_salary, phone) VALUES(?,?,?,?,?)",
        ("김사장", "FULL", 0, 2_800_000, "010-1111-2222"),
    )
    db.execute(
        "INSERT INTO staff(name, kind, hourly_wage, monthly_salary, phone) VALUES(?,?,?,?,?)",
        ("이알바", "PART", 10_500, 0, "010-3333-4444"),
    )
    db.execute(
        "INSERT INTO staff(name, kind, hourly_wage, monthly_salary, phone) VALUES(?,?,?,?,?)",
        ("박알바", "PART", 10_320, 0, "010-5555-6666"),
    )
    db.commit()
