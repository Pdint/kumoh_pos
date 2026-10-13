"""예약 주문."""
from __future__ import annotations

from datetime import datetime, timedelta

from .. import config
from . import PosError, Service
from .member import normalize_phone
from .order import OrderService

STATUS = {"WAITING": "대기", "ARRIVED": "주문전환", "CANCELLED": "취소", "NO_SHOW": "노쇼"}


class ReservationService(Service):
    def __init__(self, db, orders: OrderService):
        super().__init__(db)
        self.orders = orders

    def reservations(self, date: str | None = None, status: str | None = None):
        sql = "SELECT r.*, t.name AS table_name FROM reservations r " \
              "LEFT JOIN tables_ t ON t.id=r.table_id WHERE 1=1"
        params: list = []
        if date:
            sql += " AND substr(r.reserve_at,1,10)=?"
            params.append(date)
        if status:
            sql += " AND r.status=?"
            params.append(status)
        return self.db.query(sql + " ORDER BY r.reserve_at", params)

    def items(self, reservation_id: int):
        return self.db.query(
            "SELECT ri.*, m.name, m.price FROM reservation_items ri "
            "JOIN menus m ON m.id=ri.menu_id WHERE ri.reservation_id=?",
            (reservation_id,),
        )

    def get(self, reservation_id: int):
        row = self.db.query_one("SELECT * FROM reservations WHERE id=?", (reservation_id,))
        if not row:
            raise PosError("존재하지 않는 예약입니다.")
        return row

    def create(self, customer_name: str, phone: str, reserve_at: str, order_type: str,
               items: dict[int, int], table_id: int | None = None, note: str = "") -> int:
        if not customer_name.strip():
            raise PosError("예약자 이름을 입력하세요.")
        phone = normalize_phone(phone)
        try:
            when = datetime.strptime(reserve_at, "%Y-%m-%d %H:%M")
        except ValueError:
            raise PosError("예약 시각은 YYYY-MM-DD HH:MM 형식으로 입력하세요.") from None
        if when < self.db.now() + timedelta(minutes=config.RESERVATION_MIN_LEAD_MINUTES):
            raise PosError(f"예약은 현재로부터 {config.RESERVATION_MIN_LEAD_MINUTES}분 이후로만 가능합니다.")
        if order_type not in config.ORDER_TYPES:
            raise PosError("주문 유형이 올바르지 않습니다.")
        if order_type == "DINE_IN":
            if table_id is None:
                raise PosError("매장 예약은 테이블을 지정해야 합니다.")
            # 같은 테이블 ±1시간 이내 중복 예약 금지
            lo = (when - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
            hi = (when + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
            if self.db.query_one(
                "SELECT 1 FROM reservations WHERE table_id=? AND status='WAITING' "
                "AND reserve_at > ? AND reserve_at < ?", (table_id, lo, hi)
            ):
                raise PosError("해당 테이블은 앞뒤 1시간 이내에 다른 예약이 있습니다.")
        else:
            table_id = None
        items = {k: v for k, v in items.items() if v > 0}
        if not items:
            raise PosError("예약할 메뉴를 하나 이상 선택하세요.")
        if any(q > config.MAX_ITEM_QTY for q in items.values()):
            raise PosError(f"수량은 최대 {config.MAX_ITEM_QTY}개입니다.")
        cur = self.db.execute(
            "INSERT INTO reservations(customer_name, phone, reserve_at, order_type, table_id, "
            "note, created_at) VALUES(?,?,?,?,?,?,?)",
            (customer_name.strip(), phone, reserve_at, order_type, table_id, note.strip(),
             self.db.now_str()),
        )
        for menu_id, qty in items.items():
            self.db.execute(
                "INSERT INTO reservation_items(reservation_id, menu_id, qty) VALUES(?,?,?)",
                (cur.lastrowid, menu_id, qty),
            )
        self.db.log("예약 등록", f"{customer_name} {reserve_at}")
        self.db.commit()
        return cur.lastrowid

    def due(self) -> list:
        """알림 대상: 예약 시각 N분 전부터 지난 대기 예약."""
        limit = (self.db.now() + timedelta(minutes=config.RESERVATION_ALERT_MINUTES)).strftime(
            "%Y-%m-%d %H:%M")
        return self.db.query(
            "SELECT * FROM reservations WHERE status='WAITING' AND reserve_at <= ? "
            "ORDER BY reserve_at", (limit,)
        )

    def set_status(self, reservation_id: int, status: str) -> None:
        res = self.get(reservation_id)
        if res["status"] != "WAITING":
            raise PosError("대기 중인 예약만 변경할 수 있습니다.")
        if status not in ("CANCELLED", "NO_SHOW"):
            raise PosError("변경할 수 없는 상태입니다.")
        self.db.execute("UPDATE reservations SET status=? WHERE id=?", (status, reservation_id))
        self.db.commit()

    def convert_to_order(self, reservation_id: int) -> tuple[int, list[str]]:
        """예약을 실제 주문으로 전환한다(이 시점에 재고 차감). (주문 id, 재고 경고) 반환."""
        res = self.get(reservation_id)
        if res["status"] != "WAITING":
            raise PosError("대기 중인 예약만 주문으로 전환할 수 있습니다.")
        if res["order_type"] == "DINE_IN" and self.orders.open_order_for_table(res["table_id"]):
            raise PosError("예약 테이블에 진행 중인 주문이 있습니다. 테이블을 먼저 비워주세요.")
        warnings: list[str] = []
        try:
            order_id = self.orders.create_order(res["table_id"], res["order_type"], commit=False)
            for it in self.items(reservation_id):
                warnings += self.orders.add_item(order_id, it["menu_id"], it["qty"], commit=False)
            self.db.execute("UPDATE orders SET reservation_id=? WHERE id=?",
                            (reservation_id, order_id))
            self.db.execute("UPDATE reservations SET status='ARRIVED', order_id=? WHERE id=?",
                            (order_id, reservation_id))
        except Exception:
            self.db.rollback()
            raise
        self.db.commit()
        return order_id, warnings
