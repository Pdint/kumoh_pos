"""관리자 모드 화면들: 메뉴 · 재고 · 테이블 · 할인/쿠폰 · 직원/급여 · 주문내역(환불) · 일일마감 · 설정."""
from __future__ import annotations

import os
import tkinter as tk
from datetime import date
from tkinter import messagebox, simpledialog, ttk

from .. import config
from ..services.discount import WEEKDAY_NAMES
from ..services.staff import KINDS, hours_between
from .widgets import (COLORS, TextDialog, ask_form, center, fill_tree, font, make_tree, mono,
                      run_safe, selected_id, won)

ORDER_STATUS = {"OPEN": "진행중", "PAID": "결제완료", "CANCELLED": "취소", "REFUNDED": "환불"}


def toolbar(parent, buttons):
    bar = ttk.Frame(parent)
    bar.pack(fill="x", pady=(0, 6))
    for text, cmd in buttons:
        ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 4))
    return bar


# ====================================================================== 메뉴
class MenuAdminView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos
        toolbar(self, [("+ 메뉴 추가", self.add), ("수정", self.edit), ("판매 중지(삭제)", self.delete),
                       ("품절 설정/해제", self.toggle_soldout), ("옵션 관리", self.manage_options),
                       ("분류 추가", self.add_category),
                       ("분류 삭제", self.delete_category)])
        self.tree = make_tree(self, [
            ("cat", "분류", 80), ("name", "메뉴", 110), ("price", "판매가", 70), ("cost", "원가", 70),
            ("margin", "이익률", 60), ("dc", "메뉴할인", 60), ("avail", "주문가능", 70),
            ("state", "상태", 80), ("opts", "옵션", 70), ("recipe", "레시피(재료 소모량)", 260)],
            height=20,
            anchor_right=("price", "cost", "margin", "dc", "avail"))
        self.tree.bind("<Double-1>", lambda e: self.edit())

    def refresh(self):
        rows, ids, tags = [], [], []
        for m in self.pos.menu.menus():
            avail = self.pos.menu.available_qty(m["id"])
            state = "수동 품절" if m["manual_soldout"] else ("재고 품절" if avail <= 0 else "판매중")
            recipe = ", ".join(f"{r['name']} {r['qty']:g}{r['unit']}" for r in self.pos.menu.recipe(m["id"]))
            rows.append((m["category"], m["name"], f"{m['price']:,}", f"{m['cost']:,}",
                         f"{(m['price'] - m['cost']) / m['price'] * 100:.0f}%",
                         f"{m['discount_rate']}%" if m["discount_rate"] else "-",
                         "∞" if avail >= 9999 else avail, state,
                         f"{len(self.pos.menu.options(m['id']))}개", recipe))
            ids.append(m["id"])
            tags.append("warn" if state != "판매중" else "")
        fill_tree(self.tree, rows, ids, tags)

    def add(self):
        MenuDialog(self, self.pos, None)
        self.app.refresh_all()

    def edit(self):
        mid = selected_id(self.tree)
        if mid:
            MenuDialog(self, self.pos, mid)
            self.app.refresh_all()

    def delete(self):
        mid = selected_id(self.tree)
        if mid and messagebox.askyesno("확인", "선택한 메뉴를 판매 중지할까요?\n(판매 이력은 유지됩니다)",
                                       parent=self):
            run_safe(self, self.pos.menu.delete_menu, mid)
            self.app.refresh_all()

    def toggle_soldout(self):
        mid = selected_id(self.tree)
        if mid:
            m = self.pos.menu.get(mid)
            run_safe(self, self.pos.menu.set_manual_soldout, mid, not m["manual_soldout"])
            self.app.refresh_all()

    def manage_options(self):
        mid = selected_id(self.tree)
        if mid is None:
            messagebox.showinfo("알림", "옵션을 관리할 메뉴를 선택하세요.", parent=self)
            return
        OptionAdminDialog(self, self.pos, mid)
        self.app.refresh_all()

    def add_category(self):
        name = simpledialog.askstring("분류 추가", "분류 이름", parent=self)
        if name:
            run_safe(self, self.pos.menu.add_category, name)
            self.app.refresh_all()

    def delete_category(self):
        cats = self.pos.menu.categories()
        data = ask_form(self, "분류 삭제", [("cat", "삭제할 분류", "", [c["name"] for c in cats])])
        if data and data["cat"]:
            cid = next(c["id"] for c in cats if c["name"] == data["cat"])
            run_safe(self, self.pos.menu.delete_category, cid)
            self.app.refresh_all()


class OptionAdminDialog(tk.Toplevel):
    """메뉴 옵션 관리: 그룹(하나만 선택) 또는 개별 선택 옵션, 추가금, 사용 재료."""

    def __init__(self, parent, pos, menu_id: int):
        super().__init__(parent)
        self.pos, self.menu_id = pos, menu_id
        menu = pos.menu.get(menu_id)
        self.title(f"옵션 관리 - {menu['name']}")
        self.transient(parent.winfo_toplevel())
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="같은 그룹 이름(예: 맵기)의 옵션은 하나만 고를 수 있고, "
                             "그룹이 비어 있으면 여러 개를 함께 고를 수 있습니다.",
                  foreground=COLORS["muted"]).pack(anchor="w")
        frame = ttk.Frame(body, height=220)
        frame.pack(fill="both", expand=True, pady=6)
        self.tree = make_tree(frame, [("group", "그룹", 80), ("name", "옵션", 120),
                                      ("price", "추가금", 70), ("ing", "사용 재료", 150)],
                              height=8, anchor_right=("price",))
        bar = ttk.Frame(body)
        bar.pack(fill="x")
        ttk.Button(bar, text="+ 옵션 추가", command=self.add).pack(side="left")
        ttk.Button(bar, text="삭제", command=self.delete).pack(side="left", padx=4)
        ttk.Button(bar, text="닫기", command=self.destroy).pack(side="right")
        self.load()
        center(self, parent)
        self.grab_set()
        self.wait_window()

    def load(self):
        opts = self.pos.menu.options(self.menu_id)
        fill_tree(self.tree, [
            (o["group_name"] or "-", o["name"], f"{o['price']:,}",
             f"{o['ingredient_name']} {o['ingredient_qty']:g}{o['ingredient_unit']}"
             if o["ingredient_name"] else "-") for o in opts], [o["id"] for o in opts])

    def add(self):
        ings = self.pos.menu.ingredients()
        groups = sorted({o["group_name"] for o in self.pos.menu.options(self.menu_id) if o["group_name"]})
        d = ask_form(self, "옵션 추가", [
            ("group", f"그룹 (비우면 개별 선택{', 기존: ' + ', '.join(groups) if groups else ''})", "", None),
            ("name", "옵션 이름", "", None), ("price", "추가금(원)", "0", None),
            ("ing", "사용 재료 (없으면 비움)", "", [""] + [i["name"] for i in ings]),
            ("qty", "재료 사용량", "1", None)], width=30)
        if not d:
            return
        ing_id = next((i["id"] for i in ings if i["name"] == d["ing"]), None)
        if run_safe(self, lambda: self.pos.menu.add_option(
                self.menu_id, d["name"], int(d["price"] or 0), d["group"], ing_id,
                float(d["qty"] or 0) if ing_id else 0)):
            self.load()

    def delete(self):
        oid = selected_id(self.tree)
        if oid and messagebox.askyesno("확인", "선택한 옵션을 삭제할까요?", parent=self):
            self.pos.menu.delete_option(oid)
            self.load()


