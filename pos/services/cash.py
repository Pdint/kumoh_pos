"""시재(금전함 현금) 관리: 개시 → 입출금 → 중간 점검 → 정산(마감).

예상 시재 = 개시 금액 + 현금 매출 − 현금 환불 + 입금 − 출금
- 현금 결제·현금 환불은 시재가 개시된 상태에서만 가능하며, 해당 시재에 기록된다.
- 현금 매출은 결제 금액(받은 돈 − 거스름돈)이므로 거스름돈은 따로 계산하지 않는다.
"""
from __future__ import annotations

import json

from .. import config
from . import PosError, Service

KINDS = {"IN": "입금", "OUT": "출금"}
CHECK_KINDS = {"CHECK": "중간 점검", "CLOSE": "정산"}


class CashService(Service):
    # ------------------------------------------------------------ 조회
    def current(self):
        """개시된(정산 전) 시재. 없으면 None."""
        return self.db.query_one("SELECT * FROM cash_sessions WHERE status='OPEN' ORDER BY id DESC LIMIT 1")

    def require_open(self, action: str = "현금 거래") -> int:
        cur = self.current()
        if not cur:
            raise PosError(f"시재가 개시되지 않아 {action}를 할 수 없습니다. '시재' 탭에서 먼저 시재를 개시하세요.")
        return cur["id"]

    def get(self, session_id: int):
        row = self.db.query_one("SELECT * FROM cash_sessions WHERE id=?", (session_id,))
        if not row:
            raise PosError("존재하지 않는 시재입니다.")
        return row

    def sessions(self, start: str, end: str):
        return self.db.query(
            "SELECT * FROM cash_sessions WHERE substr(opened_at,1,10) BETWEEN ? AND ? "
            "OR substr(closed_at,1,10) BETWEEN ? AND ? ORDER BY id DESC", (start, end, start, end))

    def movements(self, session_id: int):
        return self.db.query("SELECT * FROM cash_movements WHERE session_id=? ORDER BY id", (session_id,))

    def checks(self, session_id: int):
        return self.db.query("SELECT * FROM cash_checks WHERE session_id=? ORDER BY id", (session_id,))

    def default_opening(self) -> int:
        """다음 개시 기본값: 직전 정산 때 실제로 센 현금(이월). 없으면 기본 준비금."""
        last = self.db.query_one(
            "SELECT counted_cash FROM cash_sessions WHERE status='CLOSED' ORDER BY id DESC LIMIT 1")
        return last["counted_cash"] if last else config.DEFAULT_OPENING_CASH

    def summary(self, session_id: int | None = None) -> dict:
        """시재 계산 내역. session_id 생략 시 현재 시재."""
        if session_id is None:
            cur = self.current()
            if not cur:
                raise PosError("개시된 시재가 없습니다.")
            session_id = cur["id"]
        s = self.get(session_id)
        q = self.db.scalar
        sales_row = self.db.query_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS amt FROM payments "
            "WHERE cash_session_id=? AND method='CASH'", (session_id,))
        refunds = q("SELECT COALESCE(SUM(amount),0) FROM refunds "
                    "WHERE cash_session_id=? AND method='CASH'", (session_id,))
        cash_in = q("SELECT COALESCE(SUM(amount),0) FROM cash_movements "
                    "WHERE session_id=? AND kind='IN'", (session_id,))
        cash_out = q("SELECT COALESCE(SUM(amount),0) FROM cash_movements "
                     "WHERE session_id=? AND kind='OUT'", (session_id,))
        expected = s["opening_cash"] + sales_row["amt"] - refunds + cash_in - cash_out
        return {"session_id": session_id, "status": s["status"], "opened_at": s["opened_at"],
                "opened_by": s["opened_by"], "opening": s["opening_cash"],
                "cash_sales": sales_row["amt"], "cash_sales_count": sales_row["n"],
                "cash_refunds": refunds, "cash_in": cash_in, "cash_out": cash_out,
                "expected": expected, "closed_at": s["closed_at"], "counted": s["counted_cash"],
                "difference": s["difference"], "note": s["note"]}

    # ------------------------------------------------------------ 개시 / 입출금
    @staticmethod
    def _check_amount(amount: int, what: str, allow_zero: bool = False) -> None:
        if amount < 0 or (amount == 0 and not allow_zero):
            raise PosError(f"{what}은(는) {'0원 이상' if allow_zero else '1원 이상'}이어야 합니다.")
        if amount % 10:
            raise PosError(f"{what}은(는) 10원 단위로 입력하세요.")

    def open(self, opening_cash: int, opened_by: str = "", note: str = "") -> int:
        if self.current():
            raise PosError("이미 개시된 시재가 있습니다. 먼저 정산하세요.")
        self._check_amount(opening_cash, "개시 금액", allow_zero=True)
        if opening_cash > config.MAX_OPENING_CASH:
            raise PosError(f"개시 금액은 {config.MAX_OPENING_CASH:,}원 이하로 입력하세요.")
        cur = self.db.execute(
            "INSERT INTO cash_sessions(opened_at, opened_by, opening_cash, note) VALUES(?,?,?,?)",
            (self.db.now_str(), opened_by.strip() or None, opening_cash, note.strip() or None))
        self.db.log("시재 개시", f"{opening_cash:,}원 {opened_by}")
        self.db.commit()
        return cur.lastrowid

    def add_movement(self, kind: str, amount: int, reason: str) -> int:
        """입금(잔돈 보충 등) / 출금(지출·은행 입금 등). 금전함 현금보다 많이 출금할 수 없다."""
        session_id = self.require_open("입출금")
        if kind not in KINDS:
            raise PosError("입출금 구분이 올바르지 않습니다.")
        self._check_amount(amount, "금액")
        if not reason.strip():
            raise PosError("입출금 사유를 입력하세요.")
        if kind == "OUT":
            expected = self.summary(session_id)["expected"]
            if amount > expected:
                raise PosError(f"금전함 예상 현금({expected:,}원)보다 많이 출금할 수 없습니다.")
        cur = self.db.execute(
            "INSERT INTO cash_movements(session_id, kind, amount, reason, at) VALUES(?,?,?,?,?)",
            (session_id, kind, amount, reason.strip(), self.db.now_str()))
        self.db.log(f"시재 {KINDS[kind]}", f"{amount:,}원 {reason.strip()}")
        self.db.commit()
        return cur.lastrowid

    # ------------------------------------------------------------ 점검 / 정산
    @staticmethod
    def count_total(denominations: dict[int, int]) -> int:
        """권종별 수량 → 합계. 모르는 권종이나 음수 수량은 거부."""
        total = 0
        for unit, qty in denominations.items():
            if int(unit) not in config.CASH_DENOMINATIONS:
                raise PosError(f"사용할 수 없는 권종입니다: {unit}")
            if qty < 0:
                raise PosError("권종 수량은 0 이상이어야 합니다.")
            total += int(unit) * qty
        return total

    def _record(self, kind: str, denominations: dict[int, int], note: str, checked_by: str) -> dict:
        session_id = self.require_open("시재 점검")
        counted = self.count_total(denominations)
        expected = self.summary(session_id)["expected"]
        diff = counted - expected
        note = note.strip()
        if kind == "CLOSE":
            if diff and not note:
                raise PosError(f"과부족 {diff:+,}원이 있습니다. 사유를 입력해야 정산할 수 있습니다.")
            if abs(diff) >= config.CASH_DIFF_ADMIN_LIMIT and self.db.mode != "ADMIN":
                raise PosError(f"과부족이 {config.CASH_DIFF_ADMIN_LIMIT:,}원 이상이면 관리자 모드에서만 "
                               "정산할 수 있습니다.")
        denoms = {str(k): int(v) for k, v in sorted(denominations.items(), reverse=True) if v}
        now = self.db.now_str()
        self.db.execute(
            "INSERT INTO cash_checks(session_id, kind, at, expected_cash, counted_cash, difference, "
            "denominations, note, checked_by) VALUES(?,?,?,?,?,?,?,?,?)",
            (session_id, kind, now, expected, counted, diff, json.dumps(denoms), note or None,
             checked_by.strip() or None))
        if kind == "CLOSE":
            self.db.execute(
                "UPDATE cash_sessions SET status='CLOSED', closed_at=?, closed_by=?, expected_cash=?, "
                "counted_cash=?, difference=?, note=COALESCE(?, note) WHERE id=?",
                (now, checked_by.strip() or None, expected, counted, diff, note or None, session_id))
        self.db.log("시재 정산" if kind == "CLOSE" else "시재 점검",
                    f"예상 {expected:,} / 실제 {counted:,} / 과부족 {diff:+,}")
        self.db.commit()
        return {"session_id": session_id, "expected": expected, "counted": counted, "difference": diff}

    def check(self, denominations: dict[int, int], note: str = "", checked_by: str = "") -> dict:
        """중간 시재 점검: 영업 중 현금을 세어 과부족을 기록한다(시재는 계속 열려 있음)."""
        return self._record("CHECK", denominations, note, checked_by)

    def close(self, denominations: dict[int, int], note: str = "", closed_by: str = "") -> dict:
        """시재 정산(마감). 과부족이 있으면 사유 필수, 기준 금액 이상이면 관리자만 가능."""
        return self._record("CLOSE", denominations, note, closed_by)

    # ------------------------------------------------------------ 보고서
    def report_text(self, session_id: int) -> str:
        s = self.summary(session_id)
        width = 40

        def signed(sign: str, v: int) -> str:
            return f"{sign}{v:,}원" if v else "0원"

        def line(left: str, right: str) -> str:
            w = sum(2 if ord(c) > 0x2E80 else 1 for c in left + right)
            return left + " " * max(1, width - w) + right
        out = ["=" * width, f"{config.STORE_NAME} 시재 정산서".center(width - 7), "=" * width,
               f"개시  {s['opened_at']}  {s['opened_by'] or ''}".rstrip(),
               f"정산  {s['closed_at'] or '(정산 전)'}", "-" * width,
               line("개시 금액", f"{s['opening']:,}원"),
               line(f"현금 매출 ({s['cash_sales_count']}건)", signed("+", s["cash_sales"])),
               line("현금 환불", signed("-", s["cash_refunds"])),
               line("입금", signed("+", s["cash_in"])),
               line("출금", signed("-", s["cash_out"])), "-" * width,
               line("예상 시재", f"{s['expected']:,}원")]
        if s["counted"] is not None:
            out += [line("실제 시재", f"{s['counted']:,}원"),
                    line("과부족", f"{s['difference']:+,}원")]
            if s["note"]:
                out.append(f"사유: {s['note']}")
        moves = self.movements(session_id)
        if moves:
            out += ["-" * width, "[입출금 내역]"]
            out += [line(f"  {m['at'][11:16]} {KINDS[m['kind']]} {m['reason']}",
                         f"{'+' if m['kind'] == 'IN' else '-'}{m['amount']:,}") for m in moves]
        last = self.db.query_one("SELECT * FROM cash_checks WHERE session_id=? AND kind='CLOSE'",
                                 (session_id,))
        if last:
            out += ["-" * width, "[권종별 수량]"]
            for unit, qty in json.loads(last["denominations"]).items():
                out.append(line(f"  {int(unit):,}원 × {qty}", f"{int(unit) * qty:,}원"))
        out.append("=" * width)
        return "\n".join(out)

    def day_summary(self, business_date: str) -> list[dict]:
        """그날 정산된 시재 목록(일일 마감 보고서용)."""
        rows = self.db.query("SELECT id FROM cash_sessions WHERE status='CLOSED' "
                             "AND substr(closed_at,1,10)=? ORDER BY id", (business_date,))
        return [self.summary(r["id"]) for r in rows]
