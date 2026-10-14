"""주문/결제 메인 화면: 테이블 현황 · 메뉴판 · 현재 주문."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .. import config
from .payment_dialog import PaymentDialog
from .widgets import COLORS, TextDialog, center, font, make_tree, run_safe, won

ITEM_STATUS = {"WAITING": "대기", "COOKING": "조리중", "DONE": "완료"}


class OrderView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=6)
        self.app = app
        self.pos = app.pos
        self.current_order_id: int | None = None
        self.current_table_id: int | None = None
        self.takeout_mode = False
        self.current_category: int | None = None
        self.table_buttons: dict[int, tk.Button] = {}
        self.menu_buttons: dict[int, tk.Button] = {}

        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_left()
        self._build_menu()
        self._build_order_panel()
        self.refresh()

    # ================================================================ 좌측: 테이블/포장
    def _build_left(self):
        left = ttk.Frame(self, width=250)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 6))
        tf = ttk.LabelFrame(left, text=" 테이블 ", padding=6)
        tf.pack(fill="x")
        self.table_grid = ttk.Frame(tf)
        self.table_grid.pack(fill="x")
        legend = ttk.Frame(tf)
        legend.pack(fill="x", pady=(6, 0))
        for text, color in (("빈 테이블", COLORS["table_empty"]), ("이용 중", COLORS["table_busy"]),
                            ("결제 중", COLORS["table_locked"])):
            tk.Label(legend, text="  ", bg=color).pack(side="left")
            ttk.Label(legend, text=text, font=font(8)).pack(side="left", padx=(2, 8))

        of = ttk.LabelFrame(left, text=" 포장 주문 ", padding=6)
        of.pack(fill="both", expand=True, pady=(6, 0))
        self.takeout_list = tk.Listbox(of, height=8, font=font(10), activestyle="none",
                                       exportselection=False)
        self.takeout_list.pack(fill="both", expand=True)
        self.takeout_list.bind("<<ListboxSelect>>", self._on_takeout_select)
        self.takeout_ids: list[int] = []
        btns = ttk.Frame(of)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="+ 새 포장 주문", command=self.new_takeout).pack(side="left", expand=True,
                                                                         fill="x")
        ttk.Button(btns, text="픽업 완료", command=self.pickup).pack(side="left", expand=True,
                                                                 fill="x", padx=(4, 0))

    def _render_tables(self):
        status = self.pos.orders.table_status()
        tables = self.pos.tables.tables()
        ids = [t["id"] for t in tables]
        if set(ids) != set(self.table_buttons):
            for w in self.table_grid.winfo_children():
                w.destroy()
            self.table_buttons = {}
            for idx, t in enumerate(tables):
                b = tk.Button(self.table_grid, width=9, height=3, relief="flat", font=font(9),
                              command=lambda tid=t["id"]: self.select_table(tid))
                b.grid(row=idx // 3, column=idx % 3, padx=2, pady=2, sticky="nsew")
                self.table_buttons[t["id"]] = b
        for t in tables:
            b = self.table_buttons[t["id"]]
            st = status.get(t["id"])
            if st:
                color = COLORS["table_locked"] if st["locked"] else COLORS["table_busy"]
                text = f"{t['name']}\n{st['amount']:,}원\n{st['count']}개 · {st['created_at'][11:16]}"
            else:
                color = COLORS["table_empty"]
                text = f"{t['name']}\n({t['seats']}인석)\n "
            selected = (not self.takeout_mode and t["id"] == self.current_table_id)
            b.configure(text=text, bg=color, activebackground=color,
                        relief="solid" if selected else "flat", bd=2 if selected else 0)

    def _render_takeout(self):
        orders = self.pos.orders.open_takeout_orders()
        self.takeout_ids = [o["id"] for o in orders]
        self.takeout_list.delete(0, "end")
        for o in orders:
            amount = self.pos.db.scalar(
                "SELECT COALESCE(SUM(unit_price*qty),0) FROM order_items WHERE order_id=?", (o["id"],))
            if o["status"] == "PAID":
                state = "조리완료·픽업대기" if o["completed_at"] else "결제완료·조리중"
            else:
                state = "미결제"
            self.takeout_list.insert("end", f"#{o['order_no']:03d}  {amount:,}원  [{state}]")
        if self.takeout_mode and self.current_order_id in self.takeout_ids:
            idx = self.takeout_ids.index(self.current_order_id)
            self.takeout_list.selection_clear(0, "end")
            self.takeout_list.selection_set(idx)

    def select_table(self, table_id: int):
        self._leave_current()
        self.takeout_mode = False
        self.current_table_id = table_id
        order = self.pos.orders.open_order_for_table(table_id)
        self.current_order_id = order["id"] if order else None
        self.takeout_list.selection_clear(0, "end")
        self.refresh()

    def _on_takeout_select(self, _e=None):
        sel = self.takeout_list.curselection()
        if not sel:
            return
        self._leave_current()
        self.takeout_mode = True
        self.current_table_id = None
        self.current_order_id = self.takeout_ids[sel[0]]
        self.refresh()

    def new_takeout(self):
        self._leave_current()
        self.takeout_mode = True
        self.current_table_id = None
        self.current_order_id = run_safe(self, self.pos.orders.create_order, None, "TAKEOUT")
        self.refresh()

    def pickup(self):
        sel = self.takeout_list.curselection()
        if not sel:
            messagebox.showinfo("알림", "픽업 처리할 포장 주문을 선택하세요.", parent=self)
            return
        oid = self.takeout_ids[sel[0]]
        if run_safe(self, self.pos.orders.mark_picked_up, oid):
            if self.current_order_id == oid:
                self.current_order_id = None
            self.refresh()

    def _leave_current(self):
        """빈 주문을 남기지 않도록 정리."""
        if self.current_order_id:
            self.pos.orders.discard_if_empty(self.current_order_id)

    # ================================================================ 가운데: 메뉴판
    def _build_menu(self):
        mid = ttk.Frame(self)
        mid.grid(row=0, column=1, sticky="nsew")
        self.cat_bar = ttk.Frame(mid)
        self.cat_bar.pack(fill="x")
        self.menu_grid = ttk.Frame(mid, padding=(0, 6))
        self.menu_grid.pack(fill="both", expand=True)

    def _render_categories(self):
        for w in self.cat_bar.winfo_children():
            w.destroy()
        cats = [(None, "전체")] + [(c["id"], c["name"]) for c in self.pos.menu.categories()]
        for cid, name in cats:
            active = cid == self.current_category
            tk.Button(self.cat_bar, text=name, font=font(10, True), relief="flat", padx=12, pady=6,
                      bg=COLORS["primary"] if active else "#e5e7eb",
                      fg="white" if active else "#111827",
                      command=lambda c=cid: self._set_category(c)).pack(side="left", padx=(0, 4))

    def _set_category(self, cid):
        self.current_category = cid
        self._render_categories()
        self._render_menus()

    def _render_menus(self):
        for w in self.menu_grid.winfo_children():
            w.destroy()
        self.menu_buttons = {}
        cols = 4
        for c in range(cols):
            self.menu_grid.columnconfigure(c, weight=1, uniform="menu")
        for idx, m in enumerate(self.pos.menu.menus(self.current_category)):
            avail = self.pos.menu.available_qty(m["id"])
            soldout = bool(m["manual_soldout"]) or avail <= 0
            price = f"{m['price']:,}원"
            if m["discount_rate"]:
                sale = m["price"] - self.pos.discount.item_discount(m["price"], m["discount_rate"])
                price = f"{sale:,}원 ({m['discount_rate']}%↓)"
            extra = "품절" if soldout else (f"남은수량 {avail}" if avail < 10 else " ")
            b = tk.Button(
                self.menu_grid, text=f"{m['name']}\n{price}\n{extra}", font=font(10, True),
                height=4, relief="flat", wraplength=150,
                bg=COLORS["soldout"] if soldout else "white",
                fg="#6b7280" if soldout else ("#b45309" if avail < 10 else "#111827"),
                activebackground="#dbeafe", state="disabled" if soldout else "normal",
                disabledforeground="#6b7280",
                command=lambda mid=m["id"]: self.add_menu(mid))
            b.grid(row=idx // cols, column=idx % cols, padx=3, pady=3, sticky="nsew")
            self.menu_buttons[m["id"]] = b

    def add_menu(self, menu_id: int, qty: int = 1):
        if self.current_order_id is None:
            if self.takeout_mode:
                self.current_order_id = run_safe(self, self.pos.orders.create_order, None, "TAKEOUT")
            elif self.current_table_id is not None:
                self.current_order_id = run_safe(self, self.pos.orders.create_order,
                                                 self.current_table_id)
            else:
                messagebox.showinfo("알림", "먼저 테이블을 선택하거나 포장 주문을 추가하세요.",
                                    parent=self)
                return
            if self.current_order_id is None:
                return
        options, memo = (), ""
        if self.pos.menu.options(menu_id):
            choice = OptionDialog(self, self.pos, menu_id).result
            if choice is None:
                self._leave_current_if_empty()
                return
            options, qty, memo = choice
        warnings = run_safe(self, self.pos.orders.add_item, self.current_order_id, menu_id, qty,
                            option_ids=options, memo=memo)
        self.refresh()
        if warnings:
            self.app.show_stock_warnings(warnings)

    def _leave_current_if_empty(self):
        if self.current_order_id:
            self.pos.orders.discard_if_empty(self.current_order_id)
            if not self.pos.db.query_one("SELECT 1 FROM orders WHERE id=?", (self.current_order_id,)):
                self.current_order_id = None
        self.refresh()

    # ================================================================ 우측: 현재 주문
    def _build_order_panel(self):
        right = ttk.Frame(self, width=400)
        right.grid(row=0, column=2, sticky="ns", padx=(6, 0))
        right.grid_propagate(False)
        right.pack_propagate(False)
        self.order_title = tk.Label(right, text="테이블을 선택하세요", font=font(13, True),
                                    bg=COLORS["header"], fg="white", anchor="w", padx=10, pady=8)
        self.order_title.pack(fill="x")
        tree_frame = ttk.Frame(right)
        tree_frame.pack(fill="both", expand=True, pady=4)
        self.tree = make_tree(tree_frame, [
            ("name", "메뉴", 120), ("price", "단가", 64), ("qty", "수량", 40),
            ("amount", "금액", 74), ("status", "상태", 56)], height=12,
            anchor_right=("price", "qty", "amount"))
        self.tree.tag_configure("done", foreground=COLORS["ok"])
        self.tree.tag_configure("cooking", foreground=COLORS["accent"])
        self.tree.tag_configure("unsent", foreground=COLORS["primary"])

        edit = ttk.Frame(right)
        edit.pack(fill="x")
        for text, cmd in (("+1", lambda: self.change_qty(1)), ("-1", lambda: self.change_qty(-1)),
                          ("수량 입력", self.input_qty), ("항목 삭제", self.delete_item)):
            ttk.Button(edit, text=text, command=cmd).pack(side="left", expand=True, fill="x", padx=1)
        edit2 = ttk.Frame(right)
        edit2.pack(fill="x", pady=(2, 0))
        ttk.Button(edit2, text="요청사항", command=self.edit_memo).pack(
            side="left", expand=True, fill="x", padx=1)
        ttk.Button(edit2, text="주방 주문서 출력", command=self.send_kitchen).pack(
            side="left", expand=True, fill="x", padx=1)

        summary = tk.Frame(right, bg="white", bd=1, relief="solid")
        summary.pack(fill="x", pady=6)
        self.sum_labels = {}
        for key, label in (("subtotal", "주문 금액"), ("discount", "할인 합계"), ("total", "결제 예정 금액")):
            row = tk.Frame(summary, bg="white")
            row.pack(fill="x", padx=10, pady=2)
            big = key == "total"
            tk.Label(row, text=label, bg="white", font=font(12 if big else 10, big)).pack(side="left")
            lbl = tk.Label(row, text="0원", bg="white", font=font(14 if big else 10, big),
                           fg=COLORS["primary"] if big else "#111827")
            lbl.pack(side="right")
            self.sum_labels[key] = lbl
        self.discount_note = tk.Label(summary, text="", bg="white", fg=COLORS["muted"], font=font(9),
                                      anchor="w", justify="left")
        self.discount_note.pack(fill="x", padx=10, pady=(0, 4))

        ops = ttk.Frame(right)
        ops.pack(fill="x")
        ttk.Button(ops, text="테이블 이동/합치기", command=self.merge_dialog).pack(
            side="left", expand=True, fill="x", padx=1)
        ttk.Button(ops, text="테이블 나누기", command=self.split_dialog).pack(
            side="left", expand=True, fill="x", padx=1)
        ttk.Button(ops, text="주문 취소", command=self.cancel_order).pack(
            side="left", expand=True, fill="x", padx=1)
        ttk.Button(right, text="결  제", style="Accent.TButton", command=self.open_payment).pack(
            fill="x", pady=(6, 0), ipady=6)

    def _render_order(self):
        oid = self.current_order_id
        if oid is not None:
            row = self.pos.db.query_one("SELECT * FROM orders WHERE id=?", (oid,))
            if not row or (row["status"] != "OPEN" and not
                           (row["order_type"] == "TAKEOUT" and row["status"] == "PAID")):
                self.current_order_id = oid = None
        if oid is None:
            if self.takeout_mode:
                title = "포장 주문 (새 주문)"
            elif self.current_table_id:
                title = f"테이블 {self.pos.tables.get(self.current_table_id)['name']} · 빈 테이블"
            else:
                title = "테이블을 선택하세요"
            self.order_title.configure(text=title)
            self.tree.delete(*self.tree.get_children())
            for lbl in self.sum_labels.values():
                lbl.configure(text="0원")
            self.discount_note.configure(text="")
            return
        order = self.pos.orders.get(oid)
        title = self.pos.orders.label(order)
        if order["status"] == "PAID":
            title += " · 결제완료"
        elif order["locked"]:
            title += " · 분할결제 중"
        self.order_title.configure(text=title)
        self.tree.delete(*self.tree.get_children())
        for it in self.pos.orders.items(oid):
            unit = it["unit_price"] - self.pos.discount.item_discount(it["unit_price"],
                                                                     it["discount_rate"])
            name = self.pos.orders.item_label(it)
            if it["discount_rate"]:
                name += f" ({it['discount_rate']}%↓)"
            if it["memo"]:
                name += f" *{it['memo']}"
            if it["paid_qty"]:
                name += f" [결제 {it['paid_qty']}]"
            tag = {"DONE": "done", "COOKING": "cooking"}.get(it["status"], "")
            state = ITEM_STATUS[it["status"]]
            if it["qty"] != it["ticket_qty"] and order["status"] == "OPEN":
                state, tag = "미전송", "unsent"
            self.tree.insert("", "end", iid=str(it["id"]), tags=(tag,), values=(
                name, f"{unit:,}", it["qty"], f"{unit * it['qty']:,}", state))
        calc = self.pos.discount.calculate(oid)
        disc = calc["subtotal"] - calc["total"]
        self.sum_labels["subtotal"].configure(text=won(calc["subtotal"]))
        self.sum_labels["discount"].configure(text=("-" if disc else "") + won(disc))
        self.sum_labels["total"].configure(text=won(calc["total"]))
        notes = []
        if calc["time_discount"]:
            notes.append(f"시간대 할인: {calc['time_discount_name']} -{calc['time_discount']:,}")
        if calc["coupon_discount"]:
            notes.append(f"쿠폰 할인 -{calc['coupon_discount']:,}")
        if calc["points_used"]:
            notes.append(f"포인트 사용 -{calc['points_used']:,}")
        if calc["paid"]:
            notes.append(f"결제됨 {calc['paid']:,} / 남은 금액 {calc['remaining']:,}")
        notes += calc["warnings"]
        self.discount_note.configure(text="\n".join(notes))

    def _selected_item(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("알림", "주문 항목을 선택하세요.", parent=self)
            return None
        return self.pos.db.query_one("SELECT * FROM order_items WHERE id=?", (int(sel[0]),))

    def _set_qty(self, item, qty):
        warnings = run_safe(self, self.pos.orders.set_item_qty, item["id"], qty)
        self.refresh()
        if qty and str(item["id"]) in self.tree.get_children():
            self.tree.selection_set(str(item["id"]))
        if warnings:
            self.app.show_stock_warnings(warnings)

    def change_qty(self, delta: int):
        item = self._selected_item()
        if item:
            if item["qty"] + delta == 0 and not messagebox.askyesno(
                    "확인", f"'{item['name']}'을(를) 삭제할까요?", parent=self):
                return
            self._set_qty(item, item["qty"] + delta)

    def input_qty(self):
        item = self._selected_item()
        if item:
            qty = simpledialog.askinteger("수량 변경", f"{item['name']} 수량 (0=삭제)", parent=self,
                                          initialvalue=item["qty"], minvalue=0,
                                          maxvalue=config.MAX_ITEM_QTY)
            if qty is not None:
                self._set_qty(item, qty)

    def delete_item(self):
        item = self._selected_item()
        if item and messagebox.askyesno("확인", f"'{item['name']}'을(를) 삭제할까요?", parent=self):
            self._set_qty(item, 0)

    def edit_memo(self):
        item = self._selected_item()
        if not item:
            return
        memo = simpledialog.askstring(
            "요청사항", f"{item['name']} 요청사항 ({config.MAX_MEMO_LENGTH}자 이내, 비우면 삭제)",
            parent=self, initialvalue=item["memo"])
        if memo is not None and run_safe(self, self.pos.orders.set_item_memo, item["id"], memo):
            self.refresh()

    def send_kitchen(self):
        if not self.current_order_id:
            messagebox.showinfo("알림", "주문을 먼저 선택하세요.", parent=self)
            return
        text = run_safe(self, self.pos.orders.kitchen_ticket, self.current_order_id)
        if not text:
            return
        order = self.pos.orders.get(self.current_order_id)
        result = self.pos.printer.print_text(
            text, f"kitchen_{order['created_at'][:10]}_{order['order_no']:03d}_"
                  f"{self.pos.db.now().strftime('%H%M%S')}")
        self.refresh()
        TextDialog(self, "주방 주문서", text, note=result["message"])

    def cancel_order(self):
        if not self.current_order_id:
            return
        order = self.pos.orders.get(self.current_order_id)
        if order["locked"]:
            messagebox.showinfo("알림", "결제가 진행된 주문은 관리자 모드의 '주문 내역'에서 "
                                "결제 취소해야 합니다.", parent=self)
            return
        reason = simpledialog.askstring("주문 취소", "취소 사유를 입력하세요.", parent=self,
                                        initialvalue="고객 요청")
        if reason is None:
            return
        if run_safe(self, self.pos.orders.cancel_order, self.current_order_id,
                    reason or "고객 요청"):
            self.current_order_id = None
        self.refresh()

    # ---------------------------------------------------------------- 병합/분할
    def _other_tables(self):
        return [t for t in self.pos.tables.tables() if t["id"] != self.current_table_id]

    def merge_dialog(self):
        if self.takeout_mode or not self.current_order_id:
            messagebox.showinfo("알림", "주문이 있는 테이블을 먼저 선택하세요.", parent=self)
            return
        tables = self._other_tables()
        status = self.pos.orders.table_status()
        labels = [f"{t['name']} ({'이용 중 → 합치기' if t['id'] in status else '빈 테이블 → 이동'})"
                  for t in tables]
        win = tk.Toplevel(self)
        win.title("테이블 이동 / 합치기")
        win.transient(self.winfo_toplevel())
        ttk.Label(win, text="현재 테이블의 주문을 어느 테이블로 옮길까요?", padding=10).pack()
        var = tk.StringVar()
        cb = ttk.Combobox(win, values=labels, textvariable=var, state="readonly", width=30)
        cb.pack(padx=10)

        def ok():
            if cb.current() < 0:
                return
            dst = tables[cb.current()]["id"]
            if run_safe(win, self.pos.orders.merge_tables, self.current_table_id, dst):
                win.destroy()
                self.select_table(dst)
        ttk.Button(win, text="확인", command=ok).pack(pady=10)
        center(win, self)
        win.grab_set()

    def split_dialog(self):
        if self.takeout_mode or not self.current_order_id:
            messagebox.showinfo("알림", "주문이 있는 테이블을 먼저 선택하세요.", parent=self)
            return
        items = self.pos.orders.items(self.current_order_id)
        tables = self._other_tables()
        win = tk.Toplevel(self)
        win.title("테이블 나누기")
        win.transient(self.winfo_toplevel())
        body = ttk.Frame(win, padding=12)
        body.pack()
        ttk.Label(body, text="옮길 메뉴와 수량을 선택하세요.", font=font(10, True)).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        spins = {}
        for r, it in enumerate(items, start=1):
            ttk.Label(body, text=it["name"]).grid(row=r, column=0, sticky="w", padx=(0, 10))
            ttk.Label(body, text=f"현재 {it['qty']}개").grid(row=r, column=1, padx=(0, 10))
            sv = tk.StringVar(value="0")
            ttk.Spinbox(body, from_=0, to=it["qty"], textvariable=sv, width=5).grid(row=r, column=2)
            spins[it["id"]] = sv
        r = len(items) + 1
        ttk.Label(body, text="옮길 테이블").grid(row=r, column=0, sticky="w", pady=(10, 0))
        cb = ttk.Combobox(body, values=[t["name"] for t in tables], state="readonly", width=12)
        cb.grid(row=r, column=1, columnspan=2, sticky="w", pady=(10, 0))

        def ok():
            if cb.current() < 0:
                messagebox.showinfo("알림", "옮길 테이블을 선택하세요.", parent=win)
                return
            try:
                moves = {iid: int(v.get()) for iid, v in spins.items()}
            except ValueError:
                messagebox.showwarning("입력 오류", "수량은 숫자로 입력하세요.", parent=win)
                return
            if run_safe(win, self.pos.orders.split_items, self.current_order_id, moves,
                        tables[cb.current()]["id"]):
                win.destroy()
                self.select_table(self.current_table_id)
        ttk.Button(body, text="나누기", command=ok).grid(row=r + 1, column=0, columnspan=3, pady=10)
        center(win, self)
        win.grab_set()

    # ---------------------------------------------------------------- 결제
    def open_payment(self):
        if not self.current_order_id or not self.pos.orders.items(self.current_order_id):
            messagebox.showinfo("알림", "결제할 주문이 없습니다.", parent=self)
            return
        if self.pos.orders.get(self.current_order_id)["status"] != "OPEN":
            messagebox.showinfo("알림", "이미 결제가 완료된 주문입니다.", parent=self)
            return
        PaymentDialog(self, self.app, self.current_order_id)
        self.refresh()

    # ---------------------------------------------------------------- 갱신
    def refresh(self):
        self._render_tables()
        self._render_takeout()
        self._render_categories()
        self._render_menus()
        self._render_order()


class OptionDialog(tk.Toplevel):
    """메뉴 옵션 선택 창. result = (옵션 id 목록, 수량, 요청사항) 또는 취소 시 None."""

    def __init__(self, parent, pos, menu_id: int):
        super().__init__(parent)
        self.pos = pos
        self.menu = pos.menu.get(menu_id)
        self.result = None
        self.title(f"{self.menu['name']} 옵션")
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        tk.Label(body, text=f"{self.menu['name']}  {self.menu['price']:,}원", font=font(13, True)).pack(
            anchor="w")
        self.group_vars: dict[str, tk.IntVar] = {}
        self.check_vars: dict[int, tk.IntVar] = {}
        self.options = {o["id"]: o for o in pos.menu.options(menu_id)}
        groups: dict[str, list] = {}
        singles = []
        for o in self.options.values():
            (groups.setdefault(o["group_name"], []) if o["group_name"] else singles).append(o)
        for g, opts in groups.items():
            box = ttk.LabelFrame(body, text=f" {g} (하나 선택) ", padding=6)
            box.pack(fill="x", pady=(8, 0))
            var = tk.IntVar(value=0)
            self.group_vars[g] = var
            ttk.Radiobutton(box, text="선택 안 함", value=0, variable=var,
                            command=self._update).pack(side="left", padx=4)
            for o in opts:
                ttk.Radiobutton(box, text=self._label(o), value=o["id"], variable=var,
                                command=self._update).pack(side="left", padx=4)
        if singles:
            box = ttk.LabelFrame(body, text=" 추가 선택 ", padding=6)
            box.pack(fill="x", pady=(8, 0))
            for o in singles:
                var = tk.IntVar(value=0)
                self.check_vars[o["id"]] = var
                avail = pos.menu.available_qty(menu_id, [o["id"]])
                cb = ttk.Checkbutton(box, text=self._label(o) + (" (재고 없음)" if avail <= 0 else ""),
                                     variable=var, command=self._update)
                if avail <= 0:
                    cb.state(["disabled"])
                cb.pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, text="수량").pack(side="left")
        self.qty = tk.StringVar(value="1")
        ttk.Spinbox(row, from_=1, to=config.MAX_ITEM_QTY, textvariable=self.qty, width=5,
                    command=self._update).pack(side="left", padx=6)
        self.qty.trace_add("write", lambda *a: self._update())
        ttk.Label(row, text="요청사항").pack(side="left", padx=(12, 0))
        self.memo = tk.StringVar()
        ttk.Entry(row, textvariable=self.memo, width=22).pack(side="left", padx=6)
        self.total_lbl = tk.Label(body, text="", font=font(12, True), fg=COLORS["primary"])
        self.total_lbl.pack(anchor="e", pady=(10, 0))
        btns = ttk.Frame(body)
        btns.pack(pady=(8, 0))
        ttk.Button(btns, text="담기", style="Accent.TButton", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="취소", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self._update()
        center(self, parent)
        self.grab_set()
        self.wait_window()

    @staticmethod
    def _label(o) -> str:
        return f"{o['name']} (+{o['price']:,})" if o["price"] else o["name"]

    def _selected(self) -> list[int]:
        ids = [v.get() for v in self.group_vars.values() if v.get()]
        ids += [oid for oid, v in self.check_vars.items() if v.get()]
        return ids

    def _update(self):
        try:
            qty = max(1, int(self.qty.get() or 1))
        except ValueError:
            qty = 1
        unit = self.menu["price"] + sum(self.options[i]["price"] for i in self._selected())
        self.total_lbl.configure(text=f"{unit:,}원 × {qty} = {unit * qty:,}원")

    def _ok(self):
        try:
            qty = int(self.qty.get())
        except ValueError:
            messagebox.showwarning("입력 오류", "수량은 숫자로 입력하세요.", parent=self)
            return
        self.result = (self._selected(), qty, self.memo.get())
        self.destroy()
