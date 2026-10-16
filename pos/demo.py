"""통계 화면 시연용 과거 판매 데이터 생성기.

    python main.py --demo 60     # 최근 60일치 주문/근태 기록 생성
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from . import config
from .services import PosError
from .system import PosSystem

# 시간대별 주문 가중치 (점심/저녁 피크)
HOUR_WEIGHTS = {11: 4, 12: 10, 13: 7, 14: 3, 15: 2, 16: 2, 17: 5, 18: 9, 19: 7, 20: 3}


def generate(pos: PosSystem, days: int = 60, seed: int = 42) -> int:
    if pos.cash.current():
        raise PosError("시재가 개시된 상태에서는 샘플 데이터를 만들 수 없습니다. 시재를 먼저 정산하세요.")
    rng = random.Random(seed)
    real_now = pos.db._now
    clock = {"t": datetime.now()}
    pos.db._now = lambda: clock["t"]
    menus = [m for m in pos.menu.menus()]
    weights = [6 if m["category"] in ("분식", "김밥") else 3 for m in menus]
    tables = [t["id"] for t in pos.tables.tables()]
    staff = pos.staff.staff_list()
    member_ids = []
    for i, (name, phone) in enumerate([("김철수", "010-1000-0001"), ("이영희", "010-1000-0002"),
                                       ("박민수", "010-1000-0003"), ("최지우", "010-1000-0004"),
                                       ("정다은", "010-1000-0005")]):
        m = pos.db.query_one("SELECT id FROM members WHERE phone=?", (phone,))
        member_ids.append(m["id"] if m else pos.members.register(name, phone))

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    count = 0
    for d in range(days, 0, -1):
        day = today - timedelta(days=d)
        # 시재 개시 (전날 남긴 현금 이월)
        clock["t"] = day.replace(hour=10, minute=30)
        if not pos.cash.current():
            pos.cash.open(min(pos.cash.default_opening(), 100_000), "김사장")
        # 매일 아침 재고 보충
        for ing in pos.menu.ingredients():
            if ing["stock"] < ing["threshold"] * 5:
                pos.menu.adjust_stock(ing["id"], ing["threshold"] * 6, "정기 입고")
        # 근태 기록
        for s in staff:
            if s["kind"] == "FULL" or rng.random() < 0.6:
                start = 10 if s["kind"] == "FULL" else rng.choice([11, 12, 17])
                length = 9 if s["kind"] == "FULL" else rng.choice([4, 5, 6])
                cin = day.replace(hour=start, minute=rng.randint(0, 10))
                try:
                    pos.staff.add_log(s["id"], cin.strftime("%Y-%m-%d %H:%M:%S"),
                                      (cin + timedelta(hours=length)).strftime("%Y-%m-%d %H:%M:%S"))
                except Exception:
                    pass
        n_orders = rng.randint(18, 40) + (10 if day.weekday() >= 5 else 0)
        hours = rng.choices(list(HOUR_WEIGHTS), weights=list(HOUR_WEIGHTS.values()), k=n_orders)
        for h in sorted(hours):
            clock["t"] = day.replace(hour=h, minute=rng.randint(0, 59), second=rng.randint(0, 59))
            takeout = rng.random() < 0.3
            table = None if takeout else rng.choice(tables)
            if table and pos.orders.open_order_for_table(table):
                continue
            oid = None
            try:
                oid = pos.orders.create_order(table, "TAKEOUT" if takeout else "DINE_IN")
                for m in rng.choices(menus, weights=weights, k=rng.randint(1, 4)):
                    picked = []
                    groups = set()
                    for o in pos.menu.options(m["id"]):
                        if rng.random() < 0.3 and (not o["group_name"] or o["group_name"] not in groups):
                            picked.append(o["id"])
                            groups.add(o["group_name"])
                    pos.orders.add_item(oid, m["id"], rng.choice([1, 1, 1, 2]), option_ids=picked)
            except Exception:
                # 재고 부족 등으로 주문을 완성하지 못하면 미결제 주문을 남기지 않는다.
                if oid is not None and pos.orders.get(oid)["status"] == "OPEN":
                    if pos.orders.items(oid):
                        pos.orders.cancel_order(oid, "재고 부족")
                    else:
                        pos.orders.discard_if_empty(oid)
                continue
            if rng.random() < 0.03:
                pos.orders.cancel_order(oid, "고객 변심")
                continue
            # 조리
            for it in pos.orders.items(oid):
                clock["t"] += timedelta(minutes=rng.randint(3, 12))
                pos.orders.complete_item(it["id"])
            clock["t"] += timedelta(minutes=rng.randint(5, 30))
            if rng.random() < 0.35:
                pos.payments.attach_member(oid, rng.choice(member_ids))
            method = rng.choices(["CASH", "CARD", "EASY", "TRANSFER"], weights=[2, 6, 3, 1])[0]
            total = pos.discount.calculate(oid)["total"]
            received = -(-total // 10000) * 10000 if method == "CASH" else None
            pos.payments.pay(oid, method, received=received)
            if takeout:
                pos.orders.mark_picked_up(oid)
            r = rng.random()
            if r < 0.02:
                clock["t"] += timedelta(minutes=10)
                pos.payments.refund(oid, "음식 불만족")
            elif r < 0.04:
                items = pos.orders.items(oid)
                if len(items) > 1:
                    clock["t"] += timedelta(minutes=5)
                    pos.payments.refund_items(oid, {items[0]["id"]: 1}, "메뉴 잘못 나옴")
            count += 1
        _settle_cash(pos, clock, day, rng)
    pos.db._now = real_now
    pos.db.log("샘플 데이터 생성", f"{days}일 {count}건")
    pos.db.commit()
    return count


def _denominations(amount: int) -> dict[int, int]:
    out = {}
    for unit in config.CASH_DENOMINATIONS:
        out[unit], amount = divmod(amount, unit)
    return out


def _settle_cash(pos: PosSystem, clock: dict, day: datetime, rng: random.Random) -> None:
    """영업 종료: 10만원만 남기고 은행 입금 후 시재 정산(가끔 작은 과부족 발생)."""
    if not pos.cash.current():
        return
    clock["t"] = day.replace(hour=21, minute=30)
    if rng.random() < 0.3:
        pos.cash.add_movement("OUT", rng.choice([5_000, 12_000, 20_000]), "재료 구입(시장)")
    expected = pos.cash.summary()["expected"]
    if expected > 100_000:
        pos.cash.add_movement("OUT", (expected - 100_000) // 10 * 10, "은행 입금")
    expected = pos.cash.summary()["expected"]
    diff = rng.choice([0, 0, 0, 0, -500, -1_000, 500])
    counted = max(0, expected + diff)
    clock["t"] += timedelta(minutes=5)
    pos.cash.close(_denominations(counted), "잔돈 계산 실수" if counted != expected else "", "김사장")
