"""회원 관리 및 포인트."""
from __future__ import annotations

import re

from .. import config
from . import PosError, Service

PHONE_RE = re.compile(r"01[016789]-?\d{3,4}-?\d{4}")


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if not PHONE_RE.fullmatch(digits):
        raise PosError("휴대폰 번호 형식이 올바르지 않습니다. (예: 010-1234-5678)")
    return f"{digits[:3]}-{digits[3:-4]}-{digits[-4:]}"


def grade_of(total_spent: int) -> tuple[str, int]:
    """누적 결제액에 따른 (등급, 적립률%)."""
    for name, threshold, rate in config.MEMBER_GRADES:
        if total_spent >= threshold:
            return name, rate
    name, _, rate = config.MEMBER_GRADES[-1]
    return name, rate


class MemberService(Service):
    def members(self, keyword: str = ""):
        kw = f"%{keyword.strip()}%"
        return self.db.query(
            "SELECT * FROM members WHERE active=1 AND (name LIKE ? OR phone LIKE ? "
            "OR replace(phone,'-','') LIKE ?) ORDER BY name",
            (kw, kw, kw),
        )

    def get(self, member_id: int):
        row = self.db.query_one("SELECT * FROM members WHERE id=?", (member_id,))
        if not row:
            raise PosError("존재하지 않는 회원입니다.")
        return row

    def find_by_phone(self, phone: str):
        return self.db.query_one(
            "SELECT * FROM members WHERE phone=? AND active=1", (normalize_phone(phone),)
        )

    def register(self, name: str, phone: str) -> int:
        name = name.strip()
        if not 1 <= len(name) <= 20:
            raise PosError("이름은 1~20자로 입력하세요.")
        phone = normalize_phone(phone)
        row = self.db.query_one("SELECT * FROM members WHERE phone=?", (phone,))
        if row and row["active"]:
            raise PosError("이미 가입된 휴대폰 번호입니다.")
        if row:  # 탈퇴 회원 재가입 (포인트는 소멸 상태)
            self.db.execute(
                "UPDATE members SET name=?, active=1, points=0, total_spent=0, created_at=? WHERE id=?",
                (name, self.db.now_str(), row["id"]),
            )
            member_id = row["id"]
        else:
            member_id = self.db.execute(
                "INSERT INTO members(name, phone, created_at) VALUES(?,?,?)",
                (name, phone, self.db.now_str()),
            ).lastrowid
        self.db.log("회원 등록", f"{name} {phone}")
        self.db.commit()
        return member_id

    def update(self, member_id: int, name: str, phone: str) -> None:
        phone = normalize_phone(phone)
        if not name.strip():
            raise PosError("이름을 입력하세요.")
        if self.db.query_one("SELECT 1 FROM members WHERE phone=? AND id<>?", (phone, member_id)):
            raise PosError("이미 다른 회원이 사용 중인 번호입니다.")
        self.db.execute("UPDATE members SET name=?, phone=? WHERE id=?",
                        (name.strip(), phone, member_id))
        self.db.commit()

    def withdraw(self, member_id: int) -> None:
        if self.db.query_one(
            "SELECT 1 FROM orders WHERE member_id=? AND status='OPEN'", (member_id,)
        ):
            raise PosError("진행 중인 주문이 있는 회원은 탈퇴할 수 없습니다.")
        self.db.execute("UPDATE members SET active=0 WHERE id=?", (member_id,))
        self.db.log("회원 탈퇴", str(member_id))
        self.db.commit()

    def adjust_points(self, member_id: int, delta: int, reason: str,
                      order_id: int | None = None) -> None:
        """포인트 증감(커밋은 호출자 담당)."""
        member = self.get(member_id)
        if member["points"] + delta < 0:
            raise PosError("포인트가 부족합니다.")
        self.db.execute("UPDATE members SET points=points+? WHERE id=?", (delta, member_id))
        self.db.execute(
            "INSERT INTO point_logs(member_id, delta, reason, order_id, at) VALUES(?,?,?,?,?)",
            (member_id, delta, reason, order_id, self.db.now_str()),
        )

    def point_logs(self, member_id: int):
        return self.db.query(
            "SELECT * FROM point_logs WHERE member_id=? ORDER BY id DESC", (member_id,)
        )

    def order_history(self, member_id: int):
        return self.db.query(
            "SELECT * FROM orders WHERE member_id=? AND status IN ('PAID','REFUNDED') "
            "ORDER BY id DESC", (member_id,)
        )

    @staticmethod
    def validate_point_use(points: int, member_points: int, payable: int) -> None:
        if points == 0:
            return
        if points < config.MIN_POINT_USE:
            raise PosError(f"포인트는 {config.MIN_POINT_USE:,}P 이상부터 사용할 수 있습니다.")
        if points % config.POINT_USE_UNIT:
            raise PosError(f"포인트는 {config.POINT_USE_UNIT}P 단위로 사용할 수 있습니다.")
        if points > member_points:
            raise PosError(f"보유 포인트({member_points:,}P)를 초과했습니다.")
        if points > payable:
            raise PosError("결제 금액을 초과하여 포인트를 사용할 수 없습니다.")
