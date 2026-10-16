"""시재 관리 화면: 개시 · 입출금 · 중간 점검 · 정산 · 정산 이력."""
from __future__ import annotations

import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox, ttk

from .. import config
from ..services.cash import CHECK_KINDS, KINDS
from .widgets import (COLORS, TextDialog, ask_form, center, fill_tree, font, make_tree, run_safe,
                      selected_id, won)


class CashView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.pos = app.pos

        top = ttk.Frame(self)
        top.pack(fill="x")
        card = tk.Frame(top, bg="white", bd=1, relief="solid", padx=14, pady=10)
        card.pack(side="left", fill="y")
        self.state_lbl = tk.Label(card, text="", bg="white", font=font(13, True))
        self.state_lbl.grid(row=0, column=0, columnspan=4, sticky="w")
        self.info_lbl = tk.Label(card, text="", bg="white", fg=COLORS["muted"], font=font(9))
        self.info_lbl.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 6))
        self.vals = {}
        spec = [("opening", "개시 금액", "+"), ("cash_sales", "현금 매출", "+"),
                ("cash_refunds", "현금 환불", "-"), ("cash_in", "입금", "+"),
                ("cash_out", "출금", "-")]
        for i, (key, label, sign) in enumerate(spec):
            tk.Label(card, text=f"{sign if i else ' '} {label}", bg="white", font=font(10)).grid(
                row=2 + i, column=0, sticky="w")
            v = tk.Label(card, text="-", bg="white", font=font(10))
            v.grid(row=2 + i, column=1, sticky="e", padx=(30, 0))
            self.vals[key] = v
        ttk.Separator(card).grid(row=7, column=0, columnspan=2, sticky="ew", pady=4)
        tk.Label(card, text="= 예상 시재", bg="white", font=font(12, True)).grid(row=8, column=0,
                                                                              sticky="w")
        self.expected_lbl = tk.Label(card, text="-", bg="white", font=font(15, True),
                                     fg=COLORS["primary"])
        self.expected_lbl.grid(row=8, column=1, sticky="e", padx=(30, 0))

        btns = ttk.Frame(top, padding=(14, 0))
        btns.pack(side="left", fill="y")
        self.buttons = {}
        for key, text, cmd, style in (
                ("open", "시재 개시", self.open_session, "Accent.TButton"),
                ("in", "입금 (잔돈 보충 등)", lambda: self.movement("IN"), "Big.TButton"),
                ("out", "출금 (지출·은행 입금)", lambda: self.movement("OUT"), "Big.TButton"),
                ("check", "시재 점검 (중간)", lambda: self.count("CHECK"), "Big.TButton"),
                ("close", "시재 정산 (마감)", lambda: self.count("CLOSE"), "Accent.TButton")):
            b = ttk.Button(btns, text=text, style=style, command=cmd, width=22)
            b.pack(fill="x", pady=3)
            self.buttons[key] = b
        ttk.Label(btns, text="예상 시재 = 개시 + 현금 매출 − 현금 환불 + 입금 − 출금\n"
                             f"과부족 {config.CASH_DIFF_ADMIN_LIMIT:,}원 이상은 관리자 모드에서만 정산",
                  foreground=COLORS["muted"], justify="left").pack(anchor="w", pady=(6, 0))

        cur = ttk.LabelFrame(top, text=" 현재 시재 입출금 · 점검 내역 ", padding=6)
        cur.pack(side="left", fill="both", expand=True)
        self.log_tree = make_tree(cur, [("at", "시각", 60), ("kind", "구분", 80),
                                        ("amount", "금액", 90), ("detail", "내용", 260)],
                                  height=10, anchor_right=("amount",))
        self.log_tree.tag_configure("bad", foreground=COLORS["danger"])

        hist = ttk.LabelFrame(self, text=" 시재 정산 이력 (최근 30일 · 더블클릭 = 정산서) ", padding=6)
        hist.pack(fill="both", expand=True, pady=(10, 0))
        self.hist_tree = make_tree(hist, [
            ("opened", "개시", 120), ("closed", "정산", 120), ("by", "담당", 70),
            ("opening", "개시 금액", 90), ("sales", "현금 매출", 90), ("expected", "예상 시재", 90),
            ("counted", "실제 시재", 90), ("diff", "과부족", 80), ("note", "사유", 200)],
            height=8, anchor_right=("opening", "sales", "expected", "counted", "diff"))
        self.hist_tree.tag_configure("bad", foreground=COLORS["danger"])
        self.hist_tree.bind("<Double-1>", lambda e: self.show_report())
        self.total_lbl = ttk.Label(hist, text="", font=font(10, True))
        self.total_lbl.pack(anchor="e", pady=(4, 0))

    # ---------------------------------------------------------------- 갱신
    def refresh(self):
        cur = self.pos.cash.current()
        is_open = cur is not None
        for key in ("in", "out", "check", "close"):
            self.buttons[key].state(["!disabled"] if is_open else ["disabled"])
        self.buttons["open"].state(["disabled"] if is_open else ["!disabled"])
        if is_open:
            s = self.pos.cash.summary()
            self.state_lbl.configure(text="시재 개시 중", fg=COLORS["ok"])
            self.info_lbl.configure(text=f"{s['opened_at']} 개시 · 담당 {s['opened_by'] or '-'}")
            self.vals["opening"].configure(text=won(s["opening"]))
            self.vals["cash_sales"].configure(text=f"{won(s['cash_sales'])} ({s['cash_sales_count']}건)")
            for key in ("cash_refunds", "cash_in", "cash_out"):
                self.vals[key].configure(text=won(s[key]))
            self.expected_lbl.configure(text=won(s["expected"]))
            rows, tags = [], []
            for m in self.pos.cash.movements(cur["id"]):
                rows.append((m["at"], KINDS[m["kind"]],
                             f"{'+' if m['kind'] == 'IN' else '-'}{m['amount']:,}", m["reason"]))
                tags.append("")
            for c in self.pos.cash.checks(cur["id"]):
                rows.append((c["at"], CHECK_KINDS[c["kind"]], f"{c['difference']:+,}",
                             f"예상 {c['expected_cash']:,} / 실제 {c['counted_cash']:,}"
                             + (f" ({c['note']})" if c["note"] else "")))
                tags.append("bad" if c["difference"] else "")
            order = sorted(range(len(rows)), key=lambda i: rows[i][0], reverse=True)
            fill_tree(self.log_tree, [(rows[i][0][11:16],) + rows[i][1:] for i in order],
                      tags=[tags[i] for i in order])
        else:
            self.state_lbl.configure(text="시재 미개시 — 현금 결제 불가", fg=COLORS["danger"])
            self.info_lbl.configure(text=f"다음 개시 기본 금액(이월): {won(self.pos.cash.default_opening())}")
            for v in self.vals.values():
                v.configure(text="-")
            self.expected_lbl.configure(text="-")
            self.log_tree.delete(*self.log_tree.get_children())
        end = date.today()
        sessions = [s for s in self.pos.cash.sessions((end - timedelta(days=30)).isoformat(),
                                                      end.isoformat()) if s["status"] == "CLOSED"]
        rows = []
        for s in sessions:
            sm = self.pos.cash.summary(s["id"])
            rows.append((s["opened_at"][:16], s["closed_at"][:16], s["closed_by"] or s["opened_by"] or "",
                         f"{s['opening_cash']:,}", f"{sm['cash_sales']:,}", f"{s['expected_cash']:,}",
                         f"{s['counted_cash']:,}", f"{s['difference']:+,}", s["note"] or ""))
        fill_tree(self.hist_tree, rows, [s["id"] for s in sessions],
                  ["bad" if s["difference"] else "" for s in sessions])
        total = sum(s["difference"] for s in sessions)
        self.total_lbl.configure(text=f"최근 30일 정산 {len(sessions)}회 · 과부족 합계 {total:+,}원")

    # ---------------------------------------------------------------- 동작
    def _staff_names(self) -> list[str]:
        return [s["name"] for s in self.pos.staff.staff_list()]

    def open_session(self):
        names = self._staff_names()
        d = ask_form(self, "시재 개시", [
            ("amount", "개시 금액(준비금)", self.pos.cash.default_opening(), None),
            ("by", "담당자", names[0] if names else "", names or None),
            ("note", "메모", "", None)])
        if d and run_safe(self, lambda: self.pos.cash.open(int(d["amount"].replace(",", "")),
                                                           d["by"], d["note"])):
            self.app.refresh_all()

    def movement(self, kind: str):
        title = "시재 입금" if kind == "IN" else "시재 출금"
        examples = "잔돈 보충" if kind == "IN" else "재료 구입 / 은행 입금"
        d = ask_form(self, title, [("amount", "금액(원)", "", None),
                                   ("reason", f"사유 (예: {examples})", "", None)])
        if d and run_safe(self, lambda: self.pos.cash.add_movement(
                kind, int(d["amount"].replace(",", "") or 0), d["reason"])):
            self.refresh()

    def count(self, kind: str):
        result = CountDialog(self, self.pos, kind, self._staff_names()).result
        if not result:
            return
        if kind == "CLOSE":
            sid = result["session_id"]
            TextDialog(self, "시재 정산서", self.pos.cash.report_text(sid), printer=self.pos.printer,
                       print_name=f"cash_{sid}")
        else:
            messagebox.showinfo("시재 점검", f"예상 {result['expected']:,}원 / 실제 {result['counted']:,}원\n"
                                         f"과부족 {result['difference']:+,}원을 기록했습니다.", parent=self)
        self.app.refresh_all()

    def show_report(self):
        sid = selected_id(self.hist_tree)
        if sid:
            TextDialog(self, "시재 정산서", self.pos.cash.report_text(sid), printer=self.pos.printer,
                       print_name=f"cash_{sid}")


