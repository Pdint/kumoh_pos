"""예약 주문 화면."""
from __future__ import annotations

import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox, ttk

from .. import config
from ..services.reservation import STATUS
from .widgets import COLORS, center, fill_tree, font, make_tree, run_safe, selected_id


class ReservationView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos
        self.date_var = tk.StringVar(value=date.today().isoformat())
        self.only_waiting = tk.BooleanVar(value=False)
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="날짜").pack(side="left")
        ttk.Entry(bar, textvariable=self.date_var, width=11).pack(side="left", padx=4)
        ttk.Button(bar, text="◀", width=3, command=lambda: self.shift(-1)).pack(side="left")
        ttk.Button(bar, text="오늘", command=lambda: self.shift(0)).pack(side="left", padx=2)
        ttk.Button(bar, text="▶", width=3, command=lambda: self.shift(1)).pack(side="left")
        ttk.Button(bar, text="조회", command=self.refresh).pack(side="left", padx=6)
        ttk.Checkbutton(bar, text="전체 날짜의 대기 예약만", variable=self.only_waiting,
                        command=self.refresh).pack(side="left", padx=6)
        ttk.Button(bar, text="+ 새 예약", style="Accent.TButton", command=self.new).pack(side="right")

        self.tree = make_tree(self, [
            ("time", "예약 시각", 130), ("name", "예약자", 80), ("phone", "연락처", 110),
            ("type", "유형", 60), ("table", "테이블", 60), ("items", "메뉴", 260),
            ("status", "상태", 70), ("note", "메모", 120)], height=16)
        self.tree.tag_configure("due", background="#fff4e5")
        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="주문으로 전환 (손님 도착)", style="Big.TButton",
                   command=self.convert).pack(side="left", padx=2)
        ttk.Button(btns, text="예약 취소", command=lambda: self.set_status("CANCELLED")).pack(
            side="left", padx=2)
        ttk.Button(btns, text="노쇼 처리", command=lambda: self.set_status("NO_SHOW")).pack(
            side="left", padx=2)
        ttk.Label(btns, text=f"※ 예약 시각 {config.RESERVATION_ALERT_MINUTES}분 전부터 알림이 표시됩니다.",
                  foreground=COLORS["muted"]).pack(side="right")

    def shift(self, days):
        try:
            base = date.fromisoformat(self.date_var.get()) if days else date.today()
        except ValueError:
            base = date.today()
        self.date_var.set((base + timedelta(days=days)).isoformat())
        self.refresh()

    def refresh(self):
        if self.only_waiting.get():
            rows = self.pos.reservations.reservations(status="WAITING")
        else:
            rows = self.pos.reservations.reservations(self.date_var.get())
        due_ids = {r["id"] for r in self.pos.reservations.due()}
        data, ids, tags = [], [], []
        for r in rows:
            items = ", ".join(f"{i['name']}×{i['qty']}" for i in self.pos.reservations.items(r["id"]))
            data.append((r["reserve_at"], r["customer_name"], r["phone"],
                         config.ORDER_TYPES[r["order_type"]], r["table_name"] or "-", items,
                         STATUS[r["status"]], r["note"] or ""))
            ids.append(r["id"])
            tags.append("due" if r["id"] in due_ids else ("muted" if r["status"] != "WAITING" else ""))
        fill_tree(self.tree, data, ids, tags)

    def convert(self):
        rid = selected_id(self.tree)
        if rid is None:
            return
        result = run_safe(self, self.pos.reservations.convert_to_order, rid)
        if result:
            order_id, warnings = result
            self.app.refresh_all()
            self.app.open_order(order_id)
            if warnings:
                self.app.show_stock_warnings(warnings)

    def set_status(self, status):
        rid = selected_id(self.tree)
        if rid is None:
            return
        label = "취소" if status == "CANCELLED" else "노쇼"
        if messagebox.askyesno("확인", f"선택한 예약을 {label} 처리할까요?", parent=self):
            run_safe(self, self.pos.reservations.set_status, rid, status)
            self.refresh()

    def new(self):
        ReservationDialog(self, self.pos)
        self.refresh()


class ReservationDialog(tk.Toplevel):
    def __init__(self, parent, pos):
        super().__init__(parent)
        self.pos = pos
        self.title("새 예약")
        self.transient(parent.winfo_toplevel())
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        default_time = (pos.db.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:00")
        self.vars = {k: tk.StringVar(value=v) for k, v in (
            ("name", ""), ("phone", ""), ("at", default_time), ("type", "포장"), ("table", ""),
            ("note", ""))}
        self.tables = pos.tables.tables()
        rows = [("name", "예약자"), ("phone", "연락처"), ("at", "예약 시각 (YYYY-MM-DD HH:MM)"),
                ("type", "유형"), ("table", "테이블 (매장)"), ("note", "메모")]
        for r, (key, label) in enumerate(rows):
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=3)
            if key == "type":
                w = ttk.Combobox(body, textvariable=self.vars[key], values=["포장", "매장"],
                                 state="readonly", width=24)
            elif key == "table":
                w = ttk.Combobox(body, textvariable=self.vars[key],
                                 values=[t["name"] for t in self.tables], state="readonly", width=24)
            else:
                w = ttk.Entry(body, textvariable=self.vars[key], width=26)
            w.grid(row=r, column=1, sticky="w", pady=3, padx=6)
        ttk.Label(body, text="메뉴 수량", font=font(10, True)).grid(
            row=0, column=2, sticky="w", padx=(16, 0))
        menu_frame = ttk.Frame(body)
        menu_frame.grid(row=1, column=2, rowspan=8, sticky="n", padx=(16, 0))
        self.qty_vars = {}
        menus = pos.menu.menus()
        per_col = (len(menus) + 1) // 2
        for i, m in enumerate(menus):
            col = (i // per_col) * 2
            ttk.Label(menu_frame, text=f"{m['name']} ({m['price']:,})").grid(
                row=i % per_col, column=col, sticky="w")
            v = tk.StringVar(value="0")
            ttk.Spinbox(menu_frame, from_=0, to=config.MAX_ITEM_QTY, textvariable=v, width=4).grid(
                row=i % per_col, column=col + 1, padx=(4, 12), pady=1)
            self.qty_vars[m["id"]] = v
        btns = ttk.Frame(body)
        btns.grid(row=9, column=0, columnspan=3, pady=(12, 0))
        ttk.Button(btns, text="예약 등록", style="Accent.TButton", command=self.save).pack(
            side="left", padx=4)
        ttk.Button(btns, text="취소", command=self.destroy).pack(side="left", padx=4)
        center(self, parent)
        self.grab_set()
        self.wait_window()

    def save(self):
        try:
            items = {mid: int(v.get() or 0) for mid, v in self.qty_vars.items()}
        except ValueError:
            messagebox.showwarning("입력 오류", "수량은 숫자로 입력하세요.", parent=self)
            return
        order_type = "DINE_IN" if self.vars["type"].get() == "매장" else "TAKEOUT"
        table_id = None
        if self.vars["table"].get():
            table_id = next(t["id"] for t in self.tables if t["name"] == self.vars["table"].get())
        if run_safe(self, self.pos.reservations.create, self.vars["name"].get(),
                    self.vars["phone"].get(), self.vars["at"].get().strip(), order_type, items,
                    table_id, self.vars["note"].get()):
            self.destroy()
