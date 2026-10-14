"""주방 디스플레이(KDS): 조리 대기열과 조리 시작/완료 처리."""
from __future__ import annotations

from datetime import datetime
from tkinter import ttk

from .. import config
from .widgets import COLORS, font, make_tree, run_safe

STATUS = {"WAITING": "대기", "COOKING": "조리중"}


class KitchenView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text="주방 조리 대기열", font=font(13, True)).pack(side="left")
        self.count_lbl = ttk.Label(head, text="", foreground=COLORS["muted"])
        self.count_lbl.pack(side="left", padx=10)
        ttk.Label(head, text="※ 15분 이상 대기 항목은 빨간색", foreground=COLORS["danger"]).pack(
            side="right")
        self.tree = make_tree(self, [
            ("no", "주문번호", 70), ("where", "테이블/포장", 100), ("name", "메뉴", 140),
            ("opt", "옵션 / 요청사항", 240), ("qty", "수량", 50), ("status", "상태", 70),
            ("ordered", "주문 시각", 80), ("wait", "경과", 60)], height=18, anchor_right=("qty", "wait"))
        self.tree.configure(selectmode="extended")
        self.tree.tag_configure("cooking", background="#fff4e5")
        self.tree.bind("<Double-1>", lambda e: self.complete())
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="조리 시작", style="Big.TButton", command=self.start).pack(
            side="left", expand=True, fill="x", padx=2)
        ttk.Button(bar, text="조리 완료 (더블클릭)", style="Accent.TButton", command=self.complete).pack(
            side="left", expand=True, fill="x", padx=2)
        ttk.Button(bar, text="해당 주문 전체 완료", style="Big.TButton",
                   command=self.complete_order).pack(side="left", expand=True, fill="x", padx=2)

    def refresh(self):
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        now = self.pos.db.now()
        queue = self.pos.orders.kitchen_queue()
        for it in queue:
            ordered = datetime.strptime(it["ordered_at"], config.DATETIME_FMT)
            wait = int((now - ordered).total_seconds() // 60)
            where = "포장" if it["order_type"] == "TAKEOUT" else f"테이블 {it['table_name']}"
            tags = ["cooking"] if it["status"] == "COOKING" else []
            if wait >= 15:
                tags.append("warn")
            self.tree.insert("", "end", iid=str(it["id"]), tags=tags, values=(
                f"#{it['order_no']:03d}", where, it["name"],
                " / ".join(x for x in (it["options"], f"요청: {it['memo']}" if it["memo"] else "") if x),
                it["qty"], STATUS[it["status"]],
                it["ordered_at"][11:16], f"{wait}분"))
        for iid in sel & set(self.tree.get_children()):
            self.tree.selection_add(iid)
        self.count_lbl.configure(text=f"대기 {len(queue)}건")

    def _selected(self):
        return [int(i) for i in self.tree.selection()]

    def start(self):
        for iid in self._selected():
            run_safe(self, self.pos.orders.start_cooking, iid)
        self.app.refresh_all()

    def complete(self):
        for iid in self._selected():
            run_safe(self, self.pos.orders.complete_item, iid)
        self.app.refresh_all()

    def complete_order(self):
        ids = self._selected()
        if not ids:
            return
        order_ids = {self.pos.db.scalar("SELECT order_id FROM order_items WHERE id=?", (i,))
                     for i in ids}
        for oid in order_ids:
            for it in self.pos.orders.items(oid):
                if it["status"] != "DONE":
                    self.pos.orders.complete_item(it["id"])
        self.app.refresh_all()
