"""비즈니스 로직 단위 테스트.  실행: python -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pos.services import PosError  # noqa: E402
from pos.system import PosSystem  # noqa: E402


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt

    def advance(self, **kw):
        self.dt += timedelta(**kw)


class PosTestCase(unittest.TestCase):
    def setUp(self):
        # 2026-10-12(월) 12:00 — 시간대 할인(15~17시)이 적용되지 않는 시각
        self.clock = Clock(datetime(2026, 10, 12, 12, 0))
        self.tmp = tempfile.TemporaryDirectory()
        self.pos = PosSystem(":memory:", out_dir=self.tmp.name, now=self.clock)
        self.menu = {m["name"]: m["id"] for m in self.pos.menu.menus()}
        self.ing = {i["name"]: i["id"] for i in self.pos.menu.ingredients()}

    def tearDown(self):
        self.pos.close()
        self.tmp.cleanup()

    def stock(self, name):
        return self.pos.db.scalar("SELECT stock FROM ingredients WHERE id=?", (self.ing[name],))

    def new_order(self, table=1, **items):
        oid = self.pos.orders.create_order(table)
        for name, qty in items.items():
            self.pos.orders.add_item(oid, self.menu[name], qty)
        return oid


class OrderTest(PosTestCase):
    def test_add_change_delete_items(self):
        oid = self.new_order(떡볶이=2)
        self.pos.orders.add_item(oid, self.menu["떡볶이"])  # 같은 메뉴는 수량 합산
        items = self.pos.orders.items(oid)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["qty"], 3)
        self.pos.orders.set_item_qty(items[0]["id"], 1)
        self.assertEqual(self.pos.orders.items(oid)[0]["qty"], 1)
        self.pos.orders.remove_item(items[0]["id"])
        self.assertEqual(self.pos.orders.items(oid), [])

    def test_same_table_reuses_open_order(self):
        a = self.pos.orders.create_order(2)
        b = self.pos.orders.create_order(2)
        self.assertEqual(a, b)

    def test_stock_consumed_and_restored(self):
        before = self.stock("떡")
        oid = self.new_order(떡볶이=3)
        self.assertEqual(self.stock("떡"), before - 3)
        item = self.pos.orders.items(oid)[0]
        self.pos.orders.set_item_qty(item["id"], 1)
        self.assertEqual(self.stock("떡"), before - 1)
        self.pos.orders.cancel_order(oid)
        self.assertEqual(self.stock("떡"), before)

    def test_auto_soldout_and_restock(self):
        self.pos.db.execute("UPDATE ingredients SET stock=2 WHERE id=?", (self.ing["순대"],))
        self.pos.db.commit()
        oid = self.new_order(순대=2)
        self.assertTrue(self.pos.menu.is_soldout(self.menu["순대"]))
        with self.assertRaises(PosError):
            self.pos.orders.add_item(oid, self.menu["순대"])
        self.pos.menu.adjust_stock(self.ing["순대"], 10, "입고")
        self.assertFalse(self.pos.menu.is_soldout(self.menu["순대"]))

    def test_low_stock_warning(self):
        self.pos.db.execute("UPDATE ingredients SET stock=9, threshold=8 WHERE id=?",
                            (self.ing["순대"],))
        oid = self.pos.orders.create_order(1)
        warnings = self.pos.orders.add_item(oid, self.menu["순대"], 1)
        self.assertTrue(any("순대" in w for w in warnings))

    def test_manual_soldout(self):
        self.pos.menu.set_manual_soldout(self.menu["라면"], True)
        oid = self.pos.orders.create_order(1)
        with self.assertRaises(PosError):
            self.pos.orders.add_item(oid, self.menu["라면"])

    def test_qty_limit(self):
        oid = self.pos.orders.create_order(1)
        with self.assertRaises(PosError):
            self.pos.orders.add_item(oid, self.menu["콜라"], 100)

    def test_merge_tables(self):
        a = self.new_order(1, 떡볶이=1)
        b = self.new_order(2, 순대=1)
        merged = self.pos.orders.merge_tables(1, 2)
        self.assertEqual(merged, b)
        self.assertIsNone(self.pos.orders.open_order_for_table(1))
        names = sorted(i["name"] for i in self.pos.orders.items(b))
        self.assertEqual(names, ["떡볶이", "순대"])
        with self.assertRaises(PosError):
            self.pos.orders.get(a)

    def test_move_table_when_target_empty(self):
        a = self.new_order(1, 라면=1)
        self.assertEqual(self.pos.orders.merge_tables(1, 3), a)
        self.assertEqual(self.pos.orders.get(a)["table_id"], 3)

    def test_split_table_partial_qty(self):
        a = self.new_order(1, 떡볶이=3, 콜라=1)
        items = {i["name"]: i for i in self.pos.orders.items(a)}
        b = self.pos.orders.split_items(a, {items["떡볶이"]["id"]: 1, items["콜라"]["id"]: 1}, 4)
        a_items = {i["name"]: i["qty"] for i in self.pos.orders.items(a)}
        b_items = {i["name"]: i["qty"] for i in self.pos.orders.items(b)}
        self.assertEqual(a_items, {"떡볶이": 2})
        self.assertEqual(b_items, {"떡볶이": 1, "콜라": 1})

    def test_locked_order_cannot_change(self):
        oid = self.new_order(떡볶이=2)
        self.pos.payments.pay(oid, "CARD", amount=1000)
        with self.assertRaises(PosError):
            self.pos.orders.add_item(oid, self.menu["콜라"])

    def test_kitchen_and_processing_time(self):
        oid = self.new_order(떡볶이=1, 라면=1)
        items = {i["name"]: i["id"] for i in self.pos.orders.items(oid)}
        self.clock.advance(minutes=5)
        self.pos.orders.complete_item(items["떡볶이"])
        self.assertIsNone(self.pos.orders.get(oid)["completed_at"])
        self.clock.advance(minutes=5)
        self.pos.orders.complete_item(items["라면"])
        self.assertIsNotNone(self.pos.orders.get(oid)["completed_at"])
        t = self.pos.stats.processing_times("2026-10-12", "2026-10-12")
        self.assertAlmostEqual(t["avg_order_min"], 10)
        avg = {m["name"]: m["avg_min"] for m in t["menus"]}
        self.assertAlmostEqual(avg["떡볶이"], 5)
        self.assertAlmostEqual(avg["라면"], 10)


class DiscountPaymentTest(PosTestCase):
    def test_cash_change(self):
        oid = self.new_order(떡볶이=1)  # 4,500원
        r = self.pos.payments.pay(oid, "CASH", received=10000)
        self.assertEqual(r["change"], 5500)
        self.assertTrue(r["completed"])
        self.assertEqual(self.pos.orders.get(oid)["status"], "PAID")

    def test_cash_insufficient(self):
        oid = self.new_order(떡볶이=1)
        with self.assertRaises(PosError):
            self.pos.payments.pay(oid, "CASH", received=4000)
        # 실패한 결제는 주문을 잠그지 않는다
        self.assertEqual(self.pos.orders.get(oid)["locked"], 0)

    def test_discount_order(self):
        self.clock.dt = datetime(2026, 10, 12, 15, 30)  # 월요일 15:30 → 시간대 할인 10%
        oid = self.new_order(참치김밥=2, 떡볶이=1)    # 9,000 + 4,500
        self.pos.payments.apply_coupon(oid, "WELCOME1000")
        c = self.pos.discount.calculate(oid)
        self.assertEqual(c["subtotal"], 13500)
        self.assertEqual(c["menu_discount"], 900)       # 참치김밥 10% × 2
        self.assertEqual(c["time_discount"], 1260)      # 12,600 × 10%
        self.assertEqual(c["coupon_discount"], 1000)
        self.assertEqual(c["total"], 13500 - 900 - 1260 - 1000)

    def test_time_discount_weekday_only(self):
        self.clock.dt = datetime(2026, 10, 17, 15, 30)  # 토요일
        oid = self.new_order(떡볶이=1)
        self.assertEqual(self.pos.discount.calculate(oid)["time_discount"], 0)

    def test_coupon_min_amount_and_expiry(self):
        oid = self.new_order(콜라=1)
        with self.assertRaises(PosError):
            self.pos.payments.apply_coupon(oid, "WELCOME1000")
        self.pos.discount.add_coupon("OLD1", "만료 쿠폰", "AMOUNT", 500, 0, "2026-10-01")
        with self.assertRaises(PosError):
            self.pos.payments.apply_coupon(oid, "OLD1")

    def test_points_earn_and_use(self):
        mid = self.pos.members.register("홍길동", "010-1234-5678")
        oid = self.new_order(쫄면=5)  # 30,000원
        self.pos.payments.attach_member(oid, mid)
        r = self.pos.payments.pay(oid, "CARD")
        self.assertEqual(r["earned_points"], 300)  # 일반 1%
        self.pos.members.adjust_points(mid, 900, "이벤트")
        self.pos.db.commit()
        oid2 = self.new_order(2, 떡볶이=1)
        self.pos.payments.attach_member(oid2, mid)
        with self.assertRaises(PosError):
            self.pos.payments.set_points(oid2, 500)  # 최소 1,000P
        self.pos.payments.set_points(oid2, 1200)
        self.assertEqual(self.pos.discount.calculate(oid2)["total"], 3300)
        self.pos.payments.pay(oid2, "CASH", received=3300)
        self.assertEqual(self.pos.members.get(mid)["points"], 1200 - 1200 + 33)

    def test_member_grade_rate(self):
        mid = self.pos.members.register("단골", "01099998888")
        self.pos.db.execute("UPDATE members SET total_spent=300000 WHERE id=?", (mid,))
        oid = self.new_order(떡볶이=2)
        self.pos.payments.attach_member(oid, mid)
        self.assertEqual(self.pos.payments.pay(oid, "CARD")["earned_points"], 180)  # 실버 2%

    def test_split_by_amount(self):
        oid = self.new_order(떡볶이=2)  # 9,000
        r1 = self.pos.payments.pay(oid, "CARD", amount=4000)
        self.assertFalse(r1["completed"])
        self.assertEqual(r1["remaining"], 5000)
        with self.assertRaises(PosError):
            self.pos.payments.pay(oid, "CARD", amount=6000)
        r2 = self.pos.payments.pay(oid, "EASY")
        self.assertTrue(r2["completed"])

    def test_split_by_menu(self):
        oid = self.new_order(떡볶이=1, 쫄면=1)
        items = {i["name"]: i["id"] for i in self.pos.orders.items(oid)}
        r1 = self.pos.payments.pay(oid, "CARD", item_qtys={items["쫄면"]: 1})
        self.assertEqual(r1["amount"], 6000)
        with self.assertRaises(PosError):  # 이미 결제한 메뉴는 다시 결제 불가
            self.pos.payments.pay(oid, "CARD", item_qtys={items["쫄면"]: 1})
        r2 = self.pos.payments.pay(oid, "CASH", item_qtys={items["떡볶이"]: 1}, received=5000)
        self.assertEqual(r2["amount"], 4500)
        self.assertTrue(r2["completed"])

    def test_refund_reflected_in_stats(self):
        mid = self.pos.members.register("환불러", "010-2222-3333")
        oid = self.new_order(쫄면=2)
        self.pos.payments.attach_member(oid, mid)
        self.pos.payments.pay(oid, "CARD")
        oid2 = self.new_order(2, 떡볶이=1)
        self.pos.payments.pay(oid2, "CASH")
        refunded = self.pos.payments.refund(oid, "단순 변심", restock=True)
        self.assertEqual(refunded, 12000)
        s = self.pos.stats.summary("2026-10-12", "2026-10-12")
        self.assertEqual(s["gross_sales"], 16500)
        self.assertEqual(s["refund_amount"], 12000)
        self.assertEqual(s["net_sales"], 4500)
        self.assertEqual(s["order_count"], 1)
        self.assertEqual(self.pos.members.get(mid)["points"], 0)
        menus = {m["name"] for m in self.pos.stats.menu_stats("2026-10-12", "2026-10-12")}
        self.assertNotIn("쫄면", menus)

    def test_cancel_partial_payment(self):
        oid = self.new_order(떡볶이=2)
        self.pos.payments.pay(oid, "CARD", amount=3000)
        self.pos.payments.refund(oid, "손님 퇴장")
        self.assertEqual(self.pos.orders.get(oid)["status"], "CANCELLED")
        self.assertIsNone(self.pos.orders.open_order_for_table(1))


class StatsTest(PosTestCase):
    def test_menu_rank_hourly_type_method(self):
        self.pos.payments.pay(self.new_order(1, 떡볶이=3), "CARD")
        self.clock.advance(hours=6)  # 18시
        self.pos.payments.pay(self.new_order(2, 쫄면=1, 떡볶이=1), "CASH")
        t = self.pos.orders.create_order(None, "TAKEOUT")
        self.pos.orders.add_item(t, self.menu["쫄면"], 2)
        self.pos.payments.pay(t, "EASY")
        d = "2026-10-12"
        by_qty = self.pos.stats.menu_stats(d, d, "qty")
        self.assertEqual(by_qty[0]["name"], "떡볶이")
        self.assertEqual(by_qty[0]["qty"], 4)
        by_rev = self.pos.stats.menu_stats(d, d, "revenue")
        self.assertEqual(by_rev[0]["name"], "떡볶이")  # 18,000 vs 18,000 → 동률이면 이름순
        h = self.pos.stats.hourly(d, d)
        self.assertEqual(h["peak_count_hour"], 18)
        types = {x["type"]: x for x in self.pos.stats.by_order_type(d, d)}
        self.assertEqual(types["TAKEOUT"]["sales"], 12000)
        methods = {x["method"]: x for x in self.pos.stats.by_payment_method(d, d)}
        self.assertAlmostEqual(sum(m["ratio"] for m in methods.values()), 100)
        s = self.pos.stats.summary(d, d)
        self.assertEqual(s["cost"], 1500 * 4 + 1900 * 3)
        self.assertEqual(s["profit"], s["sales"] - s["cost"])

    def test_trend_units(self):
        for day in range(3):
            self.clock.dt = datetime(2026, 10, 10 + day, 12, 0)
            self.pos.payments.pay(self.new_order(1, 떡볶이=1), "CARD")
        days = self.pos.stats.trend("2026-10-01", "2026-10-31", "day")
        self.assertEqual(len(days), 3)
        weeks = self.pos.stats.trend("2026-10-01", "2026-10-31", "week")
        self.assertEqual(len(weeks), 2)  # 10/10(토),10/11(일) | 10/12(월)
        months = self.pos.stats.trend("2026-10-01", "2026-10-31", "month")
        self.assertEqual(months[0]["sales"], 13500)

    def test_daily_closing(self):
        oid = self.new_order(떡볶이=1)
        with self.assertRaises(PosError):  # 미결제 주문 존재
            self.pos.reports.close_day("2026-10-12")
        self.pos.payments.pay(oid, "CASH")
        text, path = self.pos.reports.close_day("2026-10-12")
        self.assertIn("4,500원", text)
        self.assertTrue(os.path.exists(path))
        with self.assertRaises(PosError):
            self.pos.reports.close_day("2026-10-12")

    def test_receipt_modes(self):
        oid = self.new_order(떡볶이=1, 콜라=2)
        self.pos.payments.pay(oid, "CASH", received=10000)
        full = self.pos.reports.receipt(oid, detailed=True)
        short = self.pos.reports.receipt(oid, detailed=False)
        self.assertIn("콜라", full)
        self.assertNotIn("콜라", short)
        self.assertIn("8,500원", short)


class StaffTest(PosTestCase):
    def test_minimum_wage(self):
        with self.assertRaises(PosError):
            self.pos.staff.add_staff("저시급", "PART", hourly_wage=9000)

    def test_part_time_payroll_with_weekly_holiday(self):
        sid = self.pos.staff.add_staff("알바", "PART", hourly_wage=10000 + 400)
        # 2026-10-05(월)~09(금) 하루 4시간 → 주 20시간: 주휴수당 20/40×8×시급
        for d in range(5, 10):
            self.pos.staff.add_log(sid, f"2026-10-{d:02d} 10:00:00", f"2026-10-{d:02d} 14:00:00")
        p = {x["staff_id"]: x for x in self.pos.staff.payroll(2026, 10)}[sid]
        self.assertEqual(p["hours"], 20)
        self.assertEqual(p["base"], 208000)
        self.assertEqual(p["holiday_pay"], int(20 / 40 * 8 * 10400))
        self.assertEqual(p["days"], 5)

    def test_fulltime_overtime(self):
        sid = self.pos.staff.add_staff("정규", "FULL", monthly_salary=2_090_000 * 2)
        self.pos.staff.add_log(sid, "2026-10-05 09:00:00", "2026-10-05 19:00:00")  # 10시간
        p = {x["staff_id"]: x for x in self.pos.staff.payroll(2026, 10)}[sid]
        self.assertEqual(p["overtime_hours"], 2)
        self.assertEqual(p["overtime_pay"], int(2 * 20000 * 1.5))
        self.assertEqual(p["total"], 4_180_000 + 60000)

    def test_clock_in_out(self):
        sid = self.pos.staff.staff_list()[0]["id"]
        self.pos.staff.clock_in(sid)
        with self.assertRaises(PosError):
            self.pos.staff.clock_in(sid)
        self.clock.advance(hours=3, minutes=30)
        self.assertEqual(self.pos.staff.clock_out(sid), 3.5)

    def test_overlapping_log_rejected(self):
        sid = self.pos.staff.staff_list()[0]["id"]
        self.pos.staff.add_log(sid, "2026-10-05 09:00:00", "2026-10-05 13:00:00")
        with self.assertRaises(PosError):
            self.pos.staff.add_log(sid, "2026-10-05 12:00:00", "2026-10-05 15:00:00")


class ReservationAuthTest(PosTestCase):
    def test_reservation_flow(self):
        rid = self.pos.reservations.create(
            "김예약", "010-1111-0000", "2026-10-12 13:00", "TAKEOUT",
            {self.menu["김밥" if "김밥" in self.menu else "야채김밥"]: 3})
        self.assertEqual(self.pos.reservations.due(), [])
        self.clock.advance(minutes=52)  # 12:52 → 10분 전 알림 대상
        self.assertEqual(len(self.pos.reservations.due()), 1)
        oid, _ = self.pos.reservations.convert_to_order(rid)
        self.assertEqual(self.pos.orders.items(oid)[0]["qty"], 3)
        self.assertEqual(self.pos.reservations.get(rid)["status"], "ARRIVED")

    def test_reservation_too_soon(self):
        with self.assertRaises(PosError):
            self.pos.reservations.create("급함", "01011110000", "2026-10-12 12:10", "TAKEOUT",
                                         {self.menu["라면"]: 1})

    def test_admin_login_lock(self):
        t = [0.0]
        self.pos.auth._clock = lambda: t[0]
        for _ in range(3):
            with self.assertRaises(PosError):
                self.pos.auth.login("9999")
        with self.assertRaises(PosError):  # 잠김 상태에서는 맞는 비밀번호도 거부
            self.pos.auth.login("0000")
        t[0] = 31
        self.pos.auth.login("0000")
        self.assertTrue(self.pos.auth.is_admin)


if __name__ == "__main__":
    unittest.main()
