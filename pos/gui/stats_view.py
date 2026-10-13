"""통계 화면: 기간별 요약/추이, 메뉴 순위·이익, 시간대 분석, 결제수단·주문유형, 처리 시간."""
from __future__ import annotations

import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox, ttk

from ..services.stats import GROUP_UNITS, PERIODS, period_range
from .widgets import COLORS, BarChart, fill_tree, font, make_tree, run_safe, won


class StatsView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos
        today = date.today()
        self.start_var = tk.StringVar(value=today.replace(day=1).isoformat())
        self.end_var = tk.StringVar(value=today.isoformat())
        self.unit_var = tk.StringVar(value="일별")
        self.sort_var = tk.StringVar(value="판매수량")

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        for key, label in PERIODS.items():
            ttk.Button(bar, text=label, command=lambda k=key: self.set_period(k)).pack(side="left",
                                                                                   padx=1)
        ttk.Button(bar, text="최근 30일", command=lambda: self.set_days(30)).pack(side="left", padx=1)
        ttk.Label(bar, text="   기간").pack(side="left")
        ttk.Entry(bar, textvariable=self.start_var, width=11).pack(side="left", padx=2)
        ttk.Label(bar, text="~").pack(side="left")
        ttk.Entry(bar, textvariable=self.end_var, width=11).pack(side="left", padx=2)
        ttk.Button(bar, text="조회", command=self.refresh).pack(side="left", padx=4)
        ttk.Button(bar, text="CSV 내보내기", command=self.export).pack(side="right")
        self.range_lbl = ttk.Label(bar, text="", foreground=COLORS["muted"])
        self.range_lbl.pack(side="right", padx=8)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, pady=(8, 0))
        self._build_summary()
        self._build_menu()
        self._build_hourly()
        self._build_methods()
        self._build_times()

    # ---------------------------------------------------------------- 기간
    def set_period(self, kind):
        s, e = period_range(kind, date.today())
        self.start_var.set(s)
        self.end_var.set(e)
        self.refresh()

    def set_days(self, n):
        self.start_var.set((date.today() - timedelta(days=n - 1)).isoformat())
        self.end_var.set(date.today().isoformat())
        self.refresh()

    @property
    def range(self):
        return self.start_var.get().strip(), self.end_var.get().strip()

    # ---------------------------------------------------------------- 요약
    def _build_summary(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="매출 요약")
        cards = ttk.Frame(tab)
        cards.pack(fill="x")
        self.cards = {}
        spec = [("gross_sales", "총 결제액"), ("refund_amount", "환불액"), ("net_sales", "순매출"),
                ("order_count", "판매 건수"), ("avg_ticket", "평균 객단가"), ("discount", "할인 합계"),
                ("cost", "원가 합계"), ("profit", "순이익"), ("cancel_count", "주문 취소")]
        for i, (key, label) in enumerate(spec):
            card = tk.Frame(cards, bg="white", bd=1, relief="solid", padx=10, pady=8)
            card.grid(row=i // 5, column=i % 5, sticky="nsew", padx=3, pady=3)
            cards.columnconfigure(i % 5, weight=1)
            tk.Label(card, text=label, bg="white", fg=COLORS["muted"], font=font(9)).pack(anchor="w")
            v = tk.Label(card, text="-", bg="white", font=font(14, True),
                         fg=COLORS["primary"] if key in ("net_sales", "profit") else "#111827")
            v.pack(anchor="w")
            self.cards[key] = v
        head = ttk.Frame(tab)
        head.pack(fill="x", pady=(10, 2))
        ttk.Label(head, text="매출 추이", font=font(11, True)).pack(side="left")
        cb = ttk.Combobox(head, values=list(GROUP_UNITS.values()), textvariable=self.unit_var,
                          state="readonly", width=8)
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.trend_chart = BarChart(tab, height=230)
        self.trend_chart.pack(fill="both", expand=True)
        self.trend_tree = make_tree(tab, [("label", "기간", 120), ("count", "판매 건수", 80),
                                          ("sales", "매출", 120)], height=5,
                                    anchor_right=("count", "sales"))

    # ---------------------------------------------------------------- 메뉴
    def _build_menu(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="메뉴별 · 인기 순위")
        head = ttk.Frame(tab)
        head.pack(fill="x")
        ttk.Label(head, text="정렬 기준").pack(side="left")
        cb = ttk.Combobox(head, values=["판매수량", "매출액", "이익"], textvariable=self.sort_var,
                          state="readonly", width=10)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.best_lbl = ttk.Label(head, text="", font=font(10, True), foreground=COLORS["accent"])
        self.best_lbl.pack(side="left", padx=16)
        self.menu_chart = BarChart(tab, height=180, color="#10b981")
        self.menu_chart.pack(fill="x", pady=6)
        self.menu_tree = make_tree(tab, [
            ("rank", "순위", 40), ("name", "메뉴", 120), ("qty", "판매수량", 70),
            ("orders", "주문 수", 70), ("revenue", "매출액", 100), ("cost", "원가", 90),
            ("profit", "이익", 100), ("margin", "이익률", 60)], height=10,
            anchor_right=("qty", "orders", "revenue", "cost", "profit", "margin"))

    # ---------------------------------------------------------------- 시간대
    def _build_hourly(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="시간대별 분석")
        self.peak_lbl = ttk.Label(tab, text="", font=font(11, True), foreground=COLORS["accent"])
        self.peak_lbl.pack(anchor="w")
        ttk.Label(tab, text="시간대별 주문 수").pack(anchor="w", pady=(8, 0))
        self.hour_count_chart = BarChart(tab, height=170)
        self.hour_count_chart.pack(fill="both", expand=True)
        ttk.Label(tab, text="시간대별 매출액").pack(anchor="w", pady=(8, 0))
        self.hour_sales_chart = BarChart(tab, height=170, color="#8b5cf6")
        self.hour_sales_chart.pack(fill="both", expand=True)

    # ---------------------------------------------------------------- 결제수단/유형
    def _build_methods(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="결제수단 · 주문유형")
        ttk.Label(tab, text="결제수단별 이용 비율과 매출", font=font(11, True)).pack(anchor="w")
        self.method_chart = BarChart(tab, height=140, color="#f59e0b")
        self.method_chart.pack(fill="x", pady=4)
        f1 = ttk.Frame(tab)
        f1.pack(fill="x")
        self.method_tree = make_tree(f1, [("label", "결제수단", 100), ("count", "건수", 70),
                                          ("count_ratio", "건수 비율", 80), ("amount", "금액", 110),
                                          ("ratio", "금액 비율", 80)], height=3,
                                     anchor_right=("count", "count_ratio", "amount", "ratio"))
        ttk.Label(tab, text="주문 유형별(매장/포장) 매출", font=font(11, True)).pack(anchor="w",
                                                                         pady=(14, 0))
        f2 = ttk.Frame(tab)
        f2.pack(fill="x")
        self.type_tree = make_tree(f2, [("label", "유형", 100), ("count", "판매 건수", 80),
                                        ("sales", "매출", 110), ("ratio", "비율", 80),
                                        ("avg", "평균 객단가", 100)], height=2,
                                   anchor_right=("count", "sales", "ratio", "avg"))

    # ---------------------------------------------------------------- 처리 시간
    def _build_times(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="주문 처리 시간")
        self.time_lbl = ttk.Label(tab, text="", font=font(11, True))
        self.time_lbl.pack(anchor="w", pady=(0, 8))
        ttk.Label(tab, text="※ 주문 처리 시간 = 주문 접수 → 모든 메뉴 조리 완료 / "
                            "조리 시간 = 메뉴 주문 → 해당 메뉴 조리 완료 (주방 화면 기준)",
                  foreground=COLORS["muted"]).pack(anchor="w")
        self.time_tree = make_tree(tab, [("name", "메뉴", 140), ("count", "조리 건수", 80),
                                         ("avg", "평균 조리 시간", 120)], height=14,
                                   anchor_right=("count", "avg"))

    # ---------------------------------------------------------------- 갱신
    def refresh(self):
        s, e = self.range
        summary = run_safe(self, self.pos.stats.summary, s, e)
        if not summary:
            return
        self.range_lbl.configure(text=f"{s} ~ {e}")
        for key, lbl in self.cards.items():
            v = summary[key]
            lbl.configure(text=f"{v:,}건" if key in ("order_count", "cancel_count") else won(v))

        unit = {v: k for k, v in GROUP_UNITS.items()}[self.unit_var.get()]
        trend = self.pos.stats.trend(s, e, unit)
        self.trend_chart.set_data([(t["label"][5:] if unit == "day" else t["label"], t["sales"])
                                   for t in trend], fmt=lambda v: f"{v / 10000:.0f}만"
                                  if v >= 10000 else f"{int(v):,}")
        fill_tree(self.trend_tree, [(t["label"], f"{t['count']:,}", f"{t['sales']:,}")
                                    for t in reversed(trend)])

        sort = {"판매수량": "qty", "매출액": "revenue", "이익": "profit"}[self.sort_var.get()]
        menus = self.pos.stats.menu_stats(s, e, sort)
        fill_tree(self.menu_tree, [
            (m["rank"], m["name"], f"{m['qty']:,}", f"{m['orders']:,}", f"{m['revenue']:,}",
             f"{m['cost']:,}", f"{m['profit']:,}",
             f"{m['profit'] / m['revenue'] * 100:.0f}%" if m["revenue"] else "-") for m in menus])
        self.menu_chart.set_data([(m["name"], m[sort]) for m in menus[:10]], highlight={0})
        if menus:
            by_qty = max(menus, key=lambda m: m["qty"])
            by_rev = max(menus, key=lambda m: m["revenue"])
            self.best_lbl.configure(text=f"🏆 최다 판매: {by_qty['name']}({by_qty['qty']}개)   "
                                         f"💰 최고 매출: {by_rev['name']}({by_rev['revenue']:,}원)")
        else:
            self.best_lbl.configure(text="판매 내역 없음")

        hourly = self.pos.stats.hourly(s, e)
        hours = [h for h in range(24) if hourly["hours"][h]["count"]] or list(range(10, 22))
        lo, hi = min(hours), max(hours)
        rng = list(range(lo, hi + 1))
        pc, ps = hourly["peak_count_hour"], hourly["peak_sales_hour"]
        self.hour_count_chart.set_data([(f"{h}시", hourly["hours"][h]["count"]) for h in rng],
                                       highlight={rng.index(pc)} if pc is not None else set())
        self.hour_sales_chart.set_data([(f"{h}시", hourly["hours"][h]["sales"]) for h in rng],
                                       highlight={rng.index(ps)} if ps is not None else set(),
                                       fmt=lambda v: f"{v / 10000:.0f}만" if v >= 10000 else f"{int(v):,}")
        if pc is not None:
            self.peak_lbl.configure(
                text=f"주문이 가장 많은 시간대: {pc}시~{pc + 1}시 ({hourly['hours'][pc]['count']}건)   |   "
                     f"매출이 가장 높은 시간대: {ps}시~{ps + 1}시 ({hourly['hours'][ps]['sales']:,}원)")
        else:
            self.peak_lbl.configure(text="판매 내역 없음")

        methods = self.pos.stats.by_payment_method(s, e)
        self.method_chart.set_data([(m["label"], m["amount"]) for m in methods])
        fill_tree(self.method_tree, [(m["label"], m["count"], f"{m['count_ratio']:.1f}%",
                                      f"{m['amount']:,}", f"{m['ratio']:.1f}%") for m in methods])
        types = self.pos.stats.by_order_type(s, e)
        fill_tree(self.type_tree, [(t["label"], t["count"], f"{t['sales']:,}", f"{t['ratio']:.1f}%",
                                    f"{t['sales'] // t['count']:,}" if t["count"] else "-")
                                   for t in types])

        times = self.pos.stats.processing_times(s, e)
        self.time_lbl.configure(
            text=f"완료 주문 {times['order_count']}건 · 평균 처리 시간 {times['avg_order_min']:.1f}분 · "
                 f"최장 {times['max_order_min']:.1f}분")
        fill_tree(self.time_tree, [(m["name"], m["count"], f"{m['avg_min']:.1f}분")
                                   for m in times["menus"]])
        self.last = {"summary": summary, "menus": menus, "trend": trend, "hourly": hourly,
                     "methods": methods, "types": types}

    def export(self):
        if not hasattr(self, "last"):
            self.refresh()
        s, e = self.range
        rows = [["[요약]"], *[[k, v] for k, v in self.last["summary"].items()], [],
                ["[메뉴별]", "순위", "판매수량", "주문수", "매출", "원가", "이익"]]
        rows += [[m["name"], m["rank"], m["qty"], m["orders"], m["revenue"], m["cost"], m["profit"]]
                 for m in self.last["menus"]]
        rows += [[], ["[추이]", "건수", "매출"]]
        rows += [[t["label"], t["count"], t["sales"]] for t in self.last["trend"]]
        rows += [[], ["[시간대]", "주문수", "매출"]]
        rows += [[f"{h}시", v["count"], v["sales"]] for h, v in self.last["hourly"]["hours"].items()]
        rows += [[], ["[결제수단]", "건수", "금액", "비율(%)"]]
        rows += [[m["label"], m["count"], m["amount"], round(m["ratio"], 1)] for m in self.last["methods"]]
        rows += [[], ["[주문유형]", "건수", "매출"]]
        rows += [[t["label"], t["count"], t["sales"]] for t in self.last["types"]]
        path = self.pos.reports.export_csv(f"stats_{s}_{e}.csv", ["항목", "값"], rows)
        messagebox.showinfo("내보내기", f"저장되었습니다.\n{path}", parent=self)
