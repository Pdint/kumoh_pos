"""POS 비즈니스 로직 계층. GUI와 독립적으로 동작하며 단위 테스트 대상이다."""
from __future__ import annotations

from ..db import Database


class PosError(Exception):
    """사용자에게 그대로 보여줄 수 있는 업무 규칙 위반 오류."""


def floor_unit(amount: float, unit: int) -> int:
    """금액을 unit 단위로 절사한다."""
    return int(amount // unit * unit)


class Service:
    def __init__(self, db: Database):
        self.db = db
