"""영수증, 일일 마감 리포트, CSV 내보내기, DB 백업."""
from __future__ import annotations

import csv
import os
import sqlite3

from .. import config
from . import PosError, Service
from .discount import DiscountService
from .stats import StatsService

WIDTH = 40


def _line(left: str, right: str = "", width: int = WIDTH) -> str:
    """한글(2칸) 폭을 고려한 좌우 정렬."""
    def w(s: str) -> int:
        return sum(2 if ord(c) > 0x2E80 else 1 for c in s)
    gap = max(1, width - w(left) - w(right))
    return left + " " * gap + right


def _won(n: int) -> str:
    return f"{n:,}원"


class ReportService(Service):
    def __init__(self, db, discount: DiscountService, stats: StatsService, out_dir: str = "."):
        super().__init__(db)
        self.discount = discount
        self.stats = stats
        self.out_dir = out_dir

    def _save(self, sub: str, filename: str, text: str) -> str:
        folder = os.path.join(self.out_dir, sub)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    # ------------------------------------------------------------ 영수증
    def receipt(self, order_id: int, detailed: bool = True) -> str:
        """detailed=True: 모든 메뉴가 기록된 영수증, False: 총액만 기록된 간이 영수증."""
        order = self.db.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
        if not order:
            raise PosError("존재하지 않는 주문입니다.")
        calc = self.discount.calculate(order_id)
        items = self.db.query("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,))
        payments = self.db.query("SELECT * FROM payments WHERE order_id=? ORDER BY id", (order_id,))
        table = self.db.scalar("SELECT name FROM tables_ WHERE id=?", (order["table_id"],))
        bar = "=" * WIDTH
        dash = "-" * WIDTH
        out = [bar, f"{config.STORE_NAME} ({config.STORE_SUBTITLE})".center(WIDTH - 4),
               f"사업자 {config.BUSINESS_NO}  TEL {config.STORE_TEL}",
               config.STORE_ADDRESS, bar,
               f"주문번호 : {order['order_no']:03d}  [{config.ORDER_TYPES[order['order_type']]}]"
               + (f" 테이블 {table}" if table else ""),
               f"주문일시 : {order['created_at']}"]
        if order["paid_at"]:
            out.append(f"결제일시 : {order['paid_at']}")
        if order["status"] == "REFUNDED":
            out.append(f"** 환불 완료 ({order['cancelled_at']}) **")
        out.append(dash)
        if detailed:
            out.append(_line("메뉴", "단가   수량        금액"))
            out.append(dash)
            for it in items:
                amt = it["unit_price"] * it["qty"]
                out.append(_line(it["name"], f"{it['unit_price']:>6,} {it['qty']:>4} {amt:>11,}"))
                dc = self.discount.item_discount(it["unit_price"], it["discount_rate"]) * it["qty"]
                if dc:
                    out.append(_line(f"  └ 메뉴할인 {it['discount_rate']}%", f"-{dc:,}"))
            out.append(dash)
            out.append(_line("합계(할인 전)", _won(calc["subtotal"])))
            if calc["menu_discount"]:
                out.append(_line("메뉴 할인", "-" + _won(calc["menu_discount"])))
            if calc["time_discount"]:
                out.append(_line(f"시간대 할인 {calc['time_discount_name'] or ''}".strip(),
                                 "-" + _won(calc["time_discount"])))
            if calc["coupon_discount"]:
                out.append(_line(f"쿠폰 할인({order['coupon_code']})",
                                 "-" + _won(calc["coupon_discount"])))
            if calc["points_used"]:
                out.append(_line("포인트 사용", "-" + _won(calc["points_used"])))
            out.append(dash)
        out.append(_line("결제 금액", _won(calc["total"])))
        vat = calc["total"] - round(calc["total"] / 1.1)
        out.append(_line("  (부가세 포함)", _won(vat)))
        if detailed and payments:
            out.append(dash)
            for p in payments:
                label = config.PAYMENT_METHODS[p["method"]]
                status = " [환불]" if p["refunded_at"] else ""
                out.append(_line(f"{label}{status}", _won(p["amount"])))
                if p["method"] == "CASH":
                    out.append(_line("  받은 금액", _won(p["received"])))
                    out.append(_line("  거스름돈", _won(p["change"])))
        if order["member_id"]:
            m = self.db.query_one("SELECT * FROM members WHERE id=?", (order["member_id"],))
            out.append(dash)
            out.append(_line(f"회원 {m['name']} ({m['phone'][-4:]})",
                             f"적립 {order['points_earned']:,}P"))
            out.append(_line("잔여 포인트", f"{m['points']:,}P"))
        out += [bar, "이용해 주셔서 감사합니다!".center(WIDTH - 10), bar]
        return "\n".join(out)

    def save_receipt(self, order_id: int, detailed: bool = True) -> str:
        text = self.receipt(order_id, detailed)
        kind = "full" if detailed else "total"
        return self._save("receipts", f"receipt_{order_id}_{kind}.txt", text)

    # ------------------------------------------------------------ 일일 마감
    def closing_text(self, business_date: str) -> str:
        r = self.stats.daily_report(business_date)
        bar, dash = "=" * WIDTH, "-" * WIDTH
        out = [bar, f"{config.STORE_NAME} 일일 마감 보고서".center(WIDTH - 8),
               f"영업일 : {business_date}", f"출력   : {self.db.now_str()}", bar,
               _line("총 주문 건수(접수)", f"{r['all_orders']}건"),
               _line("판매 완료 건수", f"{r['order_count']}건"),
               _line("총 결제액", _won(r["gross_sales"])),
               _line("할인 합계", _won(r["discount"])),
               _line("포인트 사용", _won(r["points_used"])),
               dash, "[결제수단별]"]
        for m in r["methods"]:
            out.append(_line(f"  {m['label']} ({m['count']}건)",
                             f"{_won(m['amount'])} ({m['ratio']:.1f}%)"))
        out += [dash, "[주문유형별]"]
        for t in r["types"]:
            out.append(_line(f"  {t['label']} ({t['count']}건)", _won(t["sales"])))
        out += [dash, "[취소/환불]",
                _line(f"  주문 취소 ({r['cancel_count']}건)", _won(r["cancel_amount"])),
                _line(f"  환불 ({r['refund_count']}건)",
                      ("-" if r["refund_amount"] else "") + _won(r["refund_amount"])),
                dash,
                _line("순매출 (결제-환불)", _won(r["net_sales"])),
                _line("원가 합계", _won(r["cost"])),
                _line("순이익 (판매액-원가)", _won(r["profit"])),
                dash, "[판매 TOP 5]"]
        for m in r["top_menus"]:
            out.append(_line(f"  {m['rank']}. {m['name']}", f"{m['qty']}개 {_won(m['revenue'])}"))
        if not r["top_menus"]:
            out.append("  (판매 내역 없음)")
        out.append(bar)
        return "\n".join(out)

    def is_closed(self, business_date: str) -> bool:
        return bool(self.db.query_one("SELECT 1 FROM closings WHERE business_date=?",
                                      (business_date,)))

    def close_day(self, business_date: str) -> tuple[str, str]:
        """일일 마감. 미결제 주문이 남아있으면 마감할 수 없다. (보고서, 저장 경로) 반환."""
        if self.is_closed(business_date):
            raise PosError(f"{business_date}는 이미 마감되었습니다. (재출력만 가능)")
        open_n = self.db.scalar(
            "SELECT COUNT(*) FROM orders WHERE status='OPEN' AND substr(created_at,1,10)<=?",
            (business_date,))
        if open_n:
            raise PosError(f"미결제 주문이 {open_n}건 남아있어 마감할 수 없습니다.")
        text = self.closing_text(business_date)
        self.db.execute("INSERT INTO closings(business_date, closed_at, report) VALUES(?,?,?)",
                        (business_date, self.db.now_str(), text))
        self.db.log("일일 마감", business_date)
        self.db.commit()
        return text, self._save("reports", f"closing_{business_date}.txt", text)

    def closings(self):
        return self.db.query("SELECT * FROM closings ORDER BY business_date DESC")

    # ------------------------------------------------------------ 내보내기 / 백업
    def export_csv(self, filename: str, headers: list[str], rows: list[list]) -> str:
        folder = os.path.join(self.out_dir, "exports")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:  # 엑셀 한글 호환
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        return path

    def backup(self) -> str:
        folder = os.path.join(self.out_dir, "backups")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"pos_{self.db.now().strftime('%Y%m%d_%H%M%S')}.db")
        dst = sqlite3.connect(path)
        with dst:
            self.db.conn.backup(dst)
        dst.close()
        self.db.log("DB 백업", path)
        self.db.commit()
        return path
