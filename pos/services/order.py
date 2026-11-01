"""주문 생성/변경/삭제, 테이블 병합·분할, 포장 주문, 주방(조리) 처리."""
from __future__ import annotations

from .. import config
from . import PosError, Service
from .menu import MenuService


class OrderService(Service):
    def __init__(self, db, menu: MenuService):
        super().__init__(db)
        self.menu = menu

    # ------------------------------------------------------------ 조회
    def get(self, order_id: int):
        row = self.db.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
        if not row:
            raise PosError("존재하지 않는 주문입니다.")
        return row

    def items(self, order_id: int):
        return self.db.query("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,))

    def open_order_for_table(self, table_id: int):
        return self.db.query_one(
            "SELECT * FROM orders WHERE table_id=? AND status='OPEN' ORDER BY id DESC LIMIT 1",
            (table_id,),
        )

    def open_takeout_orders(self):
        """미결제이거나, 결제했지만 아직 픽업하지 않은 포장 주문."""
        return self.db.query(
            "SELECT * FROM orders WHERE order_type='TAKEOUT' AND "
            "(status='OPEN' OR (status='PAID' AND picked_up=0)) ORDER BY id"
        )

    def table_status(self) -> dict[int, dict]:
        """테이블 id -> {order_id, total(할인 전), created_at, count}"""
        rows = self.db.query(
            "SELECT o.table_id, o.id, o.created_at, o.locked, "
            "COALESCE(SUM(i.unit_price*i.qty),0) AS amount, COALESCE(SUM(i.qty),0) AS cnt "
            "FROM orders o LEFT JOIN order_items i ON i.order_id=o.id "
            "WHERE o.status='OPEN' AND o.table_id IS NOT NULL GROUP BY o.id"
        )
        return {
            r["table_id"]: {"order_id": r["id"], "amount": r["amount"], "count": r["cnt"],
                            "created_at": r["created_at"], "locked": r["locked"]}
            for r in rows
        }

    def orders_between(self, start: str, end: str, status: str | None = None):
        sql = "SELECT * FROM orders WHERE substr(created_at,1,10) BETWEEN ? AND ?"
        params: list = [start, end]
        if status:
            sql += " AND status=?"
            params.append(status)
        return self.db.query(sql + " ORDER BY id DESC", params)

    # ------------------------------------------------------------ 생성
    def _next_order_no(self) -> int:
        today = self.db.now().strftime(config.DATE_FMT)
        return (self.db.scalar(
            "SELECT MAX(order_no) FROM orders WHERE substr(created_at,1,10)=?", (today,)
        ) or 0) + 1

    def create_order(self, table_id: int | None = None, order_type: str = "DINE_IN",
                     commit: bool = True) -> int:
        if order_type not in config.ORDER_TYPES:
            raise PosError("주문 유형이 올바르지 않습니다.")
        if order_type == "DINE_IN":
            if table_id is None:
                raise PosError("매장 주문은 테이블을 선택해야 합니다.")
            existing = self.open_order_for_table(table_id)
            if existing:
                return existing["id"]
            if not self.db.query_one("SELECT 1 FROM tables_ WHERE id=? AND active=1", (table_id,)):
                raise PosError("존재하지 않는 테이블입니다.")
        else:
            table_id = None
        cur = self.db.execute(
            "INSERT INTO orders(order_no, table_id, order_type, created_at) VALUES(?,?,?,?)",
            (self._next_order_no(), table_id, order_type, self.db.now_str()),
        )
        if commit:
            self.db.commit()
        return cur.lastrowid

    def _check_editable(self, order) -> None:
        if order["status"] != "OPEN":
            raise PosError("이미 결제 완료되었거나 취소된 주문입니다.")
        if order["locked"]:
            raise PosError("결제가 진행 중인 주문은 메뉴를 변경할 수 없습니다.")

    # ------------------------------------------------------------ 항목 추가/변경/삭제
    def add_item(self, order_id: int, menu_id: int, qty: int = 1, commit: bool = True,
                 option_ids=(), memo: str = "") -> list[str]:
        """메뉴(옵션·요청사항 포함)를 주문에 추가하고 재고 경고 메시지 목록을 반환한다.

        같은 메뉴라도 옵션이나 요청사항이 다르면 별도 항목으로 담는다.
        """
        order = self.get(order_id)
        self._check_editable(order)
        if not 1 <= qty <= config.MAX_ITEM_QTY:
            raise PosError(f"수량은 1~{config.MAX_ITEM_QTY} 사이여야 합니다.")
        menu = self.menu.get(menu_id)
        if not menu["active"]:
            raise PosError("판매 중지된 메뉴입니다.")
        if menu["manual_soldout"]:
            raise PosError(f"'{menu['name']}'은(는) 품절입니다.")
        memo = self._check_memo(memo)
        opts, opt_price, opt_label = self.menu.resolve_options(menu_id, option_ids)
        opt_key = ",".join(str(o["id"]) for o in opts)
        opt_cost = 0  # 옵션 원가는 재료 차감으로 관리, 판매 원가는 메뉴 원가 기준
        unit_price = menu["price"] + opt_price

        same = self.db.query_one(
            "SELECT * FROM order_items WHERE order_id=? AND menu_id=? AND status='WAITING' "
            "AND unit_price=? AND discount_rate=? AND option_ids=? AND memo=? AND paid_qty=0",
            (order_id, menu_id, unit_price, menu["discount_rate"], opt_key, memo),
        )
        if same and same["qty"] + qty > config.MAX_ITEM_QTY:
            raise PosError(f"한 메뉴의 수량은 최대 {config.MAX_ITEM_QTY}개입니다.")
        if not same and self.db.scalar(
            "SELECT COUNT(*) FROM order_items WHERE order_id=?", (order_id,)
        ) >= config.MAX_ORDER_ITEMS:
            raise PosError(f"한 주문에는 최대 {config.MAX_ORDER_ITEMS}개 항목까지 담을 수 있습니다.")

        warnings = self.menu.consume(menu_id, qty, f"주문 #{order_id}", [o["id"] for o in opts])
        if same:
            self.db.execute("UPDATE order_items SET qty=qty+? WHERE id=?", (qty, same["id"]))
        else:
            self.db.execute(
                "INSERT INTO order_items(order_id, menu_id, name, unit_price, unit_cost, "
                "discount_rate, qty, ordered_at, option_ids, options, memo) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (order_id, menu_id, menu["name"], unit_price, menu["cost"] + opt_cost,
                 menu["discount_rate"], qty, self.db.now_str(), opt_key, opt_label, memo),
            )
        self.db.execute("UPDATE orders SET completed_at=NULL WHERE id=?", (order_id,))
        if commit:
            self.db.commit()
        return warnings

    @staticmethod
    def _check_memo(memo: str) -> str:
        memo = " ".join((memo or "").split())
        if len(memo) > config.MAX_MEMO_LENGTH:
            raise PosError(f"요청사항은 {config.MAX_MEMO_LENGTH}자 이내로 입력하세요.")
        return memo

    def set_item_memo(self, item_id: int, memo: str) -> None:
        """요청사항 수정. 조리 시작 전 항목만 바꿀 수 있다."""
        item = self._item(item_id)
        self._check_editable(self.get(item["order_id"]))
        if item["status"] != "WAITING":
            raise PosError("조리가 시작된 메뉴는 요청사항을 바꿀 수 없습니다.")
        self.db.execute("UPDATE order_items SET memo=? WHERE id=?",
                        (self._check_memo(memo), item_id))
        self.db.commit()

    @staticmethod
    def item_label(item) -> str:
        """화면·영수증용 메뉴 표시: 이름 [옵션] (요청사항)"""
        text = item["name"]
        if item["options"]:
            text += f" [{item['options']}]"
        return text

    @staticmethod
    def option_ids_of(item) -> list[int]:
        return [int(x) for x in item["option_ids"].split(",") if x]

    def _item(self, item_id: int):
        row = self.db.query_one("SELECT * FROM order_items WHERE id=?", (item_id,))
        if not row:
            raise PosError("존재하지 않는 주문 항목입니다.")
        return row

    def set_item_qty(self, item_id: int, qty: int) -> list[str]:
        """수량 변경. 0이면 삭제. 조리 전(WAITING) 항목만 감소분 재고가 복구된다."""
        item = self._item(item_id)
        self._check_editable(self.get(item["order_id"]))
        if qty < 0 or qty > config.MAX_ITEM_QTY:
            raise PosError(f"수량은 0~{config.MAX_ITEM_QTY} 사이여야 합니다.")
        diff = qty - item["qty"]
        warnings: list[str] = []
        if diff > 0:
            warnings = self.menu.consume(item["menu_id"], diff, f"주문 #{item['order_id']} 수량 변경",
                                         self.option_ids_of(item))
        elif diff < 0 and item["status"] == "WAITING":
            self.menu.consume(item["menu_id"], diff, f"주문 #{item['order_id']} 수량 변경",
                              self.option_ids_of(item))
        if qty == 0:
            self.db.execute("DELETE FROM order_items WHERE id=?", (item_id,))
        else:
            self.db.execute("UPDATE order_items SET qty=? WHERE id=?", (qty, item_id))
        self._refresh_completed(item["order_id"])
        self.db.commit()
        return warnings

    def remove_item(self, item_id: int) -> None:
        self.set_item_qty(item_id, 0)

    def cancel_order(self, order_id: int, reason: str = "고객 요청") -> None:
        """결제 전 주문 전체 취소. 조리 전 항목의 재고를 복구한다."""
        order = self.get(order_id)
        self._check_editable(order)
        for item in self.items(order_id):
            if item["status"] == "WAITING":
                self.menu.consume(item["menu_id"], -item["qty"], f"주문 #{order_id} 취소",
                                  self.option_ids_of(item))
        if not self.items(order_id):
            # 아무것도 담지 않은 주문은 기록하지 않는다.
            self.db.execute("DELETE FROM orders WHERE id=?", (order_id,))
        else:
            calc_total = self.db.scalar(
                "SELECT COALESCE(SUM(unit_price*qty),0) FROM order_items WHERE order_id=?", (order_id,)
            )
            self.db.execute(
                "UPDATE orders SET status='CANCELLED', cancelled_at=?, cancel_reason=?, subtotal=?, "
                "total=? WHERE id=?",
                (self.db.now_str(), reason, calc_total, calc_total, order_id),
            )
            self.db.log("주문 취소", f"#{order_id} {reason}")
        self.db.commit()

    def discard_if_empty(self, order_id: int) -> None:
        """항목이 하나도 없는 미결제 주문을 정리한다(테이블을 비움)."""
        order = self.db.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
        if order and order["status"] == "OPEN" and not order["locked"] and not self.items(order_id):
            self.db.execute("DELETE FROM orders WHERE id=?", (order_id,))
            self.db.commit()

    # ------------------------------------------------------------ 테이블 이동/병합/분할
    def merge_tables(self, src_table_id: int, dst_table_id: int) -> int:
        """src 테이블 주문을 dst 테이블로 합친다. dst가 비어있으면 단순 이동. 대상 주문 id 반환."""
        if src_table_id == dst_table_id:
            raise PosError("같은 테이블입니다.")
        src = self.open_order_for_table(src_table_id)
        if not src:
            raise PosError("원본 테이블에 주문이 없습니다.")
        self._check_editable(src)
        dst = self.open_order_for_table(dst_table_id)
        if not dst:
            self.db.execute("UPDATE orders SET table_id=? WHERE id=?", (dst_table_id, src["id"]))
            self.db.log("테이블 이동", f"{src_table_id} -> {dst_table_id}")
            self.db.commit()
            return src["id"]
        self._check_editable(dst)
        self.db.execute("UPDATE order_items SET order_id=? WHERE order_id=?", (dst["id"], src["id"]))
        # 회원/쿠폰 정보가 대상에 없으면 이어받는다.
        self.db.execute(
            "UPDATE orders SET member_id=COALESCE(member_id, ?), coupon_code=COALESCE(coupon_code, ?), "
            "created_at=MIN(created_at, ?), completed_at=NULL WHERE id=?",
            (src["member_id"], src["coupon_code"], src["created_at"], dst["id"]),
        )
        self.db.execute("DELETE FROM orders WHERE id=?", (src["id"],))
        self._refresh_completed(dst["id"])
        self.db.log("테이블 병합", f"{src_table_id} -> {dst_table_id}")
        self.db.commit()
        return dst["id"]

    def split_items(self, src_order_id: int, moves: dict[int, int], dst_table_id: int) -> int:
        """선택한 항목(item_id -> 옮길 수량)을 다른 테이블 주문으로 분리한다."""
        src = self.get(src_order_id)
        self._check_editable(src)
        if src["table_id"] == dst_table_id:
            raise PosError("같은 테이블로는 분리할 수 없습니다.")
        moves = {k: v for k, v in moves.items() if v > 0}
        if not moves:
            raise PosError("옮길 메뉴를 선택하세요.")
        try:
            dst_id = self._split_into(src_order_id, moves, dst_table_id)
        except Exception:
            self.db.rollback()
            raise
        self.db.log("테이블 분할", f"#{src_order_id} -> 테이블 {dst_table_id}")
        self.db.commit()
        return dst_id

    def _split_into(self, src_order_id: int, moves: dict[int, int], dst_table_id: int) -> int:
        dst_id = self.create_order(dst_table_id, "DINE_IN", commit=False)
        self._check_editable(self.get(dst_id))
        for item_id, qty in moves.items():
            item = self._item(item_id)
            if item["order_id"] != src_order_id or qty > item["qty"]:
                raise PosError("옮길 수량이 올바르지 않습니다.")
            if qty == item["qty"]:
                self.db.execute("UPDATE order_items SET order_id=? WHERE id=?", (dst_id, item_id))
            else:
                self.db.execute("UPDATE order_items SET qty=qty-?, ticket_qty=MAX(0, ticket_qty-?) "
                                "WHERE id=?", (qty, qty, item_id))
                self.db.execute(
                    "INSERT INTO order_items(order_id, menu_id, name, unit_price, unit_cost, "
                    "discount_rate, qty, status, ordered_at, done_at, option_ids, options, memo, "
                    "ticket_qty) "
                    "SELECT ?, menu_id, name, unit_price, unit_cost, discount_rate, ?, status, "
                    "ordered_at, done_at, option_ids, options, memo, MIN(ticket_qty, ?) "
                    "FROM order_items WHERE id=?",
                    (dst_id, qty, qty, item_id),
                )
        if not self.items(src_order_id):
            self.db.execute("DELETE FROM orders WHERE id=?", (src_order_id,))
        else:
            self._refresh_completed(src_order_id)
        self._refresh_completed(dst_id)
        return dst_id

    # ------------------------------------------------------------ 주방
    def kitchen_queue(self):
        return self.db.query(
            "SELECT i.*, o.order_no, o.order_type, o.table_id, t.name AS table_name "
            "FROM order_items i JOIN orders o ON o.id=i.order_id "
            "LEFT JOIN tables_ t ON t.id=o.table_id "
            "WHERE i.status<>'DONE' AND o.status IN ('OPEN','PAID') ORDER BY i.ordered_at, i.id"
        )

    def start_cooking(self, item_id: int) -> None:
        item = self._item(item_id)
        if item["status"] != "WAITING":
            raise PosError("대기 중인 항목만 조리를 시작할 수 있습니다.")
        # 주방에서 처리한 항목은 주방에 전달된 것으로 본다(주문서를 안 뽑았어도 "미전송"으로 남지 않게)
        self.db.execute("UPDATE order_items SET status='COOKING', ticket_qty=qty WHERE id=?",
                        (item_id,))
        self.db.commit()

    def complete_item(self, item_id: int) -> None:
        item = self._item(item_id)
        if item["status"] == "DONE":
            return
        self.db.execute("UPDATE order_items SET status='DONE', done_at=?, ticket_qty=qty WHERE id=?",
                        (self.db.now_str(), item_id))
        self._refresh_completed(item["order_id"])
        self.db.commit()

    def _refresh_completed(self, order_id: int) -> None:
        """모든 항목이 조리 완료되면 주문 완료 시각을 기록한다."""
        remain = self.db.scalar(
            "SELECT COUNT(*) FROM order_items WHERE order_id=? AND status<>'DONE'", (order_id,)
        )
        total = self.db.scalar("SELECT COUNT(*) FROM order_items WHERE order_id=?", (order_id,))
        if total and not remain:
            last_done = self.db.scalar(
                "SELECT MAX(done_at) FROM order_items WHERE order_id=?", (order_id,)
            )
            self.db.execute(
                "UPDATE orders SET completed_at=COALESCE(completed_at, ?) WHERE id=?",
                (last_done, order_id),
            )
        else:
            self.db.execute("UPDATE orders SET completed_at=NULL WHERE id=?", (order_id,))

    def pending_ticket_lines(self, order_id: int) -> list[tuple]:
        """주방에 아직 전달하지 않은 변경분: (항목, 추가 수량(+) 또는 취소 수량(-))."""
        return [(it, it["qty"] - it["ticket_qty"]) for it in self.items(order_id)
                if it["qty"] != it["ticket_qty"]]

    def kitchen_ticket(self, order_id: int, mark_sent: bool = True) -> str:
        """주방 주문서 텍스트(마지막 출력 이후 추가·취소된 수량만). 출력할 내용이 없으면 오류."""
        order = self.get(order_id)
        lines = self.pending_ticket_lines(order_id)
        # 항목을 삭제한 경우(주문서는 나갔는데 항목이 사라짐)는 추적하지 않는다.
        if not lines:
            raise PosError("주방에 새로 보낼 메뉴가 없습니다.")
        first = not any(it["ticket_qty"] for it in self.items(order_id))
        out = ["=" * 32, ("[주방 주문서]" if first else "[추가 주문서]").center(28),
               "=" * 32, self.label(order), f"출력 {self.db.now_str()[5:16]}", "-" * 32]
        for it, diff in lines:
            mark = f"+{diff}" if diff > 0 else f"취소 {-diff}"
            width = sum(2 if ord(c) > 0x2E80 else 1 for c in it["name"])
            out.append(it["name"] + " " * max(1, 24 - width) + f"{mark:>8}")
            if it["options"]:
                out.append(f"  - {it['options']}")
            if it["memo"]:
                out.append(f"  * 요청: {it['memo']}")
        out.append("=" * 32)
        if mark_sent:
            self.db.execute("UPDATE order_items SET ticket_qty=qty WHERE order_id=?", (order_id,))
            self.db.commit()
        return "\n".join(out)

    def mark_picked_up(self, order_id: int) -> None:
        order = self.get(order_id)
        if order["order_type"] != "TAKEOUT":
            raise PosError("포장 주문이 아닙니다.")
        if order["status"] != "PAID":
            raise PosError("결제가 완료되어야 픽업 처리할 수 있습니다.")
        self.db.execute("UPDATE orders SET picked_up=1 WHERE id=?", (order_id,))
        self.db.commit()

    def label(self, order) -> str:
        if order["order_type"] == "TAKEOUT":
            return f"포장 #{order['order_no']}"
        name = self.db.scalar("SELECT name FROM tables_ WHERE id=?", (order["table_id"],))
        return f"테이블 {name or '?'} (#{order['order_no']})"