class MenuDialog(tk.Toplevel):
    def __init__(self, parent, pos, menu_id):
        super().__init__(parent)
        self.pos, self.menu_id = pos, menu_id
        self.title("메뉴 수정" if menu_id else "메뉴 추가")
        self.transient(parent.winfo_toplevel())
        body = ttk.Frame(self, padding=12)
        body.pack()
        self.cats = pos.menu.categories()
        m = pos.menu.get(menu_id) if menu_id else None
        cat_name = next((c["name"] for c in self.cats if m and c["id"] == m["category_id"]),
                        self.cats[0]["name"] if self.cats else "")
        self.v = {
            "name": tk.StringVar(value=m["name"] if m else ""),
            "cat": tk.StringVar(value=cat_name),
            "price": tk.StringVar(value=str(m["price"]) if m else ""),
            "cost": tk.StringVar(value=str(m["cost"]) if m else "0"),
            "dc": tk.StringVar(value=str(m["discount_rate"]) if m else "0"),
        }
        for r, (key, label) in enumerate((("name", "메뉴 이름"), ("cat", "분류"), ("price", "판매가(원)"),
                                          ("cost", "원가(원)"),
                                          ("dc", f"메뉴 할인율(0~{config.MAX_MENU_DISCOUNT_RATE}%)"))):
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=3)
            if key == "cat":
                w = ttk.Combobox(body, textvariable=self.v[key], values=[c["name"] for c in self.cats],
                                 state="readonly", width=18)
            else:
                w = ttk.Entry(body, textvariable=self.v[key], width=20)
            w.grid(row=r, column=1, sticky="w", padx=6, pady=3)
        rf = ttk.LabelFrame(body, text=" 레시피: 메뉴 1개당 재료 사용량 (빈칸 = 사용 안 함) ", padding=8)
        rf.grid(row=0, column=2, rowspan=8, sticky="n", padx=(12, 0))
        current = {r["ingredient_id"]: r["qty"] for r in pos.menu.recipe(menu_id)} if menu_id else {}
        self.recipe_vars = {}
        ings = pos.menu.ingredients()
        per_col = (len(ings) + 1) // 2 or 1
        for i, ing in enumerate(ings):
            col = (i // per_col) * 2
            ttk.Label(rf, text=f"{ing['name']}({ing['unit']})").grid(row=i % per_col, column=col,
                                                                    sticky="w")
            var = tk.StringVar(value=f"{current[ing['id']]:g}" if ing["id"] in current else "")
            ttk.Entry(rf, textvariable=var, width=6).grid(row=i % per_col, column=col + 1,
                                                          padx=(4, 12), pady=1)
            self.recipe_vars[ing["id"]] = var
        btns = ttk.Frame(body)
        btns.grid(row=8, column=0, columnspan=3, pady=(12, 0))
        ttk.Button(btns, text="저장", style="Accent.TButton", command=self.save).pack(side="left", padx=4)
        ttk.Button(btns, text="취소", command=self.destroy).pack(side="left", padx=4)
        center(self, parent)
        self.grab_set()
        self.wait_window()

    def save(self):
        def do():
            recipe = {iid: float(v.get()) for iid, v in self.recipe_vars.items() if v.get().strip()}
            cat = next(c["id"] for c in self.cats if c["name"] == self.v["cat"].get())
            args = (self.v["name"].get(), cat, int(self.v["price"].get()),
                    int(self.v["cost"].get() or 0), int(self.v["dc"].get() or 0))
            if self.menu_id:
                self.pos.menu.update_menu(self.menu_id, *args, recipe=recipe)
            else:
                self.pos.menu.add_menu(*args, recipe=recipe)
        if run_safe(self, do):
            self.destroy()


# ====================================================================== 재고
class InventoryView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos
        toolbar(self, [("입고 (+)", lambda: self.adjust(1)), ("폐기/조정 (-)", lambda: self.adjust(-1)),
                       ("+ 재료 추가", self.add), ("재료 수정", self.edit), ("재료 삭제", self.delete)])
        self.summary = ttk.Label(self, text="", foreground=COLORS["danger"], font=font(10, True))
        self.summary.pack(anchor="w")
        pane = ttk.PanedWindow(self, orient="vertical")
        pane.pack(fill="both", expand=True)
        top = ttk.Frame(pane)
        pane.add(top, weight=3)
        self.tree = make_tree(top, [
            ("name", "재료", 100), ("stock", "현재 재고", 90), ("th", "경고 기준", 80),
            ("state", "상태", 70), ("menus", "사용 메뉴", 360)], height=14,
            anchor_right=("stock", "th"))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_logs())
        bottom = ttk.Frame(pane)
        pane.add(bottom, weight=2)
        ttk.Label(bottom, text="재고 변동 이력 (최근 100건)", font=font(10, True)).pack(anchor="w")
        self.logs = make_tree(bottom, [("at", "일시", 140), ("delta", "변동", 80),
                                       ("reason", "사유", 240)], height=6, anchor_right=("delta",))

    def refresh(self):
        uses = {}
        for r in self.pos.db.query(
                "SELECT r.ingredient_id, m.name FROM recipes r JOIN menus m ON m.id=r.menu_id "
                "WHERE m.active=1"):
            uses.setdefault(r["ingredient_id"], []).append(r["name"])
        rows, ids, tags = [], [], []
        low = 0
        for i in self.pos.menu.ingredients():
            is_low = i["stock"] <= i["threshold"]
            low += is_low
            state = "품절" if i["stock"] <= 0 else ("부족" if is_low else "정상")
            rows.append((i["name"], f"{i['stock']:g} {i['unit']}", f"{i['threshold']:g}", state,
                         ", ".join(uses.get(i["id"], []))))
            ids.append(i["id"])
            tags.append("warn" if is_low else "")
        fill_tree(self.tree, rows, ids, tags)
        self.summary.configure(text=f"[주의] 재고 부족 재료 {low}개 — 입고가 필요합니다." if low else "")
        self.logs.delete(*self.logs.get_children())

    def show_logs(self):
        iid = selected_id(self.tree)
        if iid is None:
            return
        rows = self.pos.db.query(
            "SELECT * FROM stock_logs WHERE ingredient_id=? ORDER BY id DESC LIMIT 100", (iid,))
        fill_tree(self.logs, [(r["at"], f"{r['delta']:+g}", r["reason"]) for r in rows])

    def adjust(self, sign):
        iid = selected_id(self.tree)
        if iid is None:
            messagebox.showinfo("알림", "재료를 선택하세요.", parent=self)
            return
        title = "입고" if sign > 0 else "폐기 / 실사 조정"
        data = ask_form(self, title, [("qty", "수량", "", None),
                                      ("reason", "사유", "입고" if sign > 0 else "폐기", None)])
        if data and run_safe(self, lambda: self.pos.menu.adjust_stock(
                iid, sign * float(data["qty"]), data["reason"] or title)):
            self.app.refresh_all()

    def add(self):
        data = ask_form(self, "재료 추가", [("name", "이름", "", None), ("unit", "단위", "개", None),
                                         ("stock", "초기 재고", "0", None),
                                         ("th", "경고 기준", str(config.DEFAULT_STOCK_THRESHOLD), None)])
        if data and run_safe(self, lambda: self.pos.menu.add_ingredient(
                data["name"], data["unit"], float(data["stock"]), float(data["th"]))):
            self.refresh()

    def edit(self):
        iid = selected_id(self.tree)
        if iid is None:
            return
        i = self.pos.db.query_one("SELECT * FROM ingredients WHERE id=?", (iid,))
        data = ask_form(self, "재료 수정", [("name", "이름", i["name"], None),
                                         ("unit", "단위", i["unit"], None),
                                         ("th", "경고 기준", f"{i['threshold']:g}", None)])
        if data and run_safe(self, lambda: self.pos.menu.update_ingredient(
                iid, data["name"], data["unit"], float(data["th"]))):
            self.app.refresh_all()

    def delete(self):
        iid = selected_id(self.tree)
        if iid and messagebox.askyesno("확인", "선택한 재료를 삭제할까요?", parent=self):
            run_safe(self, self.pos.menu.delete_ingredient, iid)
            self.refresh()