class CountDialog(tk.Toplevel):
    """권종별 수량을 입력해 실제 시재를 세는 창 (중간 점검 / 정산 공용)."""

    def __init__(self, parent, pos, kind: str, staff_names: list[str]):
        super().__init__(parent)
        self.pos, self.kind = pos, kind
        self.result = None
        self.expected = pos.cash.summary()["expected"]
        self.title("시재 정산 (마감)" if kind == "CLOSE" else "시재 점검")
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="금전함 현금을 권종별로 세어 수량을 입력하세요.", font=font(11, True)).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.qty: dict[int, tk.StringVar] = {}
        self.sub: dict[int, ttk.Label] = {}
        for r, unit in enumerate(config.CASH_DENOMINATIONS, start=1):
            ttk.Label(body, text=f"{unit:,}원").grid(row=r, column=0, sticky="e", padx=(0, 8))
            v = tk.StringVar(value="0")
            ttk.Spinbox(body, from_=0, to=9999, textvariable=v, width=7, command=self._update).grid(
                row=r, column=1, pady=1)
            v.trace_add("write", lambda *a: self._update())
            lbl = ttk.Label(body, text="0원", width=14, anchor="e")
            lbl.grid(row=r, column=2, sticky="e")
            self.qty[unit], self.sub[unit] = v, lbl
        r = len(config.CASH_DENOMINATIONS) + 1
        ttk.Separator(body).grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
        self.total_lbl = tk.Label(body, text="", font=font(11, True))
        self.total_lbl.grid(row=r + 1, column=0, columnspan=3, sticky="e")
        self.diff_lbl = tk.Label(body, text="", font=font(13, True))
        self.diff_lbl.grid(row=r + 2, column=0, columnspan=3, sticky="e")
        ttk.Label(body, text="담당자").grid(row=r + 3, column=0, sticky="e", pady=(8, 0), padx=(0, 8))
        self.by = ttk.Combobox(body, values=staff_names, width=18)
        if staff_names:
            self.by.current(0)
        self.by.grid(row=r + 3, column=1, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(body, text="과부족 사유").grid(row=r + 4, column=0, sticky="e", padx=(0, 8))
        self.note = tk.StringVar()
        ttk.Entry(body, textvariable=self.note, width=28).grid(row=r + 4, column=1, columnspan=2,
                                                                sticky="w", pady=2)
        btns = ttk.Frame(body)
        btns.grid(row=r + 5, column=0, columnspan=3, pady=(10, 0))
        ttk.Button(btns, text="정산하고 시재 닫기" if kind == "CLOSE" else "점검 기록",
                   style="Accent.TButton", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="취소", command=self.destroy).pack(side="left", padx=4)
        self._update()
        center(self, parent)
        self.grab_set()
        self.wait_window()

    def _counts(self) -> dict[int, int]:
        out = {}
        for unit, v in self.qty.items():
            try:
                out[unit] = max(0, int(v.get() or 0))
            except ValueError:
                out[unit] = 0
        return out

    def _update(self):
        counts = self._counts()
        for unit, n in counts.items():
            self.sub[unit].configure(text=f"{unit * n:,}원")
        total = sum(u * n for u, n in counts.items())
        diff = total - self.expected
        self.total_lbl.configure(text=f"실제 {total:,}원  /  예상 {self.expected:,}원")
        if diff == 0:
            self.diff_lbl.configure(text="과부족 없음", fg=COLORS["ok"])
        else:
            self.diff_lbl.configure(text=f"{'과잉' if diff > 0 else '부족'} {diff:+,}원",
                                    fg=COLORS["danger"])

    def _ok(self):
        fn = self.pos.cash.close if self.kind == "CLOSE" else self.pos.cash.check
        result = run_safe(self, fn, self._counts(), self.note.get(), self.by.get())
        if result:
            self.result = result
            self.destroy()
