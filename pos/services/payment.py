"""결제(단일/분할), 회원·쿠폰·포인트 적용, 결제 취소/환불."""
from __future__ import annotations

from .. import config
from . import PosError, Service
from .discount import DiscountService
from .member import MemberService, grade_of
from .menu import MenuService


class PaymentService(Service):
    def __init__(self, db, menu: MenuService, discount: DiscountService, member: MemberService):
        super().__init__(db)
        self.menu = menu
        self.discount = discount
        self.member = member

    def _order(self, order_id: int):
        row = self.db.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
        if not row:
            raise PosError("존재하지 않는 주문입니다.")
        return row

    def _check_unlocked(self, order) -> None:
        if order["status"] != "OPEN":
            raise PosError("결제할 수 없는 주문 상태입니다.")
        if order["locked"]:
            raise PosError("결제가 시작된 이후에는 회원/쿠폰/포인트를 변경할 수 없습니다.")

    def payments(self, order_id: int):
        return self.db.query("SELECT * FROM payments WHERE order_id=? ORDER BY id", (order_id,))

    # ------------------------------------------------------------ 결제 전 설정
    def attach_member(self, order_id: int, member_id: int | None) -> None:
        order = self._order(order_id)
        self._check_unlocked(order)
        if member_id is not None:
            self.member.get(member_id)
        self.db.execute("UPDATE orders SET member_id=?, points_used=0 WHERE id=?",
                        (member_id, order_id))
        self.db.commit()

    def apply_coupon(self, order_id: int, code: str | None) -> None:
        order = self._order(order_id)
        self._check_unlocked(order)
        if code:
            calc = self.discount.calculate(order_id)
            base = calc["subtotal"] - calc["menu_discount"] - calc["time_discount"]
            code = self.discount.check_coupon(code, base)["code"]
        self.db.execute("UPDATE orders SET coupon_code=?, points_used=0 WHERE id=?",
                        (code or None, order_id))
        self.db.commit()

    def set_points(self, order_id: int, points: int) -> None:
        order = self._order(order_id)
        self._check_unlocked(order)
        if points and not order["member_id"]:
            raise PosError("포인트는 회원만 사용할 수 있습니다.")
        self.db.execute("UPDATE orders SET points_used=0 WHERE id=?", (order_id,))
        payable = self.discount.calculate(order_id)["total"]
        if points:
            member = self.member.get(order["member_id"])
            self.member.validate_point_use(points, member["points"], payable)
        self.db.execute("UPDATE orders SET points_used=? WHERE id=?", (points, order_id))
        self.db.commit()

    # ------------------------------------------------------------ 결제
    def _lock(self, order_id: int) -> None:
        """첫 결제 시 금액을 확정(스냅샷)하고 쿠폰·포인트를 차감한다."""
        order = self._order(order_id)
        calc = self.discount.calculate(order_id)
        coupon = order["coupon_code"] if calc["coupon_discount"] > 0 else None
        self.db.execute(
            "UPDATE orders SET locked=1, subtotal=?, menu_discount=?, time_discount=?, "
            "coupon_discount=?, points_used=?, total=?, coupon_code=? WHERE id=?",
            (calc["subtotal"], calc["menu_discount"], calc["time_discount"],
             calc["coupon_discount"], calc["points_used"], calc["total"], coupon, order_id),
        )
        if coupon:
            self.db.execute("UPDATE coupons SET used_count=used_count+1 WHERE code=?", (coupon,))
        if calc["points_used"]:
            self.member.adjust_points(order["member_id"], -calc["points_used"], "포인트 사용",
                                      order_id)

    def split_amount_for_items(self, order_id: int, item_qtys: dict[int, int]) -> int:
        """메뉴별 분할 결제 금액. 주문 단위 할인(시간대·쿠폰·포인트)은 금액 비율로 나눈다."""
        calc = self.discount.calculate(order_id)
        items = {i["id"]: i for i in self.db.query(
            "SELECT * FROM order_items WHERE order_id=?", (order_id,))}
        net_total = calc["subtotal"] - calc["menu_discount"]
        selected = 0
        all_paid_after = True
        for item_id, item in items.items():
            qty = item_qtys.get(item_id, 0)
            if qty < 0 or qty > item["qty"] - item["paid_qty"]:
                raise PosError(f"'{item['name']}'의 결제 수량이 올바르지 않습니다.")
            unit_net = item["unit_price"] - self.discount.item_discount(
                item["unit_price"], item["discount_rate"])
            selected += unit_net * qty
            if item["paid_qty"] + qty < item["qty"]:
                all_paid_after = False
        if not any(item_qtys.values()):
            raise PosError("결제할 메뉴를 선택하세요.")
        if all_paid_after:
            return calc["remaining"]
        ratio = calc["total"] / net_total if net_total else 0
        # 10원 단위 반올림(마지막 결제가 끝전을 정산)
        return min(calc["remaining"], round(selected * ratio / 10) * 10)

    def pay(self, order_id: int, method: str, amount: int | None = None,
            received: int | None = None, item_qtys: dict[int, int] | None = None,
            memo: str = "") -> dict:
        """결제 1건 처리. amount 생략 시 남은 금액 전액. item_qtys 지정 시 메뉴별 분할 결제."""
        order = self._order(order_id)
        if order["status"] != "OPEN":
            raise PosError("이미 결제 완료되었거나 취소된 주문입니다.")
        if not self.db.scalar("SELECT COUNT(*) FROM order_items WHERE order_id=?", (order_id,)):
            raise PosError("주문 항목이 없습니다.")
        if method not in config.PAYMENT_METHODS:
            raise PosError("결제 수단이 올바르지 않습니다.")
        n_payments = self.db.scalar(
            "SELECT COUNT(*) FROM payments WHERE order_id=? AND refunded_at IS NULL", (order_id,))
        if n_payments >= config.MAX_SPLIT_COUNT:
            raise PosError(f"분할 결제는 최대 {config.MAX_SPLIT_COUNT}회까지 가능합니다.")

        try:
            if item_qtys:
                amount = self.split_amount_for_items(order_id, item_qtys)
            if not order["locked"]:
                self._lock(order_id)
            remaining = self.discount.calculate(order_id)["remaining"]
            if amount is None:
                amount = remaining
            if amount < 0 or amount > remaining or (amount == 0 and remaining > 0):
                raise PosError(f"결제 금액은 1원 이상, 남은 금액({remaining:,}원) 이하여야 합니다.")
            change = 0
            if method == "CASH":
                received = amount if received is None else received
                if received < amount:
                    raise PosError(f"받은 금액이 부족합니다. ({amount - received:,}원 부족)")
                change = received - amount
            else:
                received = amount
            if item_qtys:
                for item_id, qty in item_qtys.items():
                    if qty:
                        self.db.execute("UPDATE order_items SET paid_qty=paid_qty+? WHERE id=?",
                                        (qty, item_id))
            payment_id = self.db.execute(
                "INSERT INTO payments(order_id, method, amount, received, change, paid_at, memo) "
                "VALUES(?,?,?,?,?,?,?)",
                (order_id, method, amount, received, change, self.db.now_str(), memo),
            ).lastrowid
            remaining -= amount
            earned = self._finalize(order_id) if remaining == 0 else 0
        except Exception:
            self.db.rollback()
            raise
        self.db.commit()
        return {"payment_id": payment_id, "amount": amount, "change": change,
                "remaining": remaining, "completed": remaining == 0, "earned_points": earned}

    def _finalize(self, order_id: int) -> int:
        order = self._order(order_id)
        paid = self.db.scalar(
            "SELECT COALESCE(SUM(amount),0) FROM payments WHERE order_id=? AND refunded_at IS NULL",
            (order_id,))
        earned = 0
        if order["member_id"]:
            member = self.member.get(order["member_id"])
            _, rate = grade_of(member["total_spent"])
            earned = int(paid * rate / 100)
            if earned:
                self.member.adjust_points(order["member_id"], earned, "구매 적립", order_id)
            self.db.execute("UPDATE members SET total_spent=total_spent+? WHERE id=?",
                            (paid, order["member_id"]))
        self.db.execute(
            "UPDATE orders SET status='PAID', paid_at=?, points_earned=? WHERE id=?",
            (self.db.now_str(), earned, order_id),
        )
        self.db.log("결제 완료", f"#{order_id} {order['total']:,}원")
        return earned

    # ------------------------------------------------------------ 취소 / 환불
    def refund(self, order_id: int, reason: str, restock: bool = False) -> int:
        """결제 완료 주문의 환불(→REFUNDED) 또는 부분 결제 중 주문의 결제 취소(→CANCELLED).

        환불액을 반환한다. 환불 내역은 환불 시각 기준으로 매출 통계에 반영된다.
        """
        order = self._order(order_id)
        if order["status"] == "PAID":
            new_status = "REFUNDED"
        elif order["status"] == "OPEN" and order["locked"]:
            new_status = "CANCELLED"
        else:
            raise PosError("환불할 수 있는 주문이 아닙니다. (미결제 주문은 '주문 취소'를 사용하세요)")
        if not reason.strip():
            raise PosError("환불 사유를 입력하세요.")
        now = self.db.now_str()
        try:
            refunded = self.db.scalar(
                "SELECT COALESCE(SUM(amount),0) FROM payments "
                "WHERE order_id=? AND refunded_at IS NULL", (order_id,))
            self.db.execute(
                "UPDATE payments SET refunded_at=? WHERE order_id=? AND refunded_at IS NULL",
                (now, order_id))
            if order["member_id"]:
                member = self.member.get(order["member_id"])
                if order["points_earned"]:
                    take = min(order["points_earned"], member["points"])
                    if take:
                        self.member.adjust_points(order["member_id"], -take, "환불 적립 취소",
                                                  order_id)
                if order["points_used"]:
                    self.member.adjust_points(order["member_id"], order["points_used"],
                                              "환불 포인트 복원", order_id)
                if new_status == "REFUNDED":
                    self.db.execute(
                        "UPDATE members SET total_spent=MAX(0, total_spent-?) WHERE id=?",
                        (refunded, order["member_id"]))
            if order["coupon_code"]:
                self.db.execute(
                    "UPDATE coupons SET used_count=MAX(0, used_count-1) WHERE code=?",
                    (order["coupon_code"],))
            if restock:
                for item in self.db.query("SELECT * FROM order_items WHERE order_id=?", (order_id,)):
                    self.menu.consume(item["menu_id"], -item["qty"], f"주문 #{order_id} 환불 재입고")
            self.db.execute(
                "UPDATE orders SET status=?, cancelled_at=?, cancel_reason=? WHERE id=?",
                (new_status, now, reason.strip(), order_id))
            self.db.log("환불" if new_status == "REFUNDED" else "결제 취소",
                        f"#{order_id} {refunded:,}원 ({reason})")
        except Exception:
            self.db.rollback()
            raise
        self.db.commit()
        return refunded