# ====================================================================== 테이블
class TableAdminView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos
        toolbar(self, [("+ 테이블 추가", self.add), ("수정", self.edit), ("삭제", self.delete)])
        ttk.Label(self, text=f"※ 테이블은 최대 {config.MAX_TABLES}개까지 등록할 수 있으며, "
                             "주문 진행 중인 테이블은 삭제할 수 없습니다.",
                  foreground=COLORS["muted"]).pack(anchor="w", pady=(0, 4))
        self.tree = make_tree(self, [("name", "테이블", 100), ("seats", "좌석 수", 80),
                                     ("state", "현재 상태", 200)], height=16, anchor_right=("seats",))

    def refresh(self):
        status = self.pos.orders.table_status()
        tables = self.pos.tables.tables()
        fill_tree(self.tree, [
            (t["name"], t["seats"],
             f"이용 중 ({status[t['id']]['amount']:,}원)" if t["id"] in status else "비어 있음")
            for t in tables], [t["id"] for t in tables])

    def add(self):
        n = len(self.pos.tables.tables()) + 1
        data = ask_form(self, "테이블 추가", [("name", "이름", f"T{n}", None), ("seats", "좌석 수", "4", None)])
        if data and run_safe(self, lambda: self.pos.tables.add_table(data["name"], int(data["seats"]))):
            self.app.refresh_all()

    def edit(self):
        tid = selected_id(self.tree)
        if tid is None:
            return
        t = self.pos.tables.get(tid)
        data = ask_form(self, "테이블 수정", [("name", "이름", t["name"], None),
                                          ("seats", "좌석 수", t["seats"], None)])
        if data and run_safe(self, lambda: self.pos.tables.update_table(tid, data["name"],
                                                                         int(data["seats"]))):
            self.app.refresh_all()

    def delete(self):
        tid = selected_id(self.tree)
        if tid and messagebox.askyesno("확인", "선택한 테이블을 삭제할까요?", parent=self):
            run_safe(self, self.pos.tables.delete_table, tid)
            self.app.refresh_all()


