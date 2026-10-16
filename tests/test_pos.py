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
        self.pos.cash.open(100_000, "테스트")  # 현금 결제는 시재 개시가 필요

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
        with self.assertRaises(PosError):  # 시재 정산 전
            self.pos.reports.close_day("2026-10-12")
        self.pos.cash.close({50000: 2, 1000: 4, 500: 1})  # 100,000 + 4,500
        text, path = self.pos.reports.close_day("2026-10-12")
        self.assertIn("[시재 정산]", text)
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


class NewFeatureTest(PosTestCase):
    def opt(self, menu, name):
        return next(o["id"] for o in self.pos.menu.options(self.menu[menu]) if o["name"] == name)

    # ---- 계좌이체
    def test_transfer_payment_in_stats_and_receipt(self):
        oid = self.new_order(떡볶이=1)
        self.pos.payments.pay(oid, "TRANSFER")
        d = "2026-10-12"
        m = {x["method"]: x for x in self.pos.stats.by_payment_method(d, d)}
        self.assertEqual(m["TRANSFER"]["amount"], 4500)
        self.assertIn("계좌이체", self.pos.reports.receipt(oid))
        self.assertIn("계좌이체", self.pos.reports.closing_text(d))

    # ---- 메뉴 옵션 / 요청사항
    def test_option_price_and_stock(self):
        cheese = self.stock("치즈")
        oid = self.pos.orders.create_order(1)
        self.pos.orders.add_item(oid, self.menu["떡볶이"], 2,
                                 option_ids=[self.opt("떡볶이", "치즈 추가"), self.opt("떡볶이", "매운맛")])
        it = self.pos.orders.items(oid)[0]
        self.assertEqual(it["unit_price"], 5000)
        self.assertEqual(it["options"], "매운맛, 치즈 추가")
        self.assertEqual(self.stock("치즈"), cheese - 2)
        self.pos.orders.set_item_qty(it["id"], 1)          # 감소 시 옵션 재료도 복구
        self.assertEqual(self.stock("치즈"), cheese - 1)
        self.pos.orders.cancel_order(oid)
        self.assertEqual(self.stock("치즈"), cheese)

    def test_option_group_is_exclusive(self):
        oid = self.pos.orders.create_order(1)
        with self.assertRaises(PosError):
            self.pos.orders.add_item(oid, self.menu["떡볶이"], 1, option_ids=[
                self.opt("떡볶이", "순한맛"), self.opt("떡볶이", "매운맛")])

    def test_different_options_or_memo_are_separate_lines(self):
        oid = self.pos.orders.create_order(1)
        self.pos.orders.add_item(oid, self.menu["라면"], 1)
        self.pos.orders.add_item(oid, self.menu["라면"], 1, option_ids=[self.opt("라면", "곱빼기")])
        self.pos.orders.add_item(oid, self.menu["라면"], 1, memo="파 빼주세요")
        self.pos.orders.add_item(oid, self.menu["라면"], 1)  # 첫 줄에 합산
        qtys = sorted(i["qty"] for i in self.pos.orders.items(oid))
        self.assertEqual(qtys, [1, 1, 2])

    def test_memo_rules(self):
        oid = self.new_order(라면=1)
        it = self.pos.orders.items(oid)[0]
        self.pos.orders.set_item_memo(it["id"], "  덜   맵게 ")
        self.assertEqual(self.pos.orders.items(oid)[0]["memo"], "덜 맵게")
        with self.assertRaises(PosError):
            self.pos.orders.set_item_memo(it["id"], "가" * 41)
        self.pos.orders.start_cooking(it["id"])
        with self.assertRaises(PosError):
            self.pos.orders.set_item_memo(it["id"], "변경")

    def test_option_soldout_when_option_ingredient_out(self):
        self.pos.db.execute("UPDATE ingredients SET stock=0 WHERE id=?", (self.ing["치즈"],))
        oid = self.pos.orders.create_order(1)
        with self.assertRaises(PosError):
            self.pos.orders.add_item(oid, self.menu["떡볶이"], 1,
                                     option_ids=[self.opt("떡볶이", "치즈 추가")])
        self.pos.orders.add_item(oid, self.menu["떡볶이"], 1)  # 옵션 없이는 주문 가능

    def test_option_admin_validation(self):
        with self.assertRaises(PosError):
            self.pos.menu.add_option(self.menu["라면"], "황금", 150)  # 100원 단위 아님
        oid = self.pos.menu.add_option(self.menu["라면"], "떡 추가", 1000, "", self.ing["떡"], 1)
        self.pos.menu.delete_option(oid)
        self.assertNotIn(oid, [o["id"] for o in self.pos.menu.options(self.menu["라면"])])

    # ---- 주방 주문서
    def test_kitchen_ticket_only_new_items(self):
        oid = self.new_order(떡볶이=2)
        t1 = self.pos.orders.kitchen_ticket(oid)
        self.assertIn("[주방 주문서]", t1)
        self.assertIn("+2", t1)
        with self.assertRaises(PosError):
            self.pos.orders.kitchen_ticket(oid)
        self.pos.orders.add_item(oid, self.menu["콜라"], 1, memo="얼음컵")
        item = next(i for i in self.pos.orders.items(oid) if i["name"] == "떡볶이")
        self.pos.orders.set_item_qty(item["id"], 1)
        t2 = self.pos.orders.kitchen_ticket(oid)
        self.assertIn("[추가 주문서]", t2)
        self.assertIn("콜라", t2)
        self.assertIn("얼음컵", t2)
        self.assertIn("취소 1", t2)

    def test_printer_disabled_saves_file(self):
        self.pos.printer.set_enabled(False)
        r = self.pos.printer.print_text("테스트", "t1")
        self.assertFalse(r["printed"])
        self.assertTrue(os.path.exists(r["path"]))

    def test_printer_backend_called_or_fails_gracefully(self):
        sent = []
        self.pos.printer.backend = sent.append
        self.assertTrue(self.pos.printer.print_text("x", "t2")["printed"])
        self.assertEqual(len(sent), 1)

        def broken(path):
            raise OSError("프린터 없음")
        self.pos.printer.backend = broken
        r = self.pos.printer.print_text("x", "t3")
        self.assertFalse(r["printed"])
        self.assertIn("프린터 없음", r["message"])

    # ---- 부분 환불
    def test_partial_refund(self):
        mid = self.pos.members.register("부분", "010-4444-5555")
        oid = self.new_order(쫄면=2, 떡볶이=1)  # 16,500
        self.pos.payments.attach_member(oid, mid)
        self.pos.payments.pay(oid, "CARD")       # 적립 165P
        items = {i["name"]: i["id"] for i in self.pos.orders.items(oid)}
        amt = self.pos.payments.refund_items(oid, {items["쫄면"]: 1}, "하나 안 나옴", restock=True)
        self.assertEqual(amt, 6000)
        self.assertEqual(self.pos.orders.get(oid)["status"], "PAID")
        d = "2026-10-12"
        s = self.pos.stats.summary(d, d)
        self.assertEqual(s["refund_amount"], 6000)
        self.assertEqual(s["net_sales"], 10500)
        self.assertEqual(s["sales"], 10500)
        self.assertEqual(s["cost"], 1900 + 1500)
        qty = {m["name"]: m["qty"] for m in self.pos.stats.menu_stats(d, d)}
        self.assertEqual(qty["쫄면"], 1)
        self.assertEqual(self.pos.members.get(mid)["points"], 165 - int(165 * 6000 / 16500))
        with self.assertRaises(PosError):  # 이미 환불한 수량 초과
            self.pos.payments.refund_items(oid, {items["쫄면"]: 2}, "x")
        self.assertIn("부분 환불", self.pos.reports.receipt(oid))
        # 남은 메뉴를 모두 환불하면 전체 환불로 전환
        rest = self.pos.payments.refund_items(oid, {items["쫄면"]: 1, items["떡볶이"]: 1}, "전부")
        self.assertEqual(rest, 10500)
        self.assertEqual(self.pos.orders.get(oid)["status"], "REFUNDED")
        self.assertEqual(self.pos.stats.summary(d, d)["refund_amount"], 16500)
        self.assertEqual(self.pos.members.get(mid)["points"], 0)

    def test_partial_refund_across_methods(self):
        oid = self.new_order(쫄면=2)  # 12,000
        self.pos.payments.pay(oid, "CASH", amount=2000)
        self.pos.payments.pay(oid, "CARD")
        it = self.pos.orders.items(oid)[0]
        self.pos.payments.refund_items(oid, {it["id"]: 1}, "변심", method="CASH")
        by = {r["method"]: r["amount"] for r in self.pos.payments.refunds(oid)}
        self.assertEqual(by, {"CASH": 2000, "CARD": 4000})
        m = {x["method"]: x for x in self.pos.stats.by_payment_method("2026-10-12", "2026-10-12")}
        self.assertEqual(m["CASH"]["amount"], 0)
        self.assertEqual(m["CARD"]["amount"], 6000)

    # ---- 기간 비교
    def test_compare_periods(self):
        from pos.services.stats import previous_range
        self.assertEqual(previous_range("2026-10-01", "2026-10-31"), ("2026-09-01", "2026-09-30"))
        self.assertEqual(previous_range("2026-01-01", "2026-12-31"), ("2025-01-01", "2025-12-31"))
        self.assertEqual(previous_range("2026-10-12", "2026-10-18"), ("2026-10-05", "2026-10-11"))
        self.assertEqual(previous_range("2026-10-01", "2026-10-14"), ("2026-09-01", "2026-09-14"))
        self.assertEqual(previous_range("2026-03-01", "2026-03-31"), ("2026-02-01", "2026-02-28"))
        self.assertEqual(previous_range("2026-03-01", "2026-03-30"), ("2026-02-01", "2026-02-28"))
        self.clock.dt = datetime(2026, 10, 5, 12, 0)
        self.pos.payments.pay(self.new_order(1, 떡볶이=1), "CARD")
        self.clock.dt = datetime(2026, 10, 12, 12, 0)
        self.pos.payments.pay(self.new_order(1, 떡볶이=2), "CARD")
        c = self.pos.stats.compare("2026-10-12", "2026-10-18")
        net = next(m for m in c["metrics"] if m["key"] == "net_sales")
        self.assertEqual((net["current"], net["previous"], net["diff"]), (9000, 4500, 4500))
        self.assertAlmostEqual(net["rate"], 100.0)
        self.assertEqual(c["menus"][0]["qty_diff"], 1)

    # ---- 복원
    def test_backup_and_restore(self):
        path = self.pos.reports.backup()
        self.pos.members.register("복원후사라짐", "010-9090-9090")
        safety = self.pos.reports.restore(path)
        self.assertTrue(os.path.exists(safety))
        self.assertEqual(self.pos.members.members("복원후사라짐"), [])
        bad = os.path.join(self.tmp.name, "bad.db")
        with open(bad, "w") as f:
            f.write("not a db")
        with self.assertRaises(PosError):
            self.pos.reports.restore(bad)

    def test_migration_of_old_db(self):
        import sqlite3
        from pos.db import Database
        path = os.path.join(self.tmp.name, "old.db")
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE order_items (id INTEGER PRIMARY KEY, order_id INTEGER, menu_id INTEGER,
                name TEXT, unit_price INTEGER, unit_cost INTEGER, discount_rate INTEGER, qty INTEGER,
                paid_qty INTEGER, status TEXT, ordered_at TEXT, done_at TEXT);
        """)
        con.commit()
        con.close()
        db = Database(path)
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(order_items)")}
        self.assertTrue({"options", "memo", "refunded_qty", "ticket_qty"} <= cols)
        db.close()



class CashTest(PosTestCase):
    def test_cash_payment_requires_open_session(self):
        self.pos.cash.close({50000: 2})
        oid = self.new_order(떡볶이=1)
        with self.assertRaises(PosError):
            self.pos.payments.pay(oid, "CASH")
        self.pos.payments.pay(oid, "CARD")  # 카드는 시재와 무관

    def test_only_one_open_session_and_amount_rules(self):
        with self.assertRaises(PosError):
            self.pos.cash.open(50_000)
        self.pos.cash.close({50000: 2})
        for bad in (-10, 105, 2_000_000):
            with self.assertRaises(PosError):
                self.pos.cash.open(bad)
        self.assertEqual(self.pos.cash.default_opening(), 100_000)  # 직전 실제 시재 이월

    def test_expected_cash_calculation(self):
        oid = self.new_order(쫄면=2)                       # 12,000 현금
        self.pos.payments.pay(oid, "CASH", received=20_000)  # 거스름돈 8,000 → 순 12,000
        oid2 = self.new_order(2, 떡볶이=1)
        self.pos.payments.pay(oid2, "CARD")               # 카드는 제외
        self.pos.cash.add_movement("IN", 30_000, "잔돈 보충")
        self.pos.cash.add_movement("OUT", 5_000, "대파 구입")
        it = self.pos.orders.items(oid)[0]
        self.pos.payments.refund_items(oid, {it["id"]: 1}, "변심", method="CASH")  # -6,000
        s = self.pos.cash.summary()
        self.assertEqual((s["opening"], s["cash_sales"], s["cash_refunds"], s["cash_in"], s["cash_out"]),
                         (100_000, 12_000, 6_000, 30_000, 5_000))
        self.assertEqual(s["expected"], 100_000 + 12_000 - 6_000 + 30_000 - 5_000)

    def test_movement_rules(self):
        with self.assertRaises(PosError):
            self.pos.cash.add_movement("OUT", 5_000, "")          # 사유 필수
        with self.assertRaises(PosError):
            self.pos.cash.add_movement("OUT", 200_000, "은행 입금")  # 금전함보다 많이 출금 불가
        with self.assertRaises(PosError):
            self.pos.cash.add_movement("IN", 0, "x")

    def test_check_records_difference_and_keeps_session_open(self):
        r = self.pos.cash.check({50000: 1, 10000: 4, 5000: 1, 1000: 4})  # 99,000
        self.assertEqual(r["difference"], -1_000)
        self.assertIsNotNone(self.pos.cash.current())
        self.assertEqual(self.pos.cash.checks(r["session_id"])[0]["kind"], "CHECK")

    def test_close_requires_reason_and_admin_for_large_difference(self):
        with self.assertRaises(PosError):  # 과부족 사유 없음
            self.pos.cash.close({50000: 1, 10000: 4, 5000: 1, 1000: 4})
        with self.assertRaises(PosError):  # 1만원 이상 차이는 관리자만
            self.pos.cash.close({50000: 1, 10000: 3}, note="모름")
        self.pos.auth.login("0000")
        r = self.pos.cash.close({50000: 1, 10000: 3}, note="거스름돈 실수")
        self.assertEqual(r["difference"], -20_000)
        s = self.pos.cash.get(r["session_id"])
        self.assertEqual((s["status"], s["counted_cash"], s["difference"]), ("CLOSED", 80_000, -20_000))
        self.assertIn("과부족", self.pos.cash.report_text(r["session_id"]))

    def test_close_exact_without_reason(self):
        r = self.pos.cash.close({50000: 2})
        self.assertEqual(r["difference"], 0)
        with self.assertRaises(PosError):  # 정산 후 입출금 불가
            self.pos.cash.add_movement("IN", 1_000, "x")

    def test_count_rejects_bad_denominations(self):
        with self.assertRaises(PosError):
            self.pos.cash.count_total({3000: 1})
        with self.assertRaises(PosError):
            self.pos.cash.count_total({1000: -1})
        self.assertEqual(self.pos.cash.count_total({50000: 1, 500: 3, 10: 2}), 51_520)

    def test_cash_refund_needs_session_and_enough_cash(self):
        oid = self.new_order(쫄면=2)
        self.pos.payments.pay(oid, "CASH")
        self.pos.cash.add_movement("OUT", 110_000, "은행 입금")  # 금전함 2,000원 남음
        with self.assertRaises(PosError):
            self.pos.payments.refund(oid, "변심")
        self.pos.cash.add_movement("IN", 20_000, "잔돈 보충")
        self.assertEqual(self.pos.payments.refund(oid, "변심"), 12_000)
        self.pos.auth.login("0000")
        self.pos.cash.close({10000: 1}, note="")  # 예상 10,000 = 실제
        oid2 = self.new_order(2, 떡볶이=1)
        self.pos.cash.open(10_000)
        self.pos.payments.pay(oid2, "CASH")
        self.pos.cash.close({10000: 1, 1000: 4, 500: 1})
        with self.assertRaises(PosError):  # 시재 없이 현금 환불 불가
            self.pos.payments.refund(oid2, "변심")


if __name__ == "__main__":
    unittest.main()
