"""결제 창: 회원/쿠폰/포인트 적용, 전체·금액분할·메뉴별 분할 결제, 거스름돈, 영수증."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import config
from ..services.member import grade_of
from .widgets import COLORS, TextDialog, ask_form, center, font, run_safe, won


class PaymentDialog(tk.Toplevel):
    def __init__(self, parent, app, order_id: int):
        super().__init__(parent)
        self.app = app
        self.pos = app.pos
        self.order_id = order_id
        self.title("결제")
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.split_vars: dict[int, tk.StringVar] = {}

        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        left = ttk.Frame(root)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky="nsew")
        self._build_summary(left)
        self._build_benefits(left)
        self._build_history(left)
        self._build_pay(right)
        self.refresh()
        center(self, parent)
        self.grab_set()
        self.wait_window()

    # ---------------------------------------------------------------- 금액 요약
    def _build_summary(self, parent):
        box = tk.Frame(parent, bg="white", bd=1, relief="solid")
        box.pack(fill="x")
        self.title_lbl = tk.Label(box, text="", bg=COLORS["header"], fg="white", font=font(12, True),
                                  anchor="w", padx=10, pady=6)
        self.title_lbl.pack(fill="x")
        self.rows = {}
        for key, label in (("subtotal", "할인 전 금액"), ("menu_discount", "메뉴 할인"),
                           ("time_discount", "시간대 할인"), ("coupon_discount", "쿠폰 할인"),
                           ("points_used", "포인트 사용"), ("total", "최종 결제 금액"),
                           ("paid", "결제된 금액"), ("remaining", "남은 금액")):
            row = tk.Frame(box, bg="white")
            row.pack(fill="x", padx=10, pady=1)
            big = key in ("total", "remaining")
            tk.Label(row, text=label, bg="white", font=font(11 if big else 10, big)).pack(side="left")
            lbl = tk.Label(row, text="", bg="white", font=font(13 if big else 10, big),
                           fg=COLORS["danger"] if key == "remaining" else
                           (COLORS["primary"] if big else "#111827"))
            lbl.pack(side="right")
            self.rows[key] = lbl
        self.note_lbl = tk.Label(box, text="", bg="white", fg=COLORS["muted"], font=font(9),
                                 justify="left", anchor="w")
        self.note_lbl.pack(fill="x", padx=10, pady=(0, 6))

    # ---------------------------------------------------------------- 회원/쿠폰/포인트
    def _build_benefits(self, parent):
        box = ttk.LabelFrame(parent, text=" 회원 · 쿠폰 · 포인트 ", padding=8)
        box.pack(fill="x", pady=8)
        self.benefit_widgets = []
        ttk.Label(box, text="회원 휴대폰").grid(row=0, column=0, sticky="w")
        self.phone_var = tk.StringVar()
        e = ttk.Entry(box, textvariable=self.phone_var, width=16)
        e.grid(row=0, column=1, padx=4)
        b1 = ttk.Button(box, text="조회", width=6, command=self.find_member)
        b1.grid(row=0, column=2)
        b2 = ttk.Button(box, text="가입", width=6, command=self.register_member)
        b2.grid(row=0, column=3, padx=(4, 0))
        self.member_lbl = ttk.Label(box, text="비회원", foreground=COLORS["muted"])
        self.member_lbl.grid(row=1, column=0, columnspan=3, sticky="w", pady=2)
        b3 = ttk.Button(box, text="해제", width=6, command=lambda: self._attach(None))
        b3.grid(row=1, column=3, padx=(4, 0))

        ttk.Label(box, text="쿠폰 코드").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.coupon_var = tk.StringVar()
        e2 = ttk.Entry(box, textvariable=self.coupon_var, width=16)
        e2.grid(row=2, column=1, padx=4, pady=(6, 0))
        b4 = ttk.Button(box, text="적용", width=6, command=self.apply_coupon)
        b4.grid(row=2, column=2, pady=(6, 0))
        b5 = ttk.Button(box, text="해제", width=6, command=lambda: self.apply_coupon(clear=True))
        b5.grid(row=2, column=3, padx=(4, 0), pady=(6, 0))

        ttk.Label(box, text="포인트 사용").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.point_var = tk.StringVar(value="0")
        e3 = ttk.Entry(box, textvariable=self.point_var, width=16)
        e3.grid(row=3, column=1, padx=4, pady=(6, 0))
        b6 = ttk.Button(box, text="사용", width=6, command=self.use_points)
        b6.grid(row=3, column=2, pady=(6, 0))
        b7 = ttk.Button(box, text="전액", width=6, command=self.use_all_points)
        b7.grid(row=3, column=3, padx=(4, 0), pady=(6, 0))
        ttk.Label(box, text=f"※ {config.MIN_POINT_USE:,}P 이상, {config.POINT_USE_UNIT}P 단위 사용 · "
                            "결제 시작 후 변경 불가", foreground=COLORS["muted"],
                  font=font(8)).grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.benefit_widgets = [e, b1, b2, b3, e2, b4, b5, e3, b6, b7]

    def _build_history(self, parent):
        box = ttk.LabelFrame(parent, text=" 결제 내역 ", padding=6)
        box.pack(fill="both", expand=True)
        self.history = tk.Listbox(box, height=4, font=font(10))
        self.history.pack(fill="both", expand=True)

    # ---------------------------------------------------------------- 결제 입력
    def _build_pay(self, parent):
        mode_box = self.mode_box = ttk.LabelFrame(parent, text=" 결제 방식 ", padding=8)
        mode_box.pack(fill="x")
        self.mode = tk.StringVar(value="FULL")
        for val, text in (("FULL", "남은 금액 전체"), ("AMOUNT", "금액 분할"), ("MENU", "메뉴별 분할")):
            ttk.Radiobutton(mode_box, text=text, value=val, variable=self.mode,
                            command=self._on_mode).pack(side="left", padx=4)

        self.amount_box = ttk.LabelFrame(parent, text=" 이번 결제 금액 ", padding=8)
        self.amount_box.pack(fill="x", pady=6)
        self.amount_var = tk.StringVar()
        self.amount_entry = ttk.Entry(self.amount_box, textvariable=self.amount_var, width=14,
                                      font=font(12, True))
        self.amount_entry.pack(side="left")
        self.amount_var.trace_add("write", lambda *a: self._update_change())
        ttk.Label(self.amount_box, text="원  ").pack(side="left")
        ttk.Label(self.amount_box, text="N명 균등").pack(side="left")
        self.n_var = tk.StringVar(value="2")
        ttk.Spinbox(self.amount_box, from_=2, to=config.MAX_SPLIT_COUNT, textvariable=self.n_var,
                    width=3).pack(side="left", padx=2)
        ttk.Button(self.amount_box, text="1/N 계산", command=self.equal_split).pack(side="left")

        self.menu_box = ttk.LabelFrame(parent, text=" 결제할 메뉴 선택 ", padding=8)
        self.menu_inner = ttk.Frame(self.menu_box)
        self.menu_inner.pack(fill="x")
        self.menu_amount_lbl = ttk.Label(self.menu_box, text="", font=font(11, True))
        self.menu_amount_lbl.pack(anchor="e", pady=(6, 0))

        self.method_box = ttk.LabelFrame(parent, text=" 결제 수단 ", padding=8)
        self.method_box.pack(fill="x", pady=6)
        self.method = tk.StringVar(value="CARD")
        for key, label in config.PAYMENT_METHODS.items():
            ttk.Radiobutton(self.method_box, text=label, value=key, variable=self.method,
                            command=self._on_method).pack(side="left", padx=6)

        self.transfer_box = ttk.LabelFrame(parent, text=" 계좌이체 ", padding=8)
        ttk.Label(self.transfer_box, text="아래 계좌로 입금받은 뒤 '결제하기'를 누르세요.").pack(anchor="w")
        tk.Label(self.transfer_box, text=config.STORE_BANK_ACCOUNT, font=font(12, True),
                 fg=COLORS["primary"]).pack(anchor="w", pady=(4, 0))

        self.cash_box = ttk.LabelFrame(parent, text=" 현금 ", padding=8)
        ttk.Label(self.cash_box, text="받은 금액").grid(row=0, column=0, sticky="w")
        self.recv_var = tk.StringVar()
        ttk.Entry(self.cash_box, textvariable=self.recv_var, width=14, font=font(12, True)).grid(
            row=0, column=1, columnspan=3, sticky="w", padx=4)
        self.recv_var.trace_add("write", lambda *a: self._update_change())
        for i, (text, add) in enumerate((("+1천", 1000), ("+5천", 5000), ("+1만", 10000),
                                         ("+5만", 50000))):
            ttk.Button(self.cash_box, text=text, width=6,
                       command=lambda a=add: self._add_cash(a)).grid(row=1, column=i, pady=4, padx=1)
        ttk.Button(self.cash_box, text="받을 금액과 동일", command=self._exact_cash).grid(
            row=2, column=0, columnspan=2, sticky="w")
        ttk.Button(self.cash_box, text="지우기", command=lambda: self.recv_var.set("")).grid(
            row=2, column=2, columnspan=2, sticky="e")
        self.change_lbl = tk.Label(self.cash_box, text="거스름돈 0원", font=font(14, True),
                                   fg=COLORS["ok"])
        self.change_lbl.grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))

        self.pay_btn = ttk.Button(parent, text="결제하기", style="Accent.TButton", command=self.pay)
        self.pay_btn.pack(fill="x", side="bottom", ipady=6)
        ttk.Button(parent, text="닫기", command=self.destroy).pack(fill="x", side="bottom", pady=4)
        self._on_mode()
        self._on_method()

    # ---------------------------------------------------------------- 상태 갱신
    def refresh(self):
        order = self.pos.orders.get(self.order_id)
        calc = self.pos.discount.calculate(self.order_id)
        self.calc = calc
        self.title_lbl.configure(text=self.pos.orders.label(order))
        for key, lbl in self.rows.items():
            v = calc[key]
            neg = key in ("menu_discount", "time_discount", "coupon_discount", "points_used") and v
            lbl.configure(text=("-" if neg else "") + won(v))
        notes = []
        if calc["time_discount_name"]:
            notes.append(f"시간대 할인: {calc['time_discount_name']}")
        if calc["coupon_name"]:
            notes.append(f"쿠폰: {calc['coupon_name']}")
        notes += calc["warnings"]
        self.note_lbl.configure(text="\n".join(notes))

        if order["member_id"]:
            m = self.pos.members.get(order["member_id"])
            grade, rate = grade_of(m["total_spent"])
            self.member_lbl.configure(
                text=f"{m['name']} ({m['phone']}) · {grade} {rate}% 적립 · 보유 {m['points']:,}P",
                foreground=COLORS["primary"])
        else:
            self.member_lbl.configure(text="비회원", foreground=COLORS["muted"])
        if order["coupon_code"] and not self.coupon_var.get():
            self.coupon_var.set(order["coupon_code"])
        state = "disabled" if order["locked"] else "normal"
        for w in self.benefit_widgets:
            w.configure(state=state)

        self.history.delete(0, "end")
        for p in self.pos.payments.payments(self.order_id):
            extra = f" (받은 {p['received']:,} / 거스름 {p['change']:,})" if p["method"] == "CASH" else ""
            self.history.insert("end", f"{p['paid_at'][11:]}  {config.PAYMENT_METHODS[p['method']]}"
                                       f"  {p['amount']:,}원{extra}")
        self._render_menu_split()
        if self.mode.get() == "FULL":
            self.amount_var.set(str(calc["remaining"]))
        self._update_change()

    def _render_menu_split(self):
        for w in self.menu_inner.winfo_children():
            w.destroy()
        self.split_vars = {}
        for r, it in enumerate(self.pos.orders.items(self.order_id)):
            left = it["qty"] - it["paid_qty"]
            ttk.Label(self.menu_inner, text=it["name"]).grid(row=r, column=0, sticky="w")
            ttk.Label(self.menu_inner, text=f"미결제 {left}/{it['qty']}").grid(row=r, column=1, padx=8)
            var = tk.StringVar(value="0")
            sp = ttk.Spinbox(self.menu_inner, from_=0, to=left, textvariable=var, width=4,
                             state="normal" if left else "disabled",
                             command=self._update_menu_amount)
            sp.grid(row=r, column=2)
            var.trace_add("write", lambda *a: self._update_menu_amount())
            self.split_vars[it["id"]] = var
        self._update_menu_amount()

    def _menu_qtys(self) -> dict[int, int]:
        out = {}
        for iid, v in self.split_vars.items():
            try:
                out[iid] = int(v.get() or 0)
            except ValueError:
                out[iid] = 0
        return out

    def _update_menu_amount(self):
        if self.mode.get() != "MENU":
            return
        qtys = self._menu_qtys()
        if not any(qtys.values()):
            self.menu_amount_lbl.configure(text="선택 금액 0원")
            self.amount_var.set("0")
            return
        try:
            amt = self.pos.payments.split_amount_for_items(self.order_id, qtys)
        except Exception:
            self.menu_amount_lbl.configure(text="수량을 확인하세요")
            return
        self.menu_amount_lbl.configure(text=f"선택 금액 {amt:,}원 (주문 할인 비율 반영)")
        self.amount_var.set(str(amt))

    def _on_mode(self):
        mode = self.mode.get()
        if mode == "MENU":
            self.amount_box.pack_forget()
            self.menu_box.pack(fill="x", pady=6, after=self.mode_box)
            self._update_menu_amount()
        else:
            self.menu_box.pack_forget()
            self.amount_box.pack(fill="x", pady=6, after=self.mode_box)
            self.amount_entry.configure(state="normal" if mode == "AMOUNT" else "disabled")
            if mode == "FULL" and hasattr(self, "calc"):
                self.amount_var.set(str(self.calc["remaining"]))

    def _on_method(self):
        method = self.method.get()
        if method == "CASH":
            self.cash_box.pack(fill="x", pady=6, after=self.method_box)
        else:
            self.cash_box.pack_forget()
        if method == "TRANSFER":
            self.transfer_box.pack(fill="x", pady=6, after=self.method_box)
        else:
            self.transfer_box.pack_forget()

    def _amount(self) -> int:
        try:
            return int(self.amount_var.get().replace(",", "") or 0)
        except ValueError:
            return 0

    def _update_change(self):
        try:
            recv = int(self.recv_var.get().replace(",", "") or 0)
        except ValueError:
            recv = 0
        diff = recv - self._amount()
        if diff >= 0:
            self.change_lbl.configure(text=f"거스름돈 {diff:,}원", fg=COLORS["ok"])
        else:
            self.change_lbl.configure(text=f"{-diff:,}원 부족", fg=COLORS["danger"])

    def _add_cash(self, add: int):
        try:
            cur = int(self.recv_var.get().replace(",", "") or 0)
        except ValueError:
            cur = 0
        self.recv_var.set(str(cur + add))

    def _exact_cash(self):
        self.recv_var.set(str(self._amount()))

    def equal_split(self):
        try:
            n = int(self.n_var.get())
        except ValueError:
            return
        paid_count = len(self.pos.payments.payments(self.order_id))
        left = max(1, n - paid_count)
        remaining = self.calc["remaining"]
        per = -(-remaining // left)          # 올림
        per = -(-per // 10) * 10             # 10원 단위 올림
        self.amount_var.set(str(min(per, remaining)))

    # ---------------------------------------------------------------- 회원 처리
    def _attach(self, member_id):
        if run_safe(self, self.pos.payments.attach_member, self.order_id, member_id):
            self.point_var.set("0")
            self.refresh()

    def find_member(self):
        m = run_safe(self, self.pos.members.find_by_phone, self.phone_var.get())
        if m is True:
            messagebox.showinfo("회원 조회", "등록되지 않은 번호입니다. '가입'을 눌러 등록하세요.",
                                parent=self)
        elif m:
            self._attach(m["id"])

    def register_member(self):
        data = ask_form(self, "회원 가입", [("name", "이름", "", None),
                                         ("phone", "휴대폰", self.phone_var.get(), None)])
        if data:
            mid = run_safe(self, self.pos.members.register, data["name"], data["phone"])
            if mid:
                self._attach(mid)

    def apply_coupon(self, clear=False):
        code = None if clear else self.coupon_var.get().strip()
        if clear:
            self.coupon_var.set("")
        if run_safe(self, self.pos.payments.apply_coupon, self.order_id, code):
            self.point_var.set("0")
            self.refresh()

    def use_points(self):
        try:
            pts = int(self.point_var.get().replace(",", "") or 0)
        except ValueError:
            messagebox.showwarning("입력 오류", "포인트는 숫자로 입력하세요.", parent=self)
            return
        if run_safe(self, self.pos.payments.set_points, self.order_id, pts):
            self.refresh()

    def use_all_points(self):
        order = self.pos.orders.get(self.order_id)
        if not order["member_id"]:
            messagebox.showinfo("알림", "회원을 먼저 조회하세요.", parent=self)
            return
        have = self.pos.members.get(order["member_id"])["points"]
        payable = self.calc["total"] + self.calc["points_used"]
        pts = min(have, payable) // config.POINT_USE_UNIT * config.POINT_USE_UNIT
        self.point_var.set(str(pts))
        self.use_points()

    # ---------------------------------------------------------------- 결제 실행
    def pay(self):
        mode, method = self.mode.get(), self.method.get()
        kwargs = {}
        if mode == "MENU":
            kwargs["item_qtys"] = self._menu_qtys()
        elif mode == "AMOUNT":
            kwargs["amount"] = self._amount()
        if method == "CASH":
            try:
                kwargs["received"] = int(self.recv_var.get().replace(",", "") or 0)
            except ValueError:
                messagebox.showwarning("입력 오류", "받은 금액을 숫자로 입력하세요.", parent=self)
                return
            if not self.recv_var.get():
                kwargs["received"] = self._amount()
        result = run_safe(self, self.pos.payments.pay, self.order_id, method, **kwargs)
        if not result:
            return
        msg = f"{config.PAYMENT_METHODS[method]} {result['amount']:,}원 결제 완료"
        if method == "CASH":
            msg += f"\n거스름돈: {result['change']:,}원"
        self.recv_var.set("")
        if not result["completed"]:
            messagebox.showinfo("분할 결제", f"{msg}\n\n남은 금액: {result['remaining']:,}원",
                                parent=self)
            self.mode.set("AMOUNT" if mode == "AMOUNT" else mode)
            self.refresh()
            return
        if result["earned_points"]:
            msg += f"\n포인트 적립: {result['earned_points']:,}P"
        messagebox.showinfo("결제 완료", msg, parent=self)
        self.app.after_payment(self.order_id)
        self.ask_receipt()
        self.destroy()

    def ask_receipt(self):
        win = tk.Toplevel(self)
        win.title("영수증")
        win.transient(self)
        ttk.Label(win, text="영수증을 출력할까요?", font=font(11, True), padding=12).pack()
        bar = ttk.Frame(win, padding=(12, 0, 12, 12))
        bar.pack()

        def show(detailed):
            win.destroy()
            path = self.pos.reports.save_receipt(self.order_id, detailed)
            TextDialog(self.master, "영수증", self.pos.reports.receipt(self.order_id, detailed), path,
                       printer=self.pos.printer,
                       print_name=f"receipt_{self.order_id}_{'full' if detailed else 'total'}")
        ttk.Button(bar, text="상세 영수증\n(전체 메뉴)", command=lambda: show(True)).pack(side="left",
                                                                                padx=4)
        ttk.Button(bar, text="간이 영수증\n(총액만)", command=lambda: show(False)).pack(side="left",
                                                                               padx=4)
        ttk.Button(bar, text="출력 안 함", command=win.destroy).pack(side="left", padx=4, fill="y")
        center(win, self)
        win.grab_set()
        win.wait_window()