# ====================================================================== 할인 / 쿠폰
class DiscountView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos
        ttk.Label(self, text="할인 적용 순서: 메뉴별 할인 → 시간대 할인(가장 큰 1개) → 쿠폰(1장) → 포인트  "
                             "· 할인액은 10원 미만 절사", foreground=COLORS["muted"]).pack(anchor="w")
        f1 = ttk.LabelFrame(self, text=" 시간대별 할인 ", padding=6)
        f1.pack(fill="both", expand=True, pady=6)
        toolbar(f1, [("+ 추가", self.add_td), ("사용/중지", self.toggle_td), ("삭제", self.delete_td)])
        self.td_tree = make_tree(f1, [("name", "이름", 160), ("time", "시간", 110), ("rate", "할인율", 60),
                                      ("days", "요일", 120), ("active", "상태", 60)], height=5)
        f2 = ttk.LabelFrame(self, text=" 쿠폰 ", padding=6)
        f2.pack(fill="both", expand=True, pady=6)
        toolbar(f2, [("+ 추가", self.add_coupon), ("사용/중지", self.toggle_coupon),
                     ("삭제", self.delete_coupon)])
        self.cp_tree = make_tree(f2, [("code", "코드", 110), ("name", "이름", 160), ("value", "할인", 80),
                                      ("min", "최소 주문", 80), ("exp", "만료일", 90),
                                      ("uses", "사용/한도", 80), ("active", "상태", 60)], height=5)
        f3 = ttk.LabelFrame(self, text=" 메뉴별 할인 (메뉴 관리에서 설정) ", padding=6)
        f3.pack(fill="both", expand=True, pady=6)
        self.md_tree = make_tree(f3, [("name", "메뉴", 120), ("price", "정가", 80), ("rate", "할인율", 60),
                                      ("sale", "할인가", 80)], height=4,
                                 anchor_right=("price", "rate", "sale"))

    def refresh(self):
        tds = self.pos.discount.time_discounts()
        fill_tree(self.td_tree, [
            (t["name"], f"{t['start_time']}~{t['end_time']}", f"{t['rate']}%",
             "".join(WEEKDAY_NAMES[int(d)] for d in t["weekdays"]), "사용" if t["active"] else "중지")
            for t in tds], [t["id"] for t in tds], ["" if t["active"] else "muted" for t in tds])
        cps = self.pos.discount.coupons()
        fill_tree(self.cp_tree, [
            (c["code"], c["name"], f"{c['value']}%" if c["kind"] == "RATE" else f"{c['value']:,}원",
             f"{c['min_amount']:,}", c["expires"] or "무기한",
             f"{c['used_count']}/{c['max_uses'] or '∞'}", "사용" if c["active"] else "중지")
            for c in cps], [c["code"] for c in cps], ["" if c["active"] else "muted" for c in cps])
        menus = [m for m in self.pos.menu.menus() if m["discount_rate"]]
        fill_tree(self.md_tree, [
            (m["name"], f"{m['price']:,}", f"{m['discount_rate']}%",
             f"{m['price'] - self.pos.discount.item_discount(m['price'], m['discount_rate']):,}")
            for m in menus])

    def _sel(self, tree):
        sel = tree.selection()
        return sel[0] if sel else None

    def add_td(self):
        data = ask_form(self, "시간대 할인 추가", [
            ("name", "이름", "", None), ("start", "시작 (HH:MM)", "15:00", None),
            ("end", "종료 (HH:MM)", "17:00", None), ("rate", "할인율(%)", "10", None),
            ("days", "요일 (월화수목금토일 중)", "월화수목금", None)])
        if not data:
            return
        days = "".join(str(WEEKDAY_NAMES.index(c)) for c in data["days"] if c in WEEKDAY_NAMES)
        if run_safe(self, lambda: self.pos.discount.add_time_discount(
                data["name"], data["start"], data["end"], int(data["rate"]), days)):
            self.app.refresh_all()

    def toggle_td(self):
        sel = self._sel(self.td_tree)
        if sel:
            td = self.pos.db.query_one("SELECT active FROM time_discounts WHERE id=?", (int(sel),))
            self.pos.discount.set_time_discount_active(int(sel), not td["active"])
            self.app.refresh_all()

    def delete_td(self):
        sel = self._sel(self.td_tree)
        if sel and messagebox.askyesno("확인", "삭제할까요?", parent=self):
            self.pos.discount.delete_time_discount(int(sel))
            self.app.refresh_all()

    def add_coupon(self):
        data = ask_form(self, "쿠폰 추가", [
            ("code", "코드 (영문/숫자)", "", None), ("name", "이름", "", None),
            ("kind", "종류", "정액(원)", ["정액(원)", "정률(%)"]), ("value", "할인 값", "", None),
            ("min", "최소 주문 금액", "0", None), ("exp", "만료일 (YYYY-MM-DD, 빈칸=무기한)", "", None),
            ("max", "최대 사용 횟수 (0=무제한)", "0", None)])
        if data and run_safe(self, lambda: self.pos.discount.add_coupon(
                data["code"], data["name"], "RATE" if data["kind"].startswith("정률") else "AMOUNT",
                int(data["value"]), int(data["min"] or 0), data["exp"] or None, int(data["max"] or 0))):
            self.refresh()

    def toggle_coupon(self):
        sel = self._sel(self.cp_tree)
        if sel:
            c = self.pos.db.query_one("SELECT active FROM coupons WHERE code=?", (sel,))
            self.pos.discount.set_coupon_active(sel, not c["active"])
            self.refresh()

    def delete_coupon(self):
        sel = self._sel(self.cp_tree)
        if sel and messagebox.askyesno("확인", "삭제할까요?", parent=self):
            self.pos.discount.delete_coupon(sel)
            self.refresh()


