"""직원(정직원/아르바이트) 근태 기록 및 급여 계산."""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import datetime

from .. import config
from . import PosError, Service

KINDS = {"FULL": "정직원", "PART": "아르바이트"}


def _dt(s: str) -> datetime:
    return datetime.strptime(s, config.DATETIME_FMT)


def hours_between(start: str, end: str) -> float:
    return round((_dt(end) - _dt(start)).total_seconds() / 3600, 2)


class StaffService(Service):
    # ------------------------------------------------------------ 직원
    def staff_list(self, include_inactive: bool = False):
        sql = "SELECT * FROM staff"
        if not include_inactive:
            sql += " WHERE active=1"
        return self.db.query(sql + " ORDER BY kind, name")

    def get(self, staff_id: int):
        row = self.db.query_one("SELECT * FROM staff WHERE id=?", (staff_id,))
        if not row:
            raise PosError("존재하지 않는 직원입니다.")
        return row

    @staticmethod
    def _validate(name: str, kind: str, hourly_wage: int, monthly_salary: int) -> None:
        if not name.strip():
            raise PosError("이름을 입력하세요.")
        if kind not in KINDS:
            raise PosError("고용 형태가 올바르지 않습니다.")
        if kind == "PART" and hourly_wage < config.MINIMUM_WAGE:
            raise PosError(f"시급은 최저시급({config.MINIMUM_WAGE:,}원) 이상이어야 합니다.")
        if kind == "FULL" and monthly_salary / config.FULLTIME_MONTHLY_HOURS < config.MINIMUM_WAGE:
            min_salary = config.MINIMUM_WAGE * config.FULLTIME_MONTHLY_HOURS
            raise PosError(f"월급은 최저임금 기준({min_salary:,}원) 이상이어야 합니다.")

    def add_staff(self, name: str, kind: str, hourly_wage: int = 0, monthly_salary: int = 0,
                  phone: str = "") -> int:
        self._validate(name, kind, hourly_wage, monthly_salary)
        cur = self.db.execute(
            "INSERT INTO staff(name, kind, hourly_wage, monthly_salary, phone) VALUES(?,?,?,?,?)",
            (name.strip(), kind, hourly_wage if kind == "PART" else 0,
             monthly_salary if kind == "FULL" else 0, phone.strip()),
        )
        self.db.log("직원 등록", f"{name} ({KINDS[kind]})")
        self.db.commit()
        return cur.lastrowid

    def update_staff(self, staff_id: int, name: str, kind: str, hourly_wage: int,
                     monthly_salary: int, phone: str = "") -> None:
        self._validate(name, kind, hourly_wage, monthly_salary)
        self.db.execute(
            "UPDATE staff SET name=?, kind=?, hourly_wage=?, monthly_salary=?, phone=? WHERE id=?",
            (name.strip(), kind, hourly_wage if kind == "PART" else 0,
             monthly_salary if kind == "FULL" else 0, phone.strip(), staff_id),
        )
        self.db.commit()

    def retire(self, staff_id: int) -> None:
        if self.current_log(staff_id):
            raise PosError("근무 중인 직원은 퇴사 처리할 수 없습니다. 먼저 퇴근 처리하세요.")
        self.db.execute("UPDATE staff SET active=0 WHERE id=?", (staff_id,))
        self.db.log("직원 퇴사", str(staff_id))
        self.db.commit()

    @staticmethod
    def ordinary_hourly(staff) -> float:
        """통상시급: 아르바이트는 시급, 정직원은 월급/209."""
        if staff["kind"] == "PART":
            return staff["hourly_wage"]
        return staff["monthly_salary"] / config.FULLTIME_MONTHLY_HOURS

    # ------------------------------------------------------------ 출퇴근
    def current_log(self, staff_id: int):
        return self.db.query_one(
            "SELECT * FROM work_logs WHERE staff_id=? AND clock_out IS NULL", (staff_id,)
        )

    def working_now(self):
        return self.db.query(
            "SELECT s.name, s.kind, w.* FROM work_logs w JOIN staff s ON s.id=w.staff_id "
            "WHERE w.clock_out IS NULL ORDER BY w.clock_in"
        )

    def clock_in(self, staff_id: int) -> None:
        staff = self.get(staff_id)
        if not staff["active"]:
            raise PosError("퇴사한 직원입니다.")
        if self.current_log(staff_id):
            raise PosError(f"{staff['name']}님은 이미 출근 상태입니다.")
        self.db.execute("INSERT INTO work_logs(staff_id, clock_in) VALUES(?,?)",
                        (staff_id, self.db.now_str()))
        self.db.log("출근", staff["name"])
        self.db.commit()

    def clock_out(self, staff_id: int) -> float:
        staff = self.get(staff_id)
        log = self.current_log(staff_id)
        if not log:
            raise PosError(f"{staff['name']}님은 출근 기록이 없습니다.")
        now = self.db.now_str()
        hours = hours_between(log["clock_in"], now)
        if hours > config.MAX_SHIFT_HOURS:
            # 퇴근을 깜빡한 경우: 최대 근무시간으로 잘라서 기록하지 않고 관리자 수정을 요구한다.
            raise PosError(
                f"근무시간이 {config.MAX_SHIFT_HOURS}시간을 초과했습니다. 관리자 모드에서 기록을 수정하세요.")
        self.db.execute("UPDATE work_logs SET clock_out=? WHERE id=?", (now, log["id"]))
        self.db.log("퇴근", f"{staff['name']} {hours:.2f}h")
        self.db.commit()
        return hours

    def add_log(self, staff_id: int, clock_in: str, clock_out: str) -> int:
        """관리자의 근무 기록 수동 입력."""
        try:
            cin, cout = _dt(clock_in), _dt(clock_out)
        except ValueError:
            raise PosError("시간은 YYYY-MM-DD HH:MM:SS 형식으로 입력하세요.") from None
        if cout <= cin:
            raise PosError("퇴근 시간은 출근 시간보다 늦어야 합니다.")
        if (cout - cin).total_seconds() / 3600 > config.MAX_SHIFT_HOURS:
            raise PosError(f"1회 근무는 {config.MAX_SHIFT_HOURS}시간을 넘을 수 없습니다.")
        overlap = self.db.query_one(
            "SELECT 1 FROM work_logs WHERE staff_id=? AND clock_in < ? AND "
            "COALESCE(clock_out, '9999') > ?",
            (staff_id, clock_out, clock_in),
        )
        if overlap:
            raise PosError("기존 근무 기록과 시간이 겹칩니다.")
        cur = self.db.execute(
            "INSERT INTO work_logs(staff_id, clock_in, clock_out) VALUES(?,?,?)",
            (staff_id, clock_in, clock_out),
        )
        self.db.log("근무 기록 입력", f"{staff_id} {clock_in}~{clock_out}")
        self.db.commit()
        return cur.lastrowid

    def delete_log(self, log_id: int) -> None:
        self.db.execute("DELETE FROM work_logs WHERE id=?", (log_id,))
        self.db.log("근무 기록 삭제", str(log_id))
        self.db.commit()

    def logs(self, staff_id: int | None, start: str, end: str):
        sql = (
            "SELECT w.*, s.name FROM work_logs w JOIN staff s ON s.id=w.staff_id "
            "WHERE substr(w.clock_in,1,10) BETWEEN ? AND ?"
        )
        params: list = [start, end]
        if staff_id is not None:
            sql += " AND w.staff_id=?"
            params.append(staff_id)
        return self.db.query(sql + " ORDER BY w.clock_in DESC", params)

    # ------------------------------------------------------------ 급여
    def payroll(self, year: int, month: int) -> list[dict]:
        """월 급여 명세.

        - 아르바이트: 시급 × 근무시간 + 주휴수당(주 15시간 이상 근무한 주: min(주 근무시간,40)/40 × 8 × 시급)
        - 정직원: 월급 + 연장수당(1일 8시간 초과분 × 통상시급 × 1.5)
        - 주(週)는 ISO 주 기준이며, 해당 월에 속한 근무만 집계한다.
        """
        last_day = calendar.monthrange(year, month)[1]
        start, end = f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"
        result = []
        for staff in self.staff_list(include_inactive=True):
            logs = [lg for lg in self.logs(staff["id"], start, end) if lg["clock_out"]]
            if not logs and not staff["active"]:
                continue
            daily: dict[str, float] = defaultdict(float)
            weekly: dict[tuple, float] = defaultdict(float)
            for lg in logs:
                h = hours_between(lg["clock_in"], lg["clock_out"])
                daily[lg["clock_in"][:10]] += h
                weekly[_dt(lg["clock_in"]).isocalendar()[:2]] += h
            total_hours = round(sum(daily.values()), 2)
            hourly = self.ordinary_hourly(staff)
            overtime_hours = round(sum(max(0.0, h - config.DAILY_REGULAR_HOURS)
                                       for h in daily.values()), 2)
            if staff["kind"] == "PART":
                base = int(total_hours * hourly)
                holiday = int(sum(
                    min(h, 40) / 40 * 8 * hourly
                    for h in weekly.values() if h >= config.WEEKLY_HOLIDAY_MIN_HOURS
                ))
                overtime_pay = int(overtime_hours * hourly * (config.OVERTIME_RATE - 1))
            else:
                base = staff["monthly_salary"]
                holiday = 0
                overtime_pay = int(overtime_hours * hourly * config.OVERTIME_RATE)
            result.append({
                "staff_id": staff["id"], "name": staff["name"], "kind": KINDS[staff["kind"]],
                "days": len(daily), "hours": total_hours, "overtime_hours": overtime_hours,
                "hourly": int(hourly), "base": base, "overtime_pay": overtime_pay,
                "holiday_pay": holiday, "total": base + overtime_pay + holiday,
            })
        return result
