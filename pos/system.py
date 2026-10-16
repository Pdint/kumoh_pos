"""모든 서비스를 묶는 파사드. GUI와 테스트는 PosSystem 하나만 생성해서 사용한다."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from .db import Database, seed_default_options, seed_initial_data
from .services.auth import AuthService
from .services.cash import CashService
from .services.discount import DiscountService
from .services.member import MemberService
from .services.menu import MenuService
from .services.order import OrderService
from .services.payment import PaymentService
from .services.printer import PrinterService
from .services.report import ReportService
from .services.reservation import ReservationService
from .services.staff import StaffService
from .services.stats import StatsService
from .services.table import TableService


class PosSystem:
    def __init__(self, db_path: str = ":memory:", out_dir: str = ".", seed: bool = True,
                 now: Callable[[], datetime] | None = None):
        self.db = Database(db_path, now=now)
        if seed:
            seed_initial_data(self.db)
            seed_default_options(self.db)
        self.menu = MenuService(self.db)
        self.tables = TableService(self.db)
        self.discount = DiscountService(self.db)
        self.members = MemberService(self.db)
        self.orders = OrderService(self.db, self.menu)
        self.cash = CashService(self.db)
        self.payments = PaymentService(self.db, self.menu, self.discount, self.members, self.cash)
        self.staff = StaffService(self.db)
        self.reservations = ReservationService(self.db, self.orders)
        self.stats = StatsService(self.db)
        self.reports = ReportService(self.db, self.discount, self.stats, out_dir, self.cash)
        self.auth = AuthService(self.db)
        self.printer = PrinterService(self.db, out_dir)

    def close(self) -> None:
        self.db.close()