# ====================================================================== 직원 / 급여
class StaffView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos
        today = date.today()
        self.ym = tk.StringVar(value=today.strftime("%Y-%m"))
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        t1 = ttk.Frame(nb, padding=8)
        nb.add(t1, text="직원 목록")
        toolbar(t1, [("+ 직원 등록", self.add), ("수정", self.edit), ("퇴사 처리", self.retire),
                     ("출근", lambda: self.clock(True)), ("퇴근", lambda: self.clock(False))])
        ttk.Label(t1, text=f"※ 2026년 최저시급 {config.MINIMUM_WAGE:,}원 · 정직원 통상시급 = 월급 ÷ "
                           f"{config.FULLTIME_MONTHLY_HOURS}시간", foreground=COLORS["muted"]).pack(anchor="w")
        self.staff_tree = make_tree(t1, [("name", "이름", 90), ("kind", "고용 형태", 80),
                                         ("pay", "급여 조건", 140), ("phone", "연락처", 120),
                                         ("now", "근무 상태", 140)], height=14)

        t2 = ttk.Frame(nb, padding=8)
        nb.add(t2, text="근무 기록")
        bar = toolbar(t2, [("+ 기록 직접 입력", self.add_log), ("기록 삭제", self.delete_log)])
        ttk.Label(bar, text="   조회 월 (YYYY-MM)").pack(side="left")
        ttk.Entry(bar, textvariable=self.ym, width=9).pack(side="left", padx=4)
        ttk.Button(bar, text="조회", command=self.refresh).pack(side="left")
        self.log_tree = make_tree(t2, [("name", "이름", 90), ("date", "근무일", 90), ("in", "출근", 70),
                                       ("out", "퇴근", 70), ("hours", "근무시간", 80)], height=16,
                                  anchor_right=("hours",))

        t3 = ttk.Frame(nb, padding=8)
        nb.add(t3, text="급여 계산")
        bar = ttk.Frame(t3)
        bar.pack(fill="x")
        ttk.Label(bar, text="급여 월 (YYYY-MM)").pack(side="left")
        ttk.Entry(bar, textvariable=self.ym, width=9).pack(side="left", padx=4)
        ttk.Button(bar, text="계산", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="CSV 내보내기", command=self.export_payroll).pack(side="right")
        ttk.Label(t3, text="아르바이트: 시급×근무시간 + 주휴수당(주 15시간 이상 근무 주, min(주 근무시간,40)÷40×8×시급) "
                           "+ 연장 가산(일 8시간 초과분 ×0.5)\n정직원: 월급 + 연장수당(일 8시간 초과분 × 통상시급 × 1.5)",
                  foreground=COLORS["muted"], justify="left").pack(anchor="w", pady=4)
        self.pay_tree = make_tree(t3, [
            ("name", "이름", 80), ("kind", "형태", 70), ("days", "근무일", 60), ("hours", "근무시간", 70),
            ("ot", "연장시간", 70), ("hourly", "통상시급", 80), ("base", "기본급", 100),
            ("otpay", "연장수당", 90), ("holiday", "주휴수당", 90), ("total", "지급 합계", 110)],
            height=10, anchor_right=("days", "hours", "ot", "hourly", "base", "otpay", "holiday", "total"))
        self.pay_total = ttk.Label(t3, text="", font=font(12, True))
        self.pay_total.pack(anchor="e", pady=6)

    def _ym(self):
        try:
            y, m = map(int, self.ym.get().split("-"))
            date(y, m, 1)
            return y, m
        except ValueError:
            messagebox.showwarning("입력 오류", "월은 YYYY-MM 형식으로 입력하세요.", parent=self)
            return None

    def refresh(self):
        working = {w["staff_id"]: w["clock_in"] for w in self.pos.staff.working_now()}
        staff = self.pos.staff.staff_list()
        fill_tree(self.staff_tree, [
            (s["name"], KINDS[s["kind"]],
             f"시급 {s['hourly_wage']:,}원" if s["kind"] == "PART" else f"월급 {s['monthly_salary']:,}원",
             s["phone"] or "", f"근무 중 ({working[s['id']][11:16]}~)" if s["id"] in working else "-")
            for s in staff], [s["id"] for s in staff])
        ym = self._ym()
        if not ym:
            return
        y, m = ym
        import calendar
        last = calendar.monthrange(y, m)[1]
        logs = self.pos.staff.logs(None, f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}")
        fill_tree(self.log_tree, [
            (lg["name"], lg["clock_in"][:10], lg["clock_in"][11:16],
             lg["clock_out"][11:16] if lg["clock_out"] else "근무 중",
             f"{hours_between(lg['clock_in'], lg['clock_out']):.2f}h" if lg["clock_out"] else "-")
            for lg in logs], [lg["id"] for lg in logs])
        self.payroll = self.pos.staff.payroll(y, m)
        fill_tree(self.pay_tree, [
            (p["name"], p["kind"], p["days"], f"{p['hours']:.1f}", f"{p['overtime_hours']:.1f}",
             f"{p['hourly']:,}", f"{p['base']:,}", f"{p['overtime_pay']:,}", f"{p['holiday_pay']:,}",
             f"{p['total']:,}") for p in self.payroll])
        self.pay_total.configure(text=f"{y}년 {m}월 인건비 합계: {won(sum(p['total'] for p in self.payroll))}")

    def _staff_form(self, s=None):
        return ask_form(self, "직원 정보", [
            ("name", "이름", s["name"] if s else "", None),
            ("kind", "고용 형태", KINDS[s["kind"]] if s else "아르바이트", list(KINDS.values())),
            ("hourly", "시급 (아르바이트)", s["hourly_wage"] if s else config.MINIMUM_WAGE, None),
            ("monthly", "월급 (정직원)", s["monthly_salary"] if s else 0, None),
            ("phone", "연락처", s["phone"] if s else "", None)])

    def _parse(self, d):
        kind = next(k for k, v in KINDS.items() if v == d["kind"])
        return d["name"], kind, int(d["hourly"] or 0), int(d["monthly"] or 0), d["phone"]

    def add(self):
        d = self._staff_form()
        if d and run_safe(self, lambda: self.pos.staff.add_staff(*self._parse(d))):
            self.refresh()

    def edit(self):
        sid = selected_id(self.staff_tree)
        if sid is None:
            return
        d = self._staff_form(self.pos.staff.get(sid))
        if d and run_safe(self, lambda: self.pos.staff.update_staff(sid, *self._parse(d))):
            self.refresh()

    def retire(self):
        sid = selected_id(self.staff_tree)
        if sid and messagebox.askyesno("확인", "퇴사 처리할까요?", parent=self):
            run_safe(self, self.pos.staff.retire, sid)
            self.refresh()

    def clock(self, is_in):
        sid = selected_id(self.staff_tree)
        if sid is None:
            return
        if is_in:
            run_safe(self, self.pos.staff.clock_in, sid)
        else:
            h = run_safe(self, self.pos.staff.clock_out, sid)
            if h:
                messagebox.showinfo("퇴근", f"근무시간 {h:.2f}시간이 기록되었습니다.", parent=self)
        self.refresh()

    def add_log(self):
        staff = self.pos.staff.staff_list()
        today = date.today().isoformat()
        d = ask_form(self, "근무 기록 입력", [
            ("staff", "직원", staff[0]["name"] if staff else "", [s["name"] for s in staff]),
            ("in", "출근 (YYYY-MM-DD HH:MM)", f"{today} 09:00", None),
            ("out", "퇴근 (YYYY-MM-DD HH:MM)", f"{today} 18:00", None)])
        if not d:
            return
        sid = next(s["id"] for s in staff if s["name"] == d["staff"])
        if run_safe(self, self.pos.staff.add_log, sid, d["in"] + ":00", d["out"] + ":00"):
            self.refresh()

    def delete_log(self):
        lid = selected_id(self.log_tree)
        if lid and messagebox.askyesno("확인", "선택한 근무 기록을 삭제할까요?", parent=self):
            self.pos.staff.delete_log(lid)
            self.refresh()

    def export_payroll(self):
        if not hasattr(self, "payroll"):
            return
        path = self.pos.reports.export_csv(
            f"payroll_{self.ym.get()}.csv",
            ["이름", "형태", "근무일", "근무시간", "연장시간", "통상시급", "기본급", "연장수당", "주휴수당", "합계"],
            [[p["name"], p["kind"], p["days"], p["hours"], p["overtime_hours"], p["hourly"], p["base"],
              p["overtime_pay"], p["holiday_pay"], p["total"]] for p in self.payroll])
        messagebox.showinfo("내보내기", f"저장되었습니다.\n{path}", parent=self)


