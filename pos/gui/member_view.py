"""회원 관리 화면."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import config
from ..services.member import grade_of
from .widgets import (COLORS, ask_form, fill_tree, font, make_tree, run_safe, selected_id)


class MemberView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos
        self.kw = tk.StringVar()
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="검색 (이름/번호)").pack(side="left")
        e = ttk.Entry(bar, textvariable=self.kw, width=20)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self.refresh())
        ttk.Button(bar, text="검색", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="+ 회원 등록", command=self.add).pack(side="right")
        ttk.Button(bar, text="정보 수정", command=self.edit).pack(side="right", padx=4)
        self.withdraw_btn = ttk.Button(bar, text="탈퇴 처리", command=self.withdraw)
        self.withdraw_btn.pack(side="right")
        self.point_btn = ttk.Button(bar, text="포인트 조정", command=self.adjust)
        self.point_btn.pack(side="right", padx=4)

        grades = " / ".join(f"{n}: 누적 {t:,}원 이상 {r}% 적립" for n, t, r in config.MEMBER_GRADES)
        ttk.Label(self, text=f"등급 기준 — {grades}", foreground=COLORS["muted"]).pack(anchor="w",
                                                                                pady=4)
        pane = ttk.PanedWindow(self, orient="vertical")
        pane.pack(fill="both", expand=True)
        top = ttk.Frame(pane)
        pane.add(top, weight=3)
        self.tree = make_tree(top, [
            ("name", "이름", 90), ("phone", "휴대폰", 120), ("grade", "등급", 60),
            ("points", "포인트", 90), ("spent", "누적 결제", 110), ("joined", "가입일", 100)],
            height=12, anchor_right=("points", "spent"))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_history())
        bottom = ttk.Frame(pane)
        pane.add(bottom, weight=2)
        ttk.Label(bottom, text="구매 / 포인트 이력", font=font(10, True)).pack(anchor="w")
        self.hist = make_tree(bottom, [("at", "일시", 140), ("kind", "구분", 100),
                                       ("detail", "내용", 260)], height=7)

    def refresh(self):
        admin = self.pos.auth.is_admin
        self.withdraw_btn.configure(state="normal" if admin else "disabled")
        self.point_btn.configure(state="normal" if admin else "disabled")
        rows = self.pos.members.members(self.kw.get())
        fill_tree(self.tree, [(m["name"], m["phone"], grade_of(m["total_spent"])[0],
                               f"{m['points']:,}P", f"{m['total_spent']:,}", m["created_at"][:10])
                              for m in rows], [m["id"] for m in rows])
        self.hist.delete(*self.hist.get_children())

    def show_history(self):
        mid = selected_id(self.tree)
        if mid is None:
            return
        rows = []
        for o in self.pos.members.order_history(mid):
            state = "환불" if o["status"] == "REFUNDED" else "구매"
            rows.append((o["paid_at"], state, f"주문 #{o['order_no']} {o['total']:,}원"))
        for p in self.pos.members.point_logs(mid):
            rows.append((p["at"], "포인트", f"{p['delta']:+,}P  {p['reason']}"))
        rows.sort(key=lambda r: r[0] or "", reverse=True)
        fill_tree(self.hist, rows)

    def add(self):
        data = ask_form(self, "회원 등록", [("name", "이름", "", None), ("phone", "휴대폰", "", None)])
        if data and run_safe(self, self.pos.members.register, data["name"], data["phone"]):
            self.refresh()

    def edit(self):
        mid = selected_id(self.tree)
        if mid is None:
            return
        m = self.pos.members.get(mid)
        data = ask_form(self, "회원 정보 수정", [("name", "이름", m["name"], None),
                                             ("phone", "휴대폰", m["phone"], None)])
        if data and run_safe(self, self.pos.members.update, mid, data["name"], data["phone"]):
            self.refresh()

    def withdraw(self):
        mid = selected_id(self.tree)
        if mid is None:
            return
        if messagebox.askyesno("확인", "탈퇴 처리하면 보유 포인트가 소멸됩니다. 계속할까요?", parent=self):
            run_safe(self, self.pos.members.withdraw, mid)
            self.refresh()

    def adjust(self):
        mid = selected_id(self.tree)
        if mid is None:
            return
        data = ask_form(self, "포인트 조정 (관리자)", [("delta", "증감 (예: 500, -300)", "", None),
                                                  ("reason", "사유", "관리자 조정", None)])
        if not data:
            return

        def do():
            self.pos.members.adjust_points(mid, int(data["delta"]), data["reason"] or "관리자 조정")
            self.pos.db.log("포인트 조정", f"회원 {mid} {data['delta']}")
            self.pos.db.commit()
        if run_safe(self, do):
            self.refresh()
