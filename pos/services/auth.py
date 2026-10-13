"""사용자/관리자 모드 전환과 관리자 비밀번호."""
from __future__ import annotations

import time

from .. import config
from ..db import hash_password, verify_password
from . import PosError, Service


class AuthService(Service):
    def __init__(self, db, clock=time.monotonic):
        super().__init__(db)
        self._clock = clock
        self._fails = 0
        self._locked_until = 0.0

    @property
    def is_admin(self) -> bool:
        return self.db.mode == "ADMIN"

    def lock_remaining(self) -> int:
        return max(0, int(self._locked_until - self._clock() + 0.999))

    def login(self, password: str) -> None:
        remain = self.lock_remaining()
        if remain:
            raise PosError(f"로그인 시도가 잠겼습니다. {remain}초 후 다시 시도하세요.")
        if not verify_password(password, self.db.get_setting("admin_password")):
            self._fails += 1
            if self._fails >= config.MAX_LOGIN_ATTEMPTS:
                self._fails = 0
                self._locked_until = self._clock() + config.LOGIN_LOCK_SECONDS
                self.db.log("관리자 로그인 잠금")
                self.db.commit()
                raise PosError(f"비밀번호를 {config.MAX_LOGIN_ATTEMPTS}회 틀려 "
                               f"{config.LOGIN_LOCK_SECONDS}초간 잠깁니다.")
            raise PosError(f"비밀번호가 틀렸습니다. ({self._fails}/{config.MAX_LOGIN_ATTEMPTS})")
        self._fails = 0
        self.db.mode = "ADMIN"
        self.db.log("관리자 모드 진입")
        self.db.commit()

    def logout(self) -> None:
        self.db.log("사용자 모드 전환")
        self.db.mode = "USER"
        self.db.commit()

    def change_password(self, old: str, new: str) -> None:
        if not verify_password(old, self.db.get_setting("admin_password")):
            raise PosError("현재 비밀번호가 틀렸습니다.")
        if not (new.isdigit() and 4 <= len(new) <= 8):
            raise PosError("새 비밀번호는 숫자 4~8자리여야 합니다.")
        self.db.set_setting("admin_password", hash_password(new))
        self.db.log("관리자 비밀번호 변경")
        self.db.commit()

    def require_admin(self) -> None:
        if not self.is_admin:
            raise PosError("관리자 모드에서만 사용할 수 있습니다.")

    def audit_logs(self, limit: int = 300):
        return self.db.query("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))