# ====================================================================== 주문 내역 / 환불
class OrderHistoryView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos
        today = date.today().isoformat()
        self.start = tk.StringVar(value=today)
        self.end = tk.StringVar(value=today)
        self.status = tk.StringVar(value="전체")
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="주문일").pack(side="left")
        ttk.Entry(bar, textvariable=self.start, width=11).pack(side="left", padx=2)
        ttk.Label(bar, text="~").pack(side="left")
        ttk.Entry(bar, textvariable=self.end, width=11).pack(side="left", padx=2)
        ttk.Combobox(bar, textvariable=self.status, values=["전체", *ORDER_STATUS.values()],
                     state="readonly", width=9).pack(side="left", padx=6)
        ttk.Button(bar, text="조회", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="전체 환불 / 결제 취소", style="Danger.TButton", command=self.refund).pack(
            side="right")
        ttk.Button(bar, text="부분 환불", command=self.partial_refund).pack(side="right", padx=4)
        ttk.Button(bar, text="간이 영수증", command=lambda: self.receipt(False)).pack(side="right", padx=4)
        ttk.Button(bar, text="상세 영수증", command=lambda: self.receipt(True)).pack(side="right")
        self.tree = make_tree(self, [
            ("no", "번호", 50), ("created", "주문 시각", 130), ("where", "구분", 90),
            ("items", "메뉴", 260), ("total", "결제 금액", 90), ("method", "결제수단", 110),
            ("status", "상태", 70), ("reason", "취소/환불 사유", 140)], height=20,
            anchor_right=("total",))
        self.tree.tag_configure("refunded", foreground=COLORS["danger"])
        self.tree.bind("<Double-1>", lambda e: self.receipt(True))

    def refresh(self):
        inv = {v: k for k, v in ORDER_STATUS.items()}
        status = inv.get(self.status.get())
        try:
            orders = self.pos.orders.orders_between(self.start.get(), self.end.get(), status)
        except Exception:
            orders = []
        rows, ids, tags = [], [], []
        for o in orders:
            items = ", ".join(f"{i['name']}×{i['qty']}" for i in self.pos.orders.items(o["id"]))
            methods = ", ".join(sorted({config.PAYMENT_METHODS[p["method"]]
                                        for p in self.pos.payments.payments(o["id"])}))
            total = o["total"] if o["locked"] else self.pos.discount.calculate(o["id"])["total"]
            where = "포장" if o["order_type"] == "TAKEOUT" else \
                f"테이블 {self.pos.db.scalar('SELECT name FROM tables_ WHERE id=?', (o['table_id'],)) or ''}"
            status = ORDER_STATUS[o["status"]]
            reason = o["cancel_reason"] or ""
            if o["status"] == "PAID" and o["refunded_amount"]:
                status = "부분환불"
                reason = f"-{o['refunded_amount']:,}원 " + "; ".join(
                    r["reason"] for r in self.pos.payments.refunds(o["id"]))
            rows.append((o["order_no"], o["created_at"], where, items, f"{total:,}", methods or "-",
                         status, reason))
            ids.append(o["id"])
            tags.append("refunded" if o["status"] in ("REFUNDED", "CANCELLED") else "")
        fill_tree(self.tree, rows, ids, tags)

    def receipt(self, detailed):
        oid = selected_id(self.tree)
        if oid is None:
            return
        path = self.pos.reports.save_receipt(oid, detailed)
        TextDialog(self, "영수증 재출력", self.pos.reports.receipt(oid, detailed), path,
                   printer=self.pos.printer,
                   print_name=f"receipt_{oid}_{'full' if detailed else 'total'}")

    def partial_refund(self):
        oid = selected_id(self.tree)
        if oid is None:
            return
        order = self.pos.orders.get(oid)
        if order["status"] != "PAID":
            messagebox.showinfo("알림", "결제 완료된 주문만 부분 환불할 수 있습니다.", parent=self)
            return
        PartialRefundDialog(self, self.pos, oid)
        self.app.refresh_all()

    def refund(self):
        oid = selected_id(self.tree)
        if oid is None:
            return
        order = self.pos.orders.get(oid)
        if order["status"] == "OPEN" and not order["locked"]:
            messagebox.showinfo("알림", "결제 전 주문은 주문 화면에서 '주문 취소'를 사용하세요.", parent=self)
            return
        win = tk.Toplevel(self)
        win.title("결제 취소 / 환불")
        win.transient(self.winfo_toplevel())
        body = ttk.Frame(win, padding=12)
        body.pack()
        paid = sum(p["amount"] for p in self.pos.payments.payments(oid) if not p["refunded_at"])
        ttk.Label(body, text=f"주문 #{order['order_no']} · 환불 금액 {paid:,}원",
                  font=font(11, True)).pack(anchor="w")
        ttk.Label(body, text="환불 사유").pack(anchor="w", pady=(8, 0))
        reason = tk.StringVar(value="")
        ttk.Entry(body, textvariable=reason, width=36).pack(anchor="w")
        restock = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="재료 재고 복구 (조리 전 취소 시)", variable=restock).pack(anchor="w",
                                                                                   pady=6)
        ttk.Label(body, text="※ 적립 포인트 회수 · 사용 포인트 복원 · 쿠폰 사용 횟수 복원\n"
                             "※ 환불 금액은 환불일 매출 통계에서 차감됩니다.",
                  foreground=COLORS["muted"], justify="left").pack(anchor="w")

        def ok():
            amt = run_safe(win, self.pos.payments.refund, oid, reason.get(), restock.get())
            if amt is not None:
                win.destroy()
                messagebox.showinfo("환불 완료", f"{amt:,}원이 환불 처리되었습니다.", parent=self)
                self.app.refresh_all()
        ttk.Button(body, text="환불 실행", style="Danger.TButton", command=ok).pack(pady=(10, 0))
        center(win, self)
        win.grab_set()


