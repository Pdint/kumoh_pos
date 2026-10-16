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
    def __init__(self, db, discount: DiscountService, stats: StatsService, out_dir: str = ".",
                 cash=None):
        super().__init__(db)
        self.cash = cash
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
                if it["options"]:
                    out.append(f"  + {it['options']}")
                dc = self.discount.item_discount(it["unit_price"], it["discount_rate"]) * it["qty"]
                if dc:
                    out.append(_line(f"  └ 메뉴할인 {it['discount_rate']}%", f"-{dc:,}"))
                if it["refunded_qty"] and order["status"] == "PAID":
                    out.append(f"  └ 환불 {it['refunded_qty']}개")
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
                elif p["method"] == "TRANSFER":
                    out.append(f"  입금 계좌 {config.STORE_BANK_ACCOUNT}")
        refunds = self.db.query("SELECT * FROM refunds WHERE order_id=? AND partial=1 ORDER BY id",
                                (order_id,))
        if refunds and order["status"] == "PAID":
            out.append(dash)
            for r in refunds:
                out.append(_line(f"부분 환불({config.PAYMENT_METHODS[r['method']]})",
                                 "-" + _won(r["amount"])))
                out.append(f"  {r['detail']} / {r['reason']} ({r['at'][5:16]})")
            out.append(_line("환불 후 결제 금액", _won(calc["total"] - order["refunded_amount"])))
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
        if self.cash is not None:
            out += [dash, "[시재 정산]"]
            sessions = self.cash.day_summary(business_date)
            for c in sessions:
                out.append(f"  {c['opened_at'][11:16]}~{c['closed_at'][11:16]} {c['opened_by'] or ''}".rstrip())
                out.append(_line("    개시 금액", _won(c["opening"])))
                out.append(_line("    현금 매출", f"+{_won(c['cash_sales'])}"))
                if c["cash_refunds"]:
                    out.append(_line("    현금 환불", f"-{_won(c['cash_refunds'])}"))
                if c["cash_in"] or c["cash_out"]:
                    out.append(_line("    입금 / 출금", f"+{c['cash_in']:,} / -{c['cash_out']:,}"))
                out.append(_line("    예상 / 실제", f"{c['expected']:,} / {c['counted']:,}"))
                out.append(_line("    과부족", f"{c['difference']:+,}원"))
            if sessions:
                out.append(_line("  과부족 합계", f"{sum(c['difference'] for c in sessions):+,}원"))
            else:
                out.append("  (정산된 시재 없음)")
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
        if self.cash is not None and self.cash.current():
            raise PosError("시재가 정산되지 않았습니다. '시재' 탭에서 시재 정산을 먼저 하세요.")
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

    def backups(self) -> list[str]:
        folder = os.path.join(self.out_dir, "backups")
        if not os.path.isdir(folder):
            return []
        return sorted((os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".db")),
                      reverse=True)

    def backup(self, label: str = "") -> str:
        folder = os.path.join(self.out_dir, "backups")
        os.makedirs(folder, exist_ok=True)
        stamp = self.db.now().strftime('%Y%m%d_%H%M%S')
        path = os.path.join(folder, f"pos_{stamp}{('_' + label) if label else ''}.db")
        n = 1
        while os.path.exists(path):  # 같은 초에 여러 번 백업하는 경우
            n += 1
            path = os.path.join(folder, f"pos_{stamp}{('_' + label) if label else ''}_{n}.db")
        dst = sqlite3.connect(path)
        with dst:
            self.db.conn.backup(dst)
        dst.close()
        self.db.log("DB 백업", path)
        self.db.commit()
        return path

    REQUIRED_TABLES = {"menus", "orders", "order_items", "payments", "ingredients", "settings"}

    def restore(self, path: str) -> str:
        """백업 파일로 현재 DB를 되돌린다. 복원 전에 현재 상태를 자동 백업하고 그 경로를 반환한다."""
        if not os.path.isfile(path):
            raise PosError("백업 파일을 찾을 수 없습니다.")
        try:
            src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            ok = src.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        except sqlite3.DatabaseError:
            raise PosError("올바른 POS 백업 파일이 아닙니다.") from None
        if not ok or not self.REQUIRED_TABLES <= tables:
            src.close()
            raise PosError("올바른 POS 백업 파일이 아니거나 손상된 파일입니다.")
        safety = self.backup("before_restore")
        try:
            self.db.commit()
            src.backup(self.db.conn)
        finally:
            src.close()
        self.db.migrate()  # 이전 버전 백업이면 새 컬럼 추가
        self.db.log("DB 복원", f"{path} (복원 전 백업: {safety})")
        self.db.commit()
        return safety
