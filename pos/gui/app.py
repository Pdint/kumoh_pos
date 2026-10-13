"""메인 윈도우: 상단 바(가게명·모드·시계), 탭 구성, 사용자/관리자 모드 전환, 예약 알림."""
from __future__ import annotations

import tkinter as tk
import traceback
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from .. import config
from ..services.staff import KINDS
from ..system import PosSystem
from .admin_views import (ClosingView, DiscountView, InventoryView, MenuAdminView,
                          OrderHistoryView, SettingsView, StaffView, TableAdminView)
from .kitchen import KitchenView
from .member_view import MemberView
from .order_view import OrderView
from .reservation_view import ReservationView
from .stats_view import StatsView
from .widgets import COLORS, center, font, init_fonts, make_tree, run_safe

USER_TABS = [("주문 / 결제", OrderView), ("주방", KitchenView), ("예약", ReservationView),
             ("회원", MemberView)]
ADMIN_TABS = [("주문 내역·환불", OrderHistoryView), ("매출 통계", StatsView), ("메뉴", MenuAdminView),
              ("재고", InventoryView), ("테이블", TableAdminView), ("할인·쿠폰", DiscountView),
              ("직원·급여", StaffView), ("일일 마감", ClosingView), ("설정", SettingsView)]


class App(tk.Tk):
    def __init__(self, pos: PosSystem, icon: str | None = None,
                 error_log: Callable[[str], str] | None = None):
        super().__init__()
        self.pos = pos
        self.error_log = error_log
        init_fonts(self)
        self.title(f"{config.STORE_NAME} POS")
        if icon:
            try:
                self.iconbitmap(default=icon)
            except tk.TclError:
                pass
        self.geometry("1400x840")
        self.minsize(1200, 760)
        self.configure(bg=COLORS["bg"])
        self.alerted_reservations: set[int] = set()
        self.warned_ingredients: set[str] = set()

        self._build_header()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=6)
        self.views: dict[str, ttk.Frame] = {}
        for title, cls in USER_TABS + ADMIN_TABS:
            view = cls(self.nb, self)
            self.views[title] = view
        for title, _ in USER_TABS:
            self.nb.add(self.views[title], text=f" {title} ")
        self.order_view: OrderView = self.views["주문 / 결제"]
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh_current())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<F12>", lambda e: self.toggle_mode())
        self._tick()
        self.after(1500, self._check_reservations)
        self.after(800, self._startup_stock_check)

    def report_callback_exception(self, exc, val, tb):
        """예상치 못한 오류: 프로그램을 끄지 않고 알린 뒤 로그 파일에 기록한다."""
        text = "".join(traceback.format_exception(exc, val, tb))
        where = self.error_log(text) if self.error_log else None
        messagebox.showerror("오류", f"처리 중 오류가 발생했습니다: {val}"
                             + (f"\n\n로그: {where}" if where else ""), parent=self)

    # ---------------------------------------------------------------- 상단 바
    def _build_header(self):
        head = tk.Frame(self, bg=COLORS["header"], height=52)
        head.pack(fill="x")
        tk.Label(head, text=config.STORE_NAME, bg=COLORS["header"], fg="white",
                 font=font(17, True)).pack(side="left", padx=(14, 4), pady=8)
        tk.Label(head, text=config.STORE_SUBTITLE, bg=COLORS["header"], fg="#9ca3af",
                 font=font(10)).pack(side="left", pady=(10, 0))
        self.mode_lbl = tk.Label(head, text="", font=font(10, True), padx=10, pady=3)
        self.mode_lbl.pack(side="left", padx=16)
        self.clock_lbl = tk.Label(head, text="", bg=COLORS["header"], fg="white", font=font(12, True))
        self.clock_lbl.pack(side="right", padx=14)
        self.mode_btn = tk.Button(head, text="", font=font(10, True), relief="flat", padx=12,
                                  command=self.toggle_mode)
        self.mode_btn.pack(side="right", padx=4, pady=10)
        tk.Button(head, text="출퇴근", font=font(10, True), relief="flat", padx=12,
                  command=self.attendance).pack(side="right", padx=4, pady=10)
        self.alert_lbl = tk.Label(head, text="", bg=COLORS["header"], fg="#fbbf24", font=font(10, True),
                                  cursor="hand2")
        self.alert_lbl.pack(side="right", padx=10)
        self.alert_lbl.bind("<Button-1>", lambda e: self.show_tab("예약"))
        self._update_mode_ui()

    def _update_mode_ui(self):
        if self.pos.auth.is_admin:
            self.mode_lbl.configure(text="관리자 모드", bg=COLORS["accent"], fg="white")
            self.mode_btn.configure(text="사용자 모드로 전환", bg="#fde68a")
        else:
            self.mode_lbl.configure(text="사용자 모드", bg="#374151", fg="white")
            self.mode_btn.configure(text="관리자 모드 (F12)", bg="#e5e7eb")

    def toggle_mode(self):
        if self.pos.auth.is_admin:
            self.pos.auth.logout()
            for title, _ in ADMIN_TABS:
                self.nb.forget(self.views[title])
            self.nb.select(self.order_view)
        else:
            pw = simpledialog.askstring("관리자 로그인", "관리자 비밀번호 (초기값 0000)", show="*",
                                        parent=self)
            if pw is None or not run_safe(self, self.pos.auth.login, pw):
                return
            for title, _ in ADMIN_TABS:
                self.nb.add(self.views[title], text=f" {title} ")
            self.nb.select(self.views["매출 통계"])
        self._update_mode_ui()
        self.refresh_current()

    # ---------------------------------------------------------------- 공용 동작
    def show_tab(self, title):
        view = self.views[title]
        if str(view) in self.nb.tabs():
            self.nb.select(view)

    def refresh_current(self):
        sel = self.nb.select()
        if sel:
            view = self.nametowidget(sel)
            if hasattr(view, "refresh"):
                view.refresh()

    def refresh_all(self):
        self.order_view.refresh()
        self.refresh_current()
        self._update_low_stock_memory()
        self._update_alert()

    def after_payment(self, order_id: int):
        self.refresh_all()

    def open_order(self, order_id: int):
        order = self.pos.orders.get(order_id)
        self.nb.select(self.order_view)
        if order["order_type"] == "TAKEOUT":
            self.order_view._leave_current()
            self.order_view.takeout_mode = True
            self.order_view.current_table_id = None
            self.order_view.current_order_id = order_id
            self.order_view.refresh()
        else:
            self.order_view.select_table(order["table_id"])

    def _update_low_stock_memory(self):
        low = {r["name"] for r in self.pos.db.query(
            "SELECT name FROM ingredients WHERE stock <= threshold")}
        self.warned_ingredients &= low

    def show_stock_warnings(self, warnings: list[str]):
        """재고 경고는 재료별로 기준 이하로 처음 떨어졌을 때 한 번만 팝업한다."""
        new = []
        for w in warnings:
            name = w.split("] ", 1)[1].split(" 잔량")[0]
            if name not in self.warned_ingredients:
                self.warned_ingredients.add(name)
                new.append(w)
        if new:
            messagebox.showwarning("재고 경고", "\n".join(new) + "\n\n관리자 모드 > 재고 에서 입고 처리하세요.",
                                   parent=self)

    def _startup_stock_check(self):
        warnings = self.pos.menu.low_stock_warnings()
        if warnings:
            self.show_stock_warnings(warnings)

    # ---------------------------------------------------------------- 주기 작업
    def _tick(self):
        now = self.pos.db.now()
        self.clock_lbl.configure(text=now.strftime("%Y-%m-%d (%a) %H:%M:%S"))
        if now.second % 15 == 0:
            sel = self.nb.select()
            if sel and self.nametowidget(sel) is self.views["주방"]:
                self.views["주방"].refresh()
        self.after(1000, self._tick)

    def _update_alert(self):
        due = self.pos.reservations.due()
        self.alert_lbl.configure(text=f"[알림] 예약 {len(due)}건 도래" if due else "")
        return due

    def _check_reservations(self):
        due = self._update_alert()
        new = [r for r in due if r["id"] not in self.alerted_reservations]
        for r in new:
            self.alerted_reservations.add(r["id"])
            items = ", ".join(f"{i['name']}×{i['qty']}" for i in self.pos.reservations.items(r["id"]))
            where = config.ORDER_TYPES[r["order_type"]]
            if messagebox.askyesno(
                    "예약 알림",
                    f"[{r['reserve_at']}] {r['customer_name']}님 ({r['phone']}) {where} 예약\n"
                    f"메뉴: {items}\n메모: {r['note'] or '-'}\n\n지금 주문으로 전환할까요?", parent=self):
                result = run_safe(self, self.pos.reservations.convert_to_order, r["id"])
                if result:
                    order_id, warnings = result
                    self.refresh_all()
                    self.open_order(order_id)
                    if warnings:
                        self.show_stock_warnings(warnings)
        self._update_alert()
        self.after(30_000, self._check_reservations)

    # ---------------------------------------------------------------- 출퇴근
    def attendance(self):
        win = tk.Toplevel(self)
        win.title("직원 출퇴근")
        win.transient(self)
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)
        tree = make_tree(body, [("name", "이름", 90), ("kind", "형태", 80), ("state", "상태", 160)],
                         height=8)

        def load():
            working = {w["staff_id"]: w["clock_in"] for w in self.pos.staff.working_now()}
            tree.delete(*tree.get_children())
            for s in self.pos.staff.staff_list():
                state = f"근무 중 ({working[s['id']][11:16]} 출근)" if s["id"] in working else "퇴근"
                tree.insert("", "end", iid=str(s["id"]), values=(s["name"], KINDS[s["kind"]], state))

        def act(is_in):
            sel = tree.selection()
            if not sel:
                return
            sid = int(sel[0])
            if is_in:
                if run_safe(win, self.pos.staff.clock_in, sid):
                    messagebox.showinfo("출근", "출근 처리되었습니다.", parent=win)
            else:
                h = run_safe(win, self.pos.staff.clock_out, sid)
                if h:
                    messagebox.showinfo("퇴근", f"수고하셨습니다! 근무시간 {h:.2f}시간", parent=win)
            load()

        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="출근", style="Big.TButton", command=lambda: act(True)).pack(
            side="left", expand=True, fill="x", padx=2)
        ttk.Button(bar, text="퇴근", style="Big.TButton", command=lambda: act(False)).pack(
            side="left", expand=True, fill="x", padx=2)
        load()
        center(win, self)
        win.grab_set()

    def on_close(self):
        open_n = self.pos.db.scalar("SELECT COUNT(*) FROM orders WHERE status='OPEN' AND "
                                    "EXISTS (SELECT 1 FROM order_items WHERE order_id=orders.id)")
        msg = "프로그램을 종료할까요?"
        if open_n:
            msg = f"미결제 주문이 {open_n}건 있습니다. (데이터는 저장되어 다음 실행 시 유지됩니다)\n\n" + msg
        if messagebox.askyesno("종료", msg, parent=self):
            # 빈 주문 정리
            self.pos.db.execute("DELETE FROM orders WHERE status='OPEN' AND locked=0 AND NOT EXISTS "
                                "(SELECT 1 FROM order_items WHERE order_id=orders.id)")
            self.pos.db.commit()
            self.pos.close()
            self.destroy()


def run(pos: PosSystem, icon: str | None = None,
        error_log: Callable[[str], str] | None = None) -> None:
    app = App(pos, icon=icon, error_log=error_log)
    app.mainloop()