class PartialRefundDialog(tk.Toplevel):
    """메뉴·수량을 골라 일부만 환불한다."""

    def __init__(self, parent, pos, order_id: int):
        super().__init__(parent)
        self.pos, self.order_id = pos, order_id
        self.title("부분 환불")
        self.transient(parent.winfo_toplevel())
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        order = pos.orders.get(order_id)
        tk.Label(body, text=f"주문 #{order['order_no']} · 결제 {order['total']:,}원"
                            + (f" · 기환불 {order['refunded_amount']:,}원" if order["refunded_amount"] else ""),
                 font=font(11, True)).grid(row=0, column=0, columnspan=4, sticky="w")
        self.vars: dict[int, tk.StringVar] = {}
        r = 1
        for it in pos.orders.items(order_id):
            left = it["qty"] - it["refunded_qty"]
            ttk.Label(body, text=pos.orders.item_label(it)).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Label(body, text=f"환불 가능 {left}/{it['qty']}").grid(row=r, column=1, padx=8)
            v = tk.StringVar(value="0")
            sp = ttk.Spinbox(body, from_=0, to=left, textvariable=v, width=4, command=self._update)
            if not left:
                sp.state(["disabled"])
            sp.grid(row=r, column=2)
            v.trace_add("write", lambda *a: self._update())
            self.vars[it["id"]] = v
            r += 1
        avail = pos.payments.refundable_by_method(order_id)
        self.methods = list(avail)
        ttk.Label(body, text="환불 수단").grid(row=r, column=0, sticky="w", pady=(10, 0))
        self.method = ttk.Combobox(body, state="readonly", width=24, values=[
            f"{config.PAYMENT_METHODS[m]} (최대 {a:,}원)" for m, a in avail.items()])
        if self.methods:
            self.method.current(0)
        self.method.grid(row=r, column=1, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(body, text="환불 사유").grid(row=r + 1, column=0, sticky="w", pady=4)
        self.reason = tk.StringVar()
        ttk.Entry(body, textvariable=self.reason, width=28).grid(row=r + 1, column=1, columnspan=3,
                                                                  sticky="w")
        self.restock = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="재료 재고 복구", variable=self.restock).grid(
            row=r + 2, column=0, columnspan=2, sticky="w")
        self.amount_lbl = tk.Label(body, text="", font=font(12, True), fg=COLORS["danger"])
        self.amount_lbl.grid(row=r + 3, column=0, columnspan=4, sticky="e", pady=(8, 0))
        ttk.Label(body, text="※ 주문 할인(시간대·쿠폰·포인트)은 금액 비율대로 나눠 환불합니다.\n"
                             "※ 선택한 수단의 금액이 부족하면 나머지는 다른 결제수단으로 환불합니다.\n"
                             "※ 남은 메뉴를 모두 고르면 전체 환불로 처리됩니다.",
                  foreground=COLORS["muted"], justify="left").grid(
            row=r + 4, column=0, columnspan=4, sticky="w", pady=(6, 0))
        btns = ttk.Frame(body)
        btns.grid(row=r + 5, column=0, columnspan=4, pady=(10, 0))
        ttk.Button(btns, text="환불 실행", style="Danger.TButton", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="닫기", command=self.destroy).pack(side="left", padx=4)
        self._update()
        center(self, parent)
        self.grab_set()
        self.wait_window()

    def _qtys(self) -> dict[int, int]:
        out = {}
        for iid, v in self.vars.items():
            try:
                out[iid] = int(v.get() or 0)
            except ValueError:
                out[iid] = 0
        return out

    def _update(self):
        qtys = self._qtys()
        if not any(qtys.values()):
            self.amount_lbl.configure(text="환불 금액 0원")
            return
        try:
            amt = self.pos.payments.item_refund_amount(self.order_id, qtys)
            self.amount_lbl.configure(text=f"환불 금액 {amt:,}원")
        except Exception:
            self.amount_lbl.configure(text="수량을 확인하세요")

    def _ok(self):
        method = self.methods[self.method.current()] if self.method.current() >= 0 else None
        amt = run_safe(self, self.pos.payments.refund_items, self.order_id, self._qtys(),
                       self.reason.get(), method, self.restock.get())
        if amt is not None:
            messagebox.showinfo("환불 완료", f"{amt:,}원을 환불 처리했습니다.", parent=self)
            self.destroy()


# ====================================================================== 일일 마감
class ClosingView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos
        self.day = tk.StringVar(value=date.today().isoformat())
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="영업일").pack(side="left")
        ttk.Entry(bar, textvariable=self.day, width=11).pack(side="left", padx=4)
        ttk.Button(bar, text="미리보기", command=self.preview).pack(side="left", padx=2)
        ttk.Button(bar, text="인쇄", command=self.print_report).pack(side="left", padx=2)
        ttk.Button(bar, text="일일 마감 실행", style="Accent.TButton", command=self.close_day).pack(
            side="left", padx=6)
        self.state_lbl = ttk.Label(bar, text="", font=font(10, True))
        self.state_lbl.pack(side="left", padx=10)
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=(8, 0))
        self.text = tk.Text(body, width=50, font=mono(10), bg="#fffef7", padx=12, pady=10)
        self.text.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(body, width=260)
        right.pack(side="left", fill="y", padx=(8, 0))
        ttk.Label(right, text="마감 이력 (선택 시 재출력)", font=font(10, True)).pack(anchor="w")
        self.hist = make_tree(right, [("date", "영업일", 100), ("at", "마감 시각", 140)], height=18)
        self.hist.bind("<<TreeviewSelect>>", self.show_history)

    def refresh(self):
        closings = self.pos.reports.closings()
        fill_tree(self.hist, [(c["business_date"], c["closed_at"]) for c in closings],
                  [i for i, _ in enumerate(closings)])
        self._closings = closings
        self.preview()

    def print_report(self):
        text = self.text.get("1.0", "end").strip()
        if not text:
            return
        result = self.pos.printer.print_text(
            text, f"closing_{self.day.get().strip()}_{self.pos.db.now().strftime('%H%M%S')}")
        messagebox.showinfo("인쇄", result["message"], parent=self)

    def _show(self, text):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.configure(state="disabled")

    def preview(self):
        d = self.day.get().strip()
        text = run_safe(self, self.pos.reports.closing_text, d)
        if text:
            closed = self.pos.reports.is_closed(d)
            self.state_lbl.configure(text="마감 완료" if closed else "미마감",
                                     foreground=COLORS["ok"] if closed else COLORS["accent"])
            self._show(text)

    def close_day(self):
        d = self.day.get().strip()
        if not messagebox.askyesno("일일 마감", f"{d} 영업을 마감할까요?", parent=self):
            return
        result = run_safe(self, self.pos.reports.close_day, d)
        if result:
            text, path = result
            messagebox.showinfo("마감 완료", f"마감 보고서가 저장되었습니다.\n{path}", parent=self)
            self.refresh()

    def show_history(self, _e=None):
        sel = self.hist.selection()
        if sel:
            self._show(self._closings[int(sel[0])]["report"])


