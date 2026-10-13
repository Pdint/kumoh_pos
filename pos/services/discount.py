"""할인(메뉴별 / 시간대별 / 쿠폰) 관리와 주문 금액 계산."""
from __future__ import annotations

import re
from datetime import datetime

from .. import config
from . import PosError, Service, floor_unit

WEEKDAY_NAMES = "월화수목금토일"


class DiscountService(Service):
    # ------------------------------------------------------------ 시간대 할인
    def time_discounts(self, include_inactive: bool = True):
        sql = "SELECT * FROM time_discounts"
        if not include_inactive:
            sql += " WHERE active=1"
        return self.db.query(sql + " ORDER BY start_time")

    @staticmethod
    def _check_hhmm(value: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value.strip()):
            raise PosError("시간은 HH:MM 형식(00:00~23:59)으로 입력하세요.")
        return value.strip()

    def add_time_discount(self, name: str, start: str, end: str, rate: int,
                          weekdays: str = "0123456") -> int:
        start, end = self._check_hhmm(start), self._check_hhmm(end)
        if start >= end:
            raise PosError("종료 시간은 시작 시간보다 늦어야 합니다.")
        if not 1 <= rate <= config.MAX_TIME_DISCOUNT_RATE:
            raise PosError(f"시간대 할인율은 1~{config.MAX_TIME_DISCOUNT_RATE}% 사이여야 합니다.")
        weekdays = "".join(sorted(set(weekdays) & set("0123456")))
        if not weekdays:
            raise PosError("적용 요일을 하나 이상 선택하세요.")
        cur = self.db.execute(
            "INSERT INTO time_discounts(name, start_time, end_time, rate, weekdays) VALUES(?,?,?,?,?)",
            (name.strip() or f"{start}~{end} 할인", start, end, rate, weekdays),
        )
        self.db.log("시간대 할인 추가", f"{start}~{end} {rate}%")
        self.db.commit()
        return cur.lastrowid

    def set_time_discount_active(self, td_id: int, active: bool) -> None:
        self.db.execute("UPDATE time_discounts SET active=? WHERE id=?", (int(active), td_id))
        self.db.commit()

    def delete_time_discount(self, td_id: int) -> None:
        self.db.execute("DELETE FROM time_discounts WHERE id=?", (td_id,))
        self.db.commit()

    def active_time_discount(self, at: datetime):
        """해당 시각에 적용되는 시간대 할인 중 할인율이 가장 큰 것(중복 적용 불가)."""
        hhmm = at.strftime("%H:%M")
        weekday = str(at.weekday())
        best = None
        for td in self.time_discounts(include_inactive=False):
            if weekday in td["weekdays"] and td["start_time"] <= hhmm < td["end_time"]:
                if best is None or td["rate"] > best["rate"]:
                    best = td
        return best

    # ------------------------------------------------------------ 쿠폰
    def coupons(self):
        return self.db.query("SELECT * FROM coupons ORDER BY code")

    def add_coupon(self, code: str, name: str, kind: str, value: int, min_amount: int = 0,
                   expires: str | None = None, max_uses: int = 0) -> None:
        code = code.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{4,20}", code):
            raise PosError("쿠폰 코드는 영문 대문자/숫자 4~20자입니다.")
        if self.db.query_one("SELECT 1 FROM coupons WHERE code=?", (code,)):
            raise PosError("이미 존재하는 쿠폰 코드입니다.")
        if kind not in ("RATE", "AMOUNT"):
            raise PosError("쿠폰 종류가 올바르지 않습니다.")
        if kind == "RATE" and not 1 <= value <= 50:
            raise PosError("정률 쿠폰의 할인율은 1~50% 입니다.")
        if kind == "AMOUNT" and (value <= 0 or value % 100):
            raise PosError("정액 쿠폰 금액은 100원 단위 양수입니다.")
        if min_amount < 0 or max_uses < 0:
            raise PosError("최소 금액/사용 횟수는 0 이상이어야 합니다.")
        if expires:
            try:
                datetime.strptime(expires, config.DATE_FMT)
            except ValueError:
                raise PosError("만료일은 YYYY-MM-DD 형식입니다.") from None
        self.db.execute(
            "INSERT INTO coupons(code, name, kind, value, min_amount, expires, max_uses) "
            "VALUES(?,?,?,?,?,?,?)",
            (code, name.strip() or code, kind, value, min_amount, expires or None, max_uses),
        )
        self.db.log("쿠폰 추가", code)
        self.db.commit()

    def set_coupon_active(self, code: str, active: bool) -> None:
        self.db.execute("UPDATE coupons SET active=? WHERE code=?", (int(active), code))
        self.db.commit()

    def delete_coupon(self, code: str) -> None:
        self.db.execute("DELETE FROM coupons WHERE code=?", (code,))
        self.db.commit()

    def check_coupon(self, code: str, base_amount: int):
        """쿠폰 사용 가능 여부를 검사하고 쿠폰 행을 반환한다."""
        row = self.db.query_one("SELECT * FROM coupons WHERE code=?", (code.strip().upper(),))
        if not row or not row["active"]:
            raise PosError("사용할 수 없는 쿠폰입니다.")
        if row["expires"] and row["expires"] < self.db.now().strftime(config.DATE_FMT):
            raise PosError(f"만료된 쿠폰입니다. (만료일 {row['expires']})")
        if row["max_uses"] and row["used_count"] >= row["max_uses"]:
            raise PosError("사용 횟수가 모두 소진된 쿠폰입니다.")
        if base_amount < row["min_amount"]:
            raise PosError(f"{row['min_amount']:,}원 이상 주문 시 사용 가능한 쿠폰입니다.")
        return row

    @staticmethod
    def coupon_amount(coupon, base_amount: int) -> int:
        if coupon["kind"] == "RATE":
            return floor_unit(base_amount * coupon["value"] / 100, config.DISCOUNT_UNIT)
        return min(coupon["value"], base_amount)

    # ------------------------------------------------------------ 금액 계산
    @staticmethod
    def item_discount(unit_price: int, rate: int) -> int:
        """메뉴 1개당 할인액."""
        return floor_unit(unit_price * rate / 100, config.DISCOUNT_UNIT)

    def calculate(self, order_id: int) -> dict:
        """주문 금액 내역. 결제가 시작된(잠긴) 주문은 저장된 금액을 그대로 사용한다.

        할인 적용 순서: 메뉴별 할인 → 시간대 할인 → 쿠폰 → 포인트
        """
        order = self.db.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
        if not order:
            raise PosError("존재하지 않는 주문입니다.")
        paid = self.db.scalar(
            "SELECT COALESCE(SUM(amount),0) FROM payments WHERE order_id=? AND refunded_at IS NULL",
            (order_id,),
        )
        result = {
            "order_id": order_id, "warnings": [], "time_discount_name": None,
            "coupon_name": None, "paid": paid,
        }
        if order["locked"]:
            result.update(
                subtotal=order["subtotal"], menu_discount=order["menu_discount"],
                time_discount=order["time_discount"], coupon_discount=order["coupon_discount"],
                points_used=order["points_used"], total=order["total"],
            )
            if order["coupon_code"]:
                result["coupon_name"] = order["coupon_code"]
            if order["time_discount"]:
                td = self.active_time_discount(
                    datetime.strptime(order["created_at"], config.DATETIME_FMT))
                if td:
                    result["time_discount_name"] = f"{td['name']}({td['rate']}%)"
            result["remaining"] = max(0, order["total"] - paid)
            result["before_discount"] = order["subtotal"]
            return result

        items = self.db.query("SELECT * FROM order_items WHERE order_id=?", (order_id,))
        subtotal = sum(i["unit_price"] * i["qty"] for i in items)
        menu_discount = sum(self.item_discount(i["unit_price"], i["discount_rate"]) * i["qty"]
                            for i in items)
        amount = subtotal - menu_discount

        time_discount = 0
        created = datetime.strptime(order["created_at"], config.DATETIME_FMT)
        td = self.active_time_discount(created)
        if td and amount > 0:
            time_discount = floor_unit(amount * td["rate"] / 100, config.DISCOUNT_UNIT)
            result["time_discount_name"] = f"{td['name']}({td['rate']}%)"
        amount -= time_discount

        coupon_discount = 0
        if order["coupon_code"]:
            try:
                coupon = self.check_coupon(order["coupon_code"], amount)
                coupon_discount = self.coupon_amount(coupon, amount)
                result["coupon_name"] = coupon["name"]
            except PosError as e:
                result["warnings"].append(f"쿠폰 미적용: {e}")
        amount -= coupon_discount

        points_used = min(order["points_used"], amount)
        amount -= points_used

        result.update(
            subtotal=subtotal, menu_discount=menu_discount, time_discount=time_discount,
            coupon_discount=coupon_discount, points_used=points_used, total=amount,
            before_discount=subtotal, remaining=max(0, amount - paid),
        )
        return result