# ====================================================================== 설정
class SettingsView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos
        top = ttk.Frame(self)
        top.pack(fill="x")
        info = ttk.LabelFrame(top, text=" 가게 정보 ", padding=8)
        info.pack(side="left", fill="y")
        for k, v in (("상호", f"{config.STORE_NAME} ({config.STORE_SUBTITLE})"),
                     ("주소", config.STORE_ADDRESS), ("전화", config.STORE_TEL),
                     ("사업자번호", config.BUSINESS_NO), ("DB 파일", self.pos.db.path)):
            ttk.Label(info, text=f"{k}: {v}").pack(anchor="w")
        sec = ttk.LabelFrame(top, text=" 보안 / 데이터 ", padding=8)
        sec.pack(side="left", fill="y", padx=8)
        ttk.Button(sec, text="관리자 비밀번호 변경", command=self.change_pw).pack(fill="x", pady=2)
        ttk.Button(sec, text="DB 백업", command=self.backup).pack(fill="x", pady=2)
        ttk.Button(sec, text="백업 파일로 복원", command=self.restore).pack(fill="x", pady=2)
        ttk.Button(sec, text="샘플 판매 데이터 생성 (30일)", command=self.demo).pack(fill="x", pady=2)
        prn = ttk.LabelFrame(top, text=" 프린터 ", padding=8)
        prn.pack(side="left", fill="y")
        self.printer_var = tk.BooleanVar(value=self.pos.printer.enabled)
        ttk.Checkbutton(prn, text="기본 프린터로 인쇄 사용", variable=self.printer_var,
                        command=lambda: self.pos.printer.set_enabled(self.printer_var.get())).pack(
            anchor="w")
        ttk.Button(prn, text="테스트 인쇄", command=self.test_print).pack(fill="x", pady=4)
        ttk.Label(prn, text="끄면 영수증·주문서를 prints 폴더에\n텍스트 파일로만 저장합니다.",
                  foreground=COLORS["muted"], justify="left").pack(anchor="w")
        ttk.Label(self, text="작업 로그 (최근 300건)", font=font(10, True)).pack(anchor="w", pady=(10, 0))
        self.tree = make_tree(self, [("at", "일시", 140), ("mode", "모드", 70), ("action", "작업", 140),
                                     ("detail", "내용", 400)], height=14)

    def refresh(self):
        rows = self.pos.auth.audit_logs()
        fill_tree(self.tree, [(r["at"], "관리자" if r["mode"] == "ADMIN" else "사용자", r["action"],
                               r["detail"] or "") for r in rows])

    def change_pw(self):
        d = ask_form(self, "비밀번호 변경", [("old", "현재 비밀번호", "", None),
                                         ("new", "새 비밀번호 (숫자 4~8자리)", "", None),
                                         ("new2", "새 비밀번호 확인", "", None)])
        if not d:
            return
        if d["new"] != d["new2"]:
            messagebox.showwarning("알림", "새 비밀번호가 일치하지 않습니다.", parent=self)
            return
        if run_safe(self, self.pos.auth.change_password, d["old"], d["new"]):
            messagebox.showinfo("완료", "비밀번호가 변경되었습니다.", parent=self)

    def backup(self):
        path = self.pos.reports.backup()
        messagebox.showinfo("백업 완료", f"백업 파일: {path}", parent=self)
        self.refresh()

    def test_print(self):
        text = "\n".join(["=" * 32, f"{config.STORE_NAME} 테스트 인쇄".center(26), "=" * 32,
                          f"출력 시각 {self.pos.db.now_str()}", "한글/숫자 정렬 확인 1234567890",
                          "=" * 32])
        result = self.pos.printer.print_text(text, "test_print")
        messagebox.showinfo("테스트 인쇄", result["message"], parent=self)
        self.refresh()

    def restore(self):
        from tkinter import filedialog
        backups = self.pos.reports.backups()
        initial = os.path.dirname(backups[0]) if backups else self.pos.reports.out_dir
        path = filedialog.askopenfilename(
            parent=self, title="복원할 백업 파일 선택", initialdir=initial,
            filetypes=[("POS 백업", "*.db"), ("모든 파일", "*.*")])
        if not path:
            return
        open_n = self.pos.db.scalar("SELECT COUNT(*) FROM orders WHERE status='OPEN'")
        warn = f"\n\n※ 현재 진행 중인 주문 {open_n}건도 백업 시점으로 되돌아갑니다." if open_n else ""
        if not messagebox.askyesno(
                "DB 복원", f"{os.path.basename(path)} 시점으로 모든 데이터를 되돌립니다.\n"
                           f"현재 데이터는 복원 전에 자동으로 백업됩니다.{warn}\n\n계속할까요?",
                parent=self):
            return
        safety = run_safe(self, self.pos.reports.restore, path)
        if safety:
            messagebox.showinfo("복원 완료", f"복원했습니다.\n복원 전 데이터 백업: {safety}\n\n"
                                         "관리자 비밀번호도 백업 시점의 값으로 돌아갑니다.", parent=self)
            self.app.after_restore()

    def demo(self):
        if not messagebox.askyesno("샘플 데이터", "최근 30일치 가상 판매/근태 데이터를 추가합니다.\n"
                                             "(통계 기능 시연용) 계속할까요?", parent=self):
            return
        from ..demo import generate
        n = generate(self.pos, 30, seed=int(date.today().strftime("%j")))
        messagebox.showinfo("완료", f"샘플 주문 {n}건을 생성했습니다.", parent=self)
        self.app.refresh_all()
