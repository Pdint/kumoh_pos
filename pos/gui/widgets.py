"""GUI 공용 위젯/도우미."""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
from typing import Callable

from ..services import PosError

COLORS = {
    "bg": "#f4f5f7", "panel": "#ffffff", "primary": "#1f6feb", "primary_dark": "#1554b8",
    "accent": "#ff8a00", "danger": "#d93025", "ok": "#188038", "muted": "#6b7280",
    "header": "#1f2937", "table_empty": "#e8f0fe", "table_busy": "#ffe8cc",
    "table_locked": "#fde2e1", "soldout": "#d1d5db", "warn_row": "#fde2e1",
}

FONT_FAMILY = "TkDefaultFont"
MONO_FAMILY = "TkFixedFont"


def init_fonts(root: tk.Misc) -> None:
    """설치된 한글 글꼴을 찾아 기본 글꼴로 지정한다."""
    global FONT_FAMILY, MONO_FAMILY
    families = set(tkfont.families(root))
    # 영수증/보고서 정렬용 한글 고정폭 글꼴
    for name in ("D2Coding", "NanumGothicCoding", "굴림체", "GulimChe", "돋움체", "DotumChe",
                 "AppleGothic"):
        if name in families:
            MONO_FAMILY = name
            break
    for name in ("맑은 고딕", "Malgun Gothic", "Apple SD Gothic Neo", "AppleGothic",
                 "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR", "UnDotum"):
        if name in families:
            FONT_FAMILY = name
            break
    for fname in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(fname).configure(family=FONT_FAMILY, size=10)
        except tk.TclError:
            pass
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure(".", font=(FONT_FAMILY, 10), background=COLORS["bg"])
    style.configure("TNotebook.Tab", padding=(14, 6), font=(FONT_FAMILY, 10, "bold"))
    style.configure("Treeview", rowheight=26, font=(FONT_FAMILY, 10))
    style.configure("Treeview.Heading", font=(FONT_FAMILY, 10, "bold"))
    style.configure("TLabelframe.Label", font=(FONT_FAMILY, 10, "bold"))
    style.configure("Big.TButton", font=(FONT_FAMILY, 11, "bold"), padding=8)
    style.configure("Accent.TButton", font=(FONT_FAMILY, 11, "bold"), padding=8,
                    foreground="white", background=COLORS["primary"])
    style.map("Accent.TButton", background=[("disabled", "#cbd5e1"), ("active", COLORS["primary_dark"])],
              foreground=[("disabled", "#f8fafc")])
    style.configure("Danger.TButton", foreground="white", background=COLORS["danger"])
    style.map("Danger.TButton", background=[("active", "#a52714")])


def font(size: int = 10, bold: bool = False):
    return (FONT_FAMILY, size, "bold") if bold else (FONT_FAMILY, size)


def mono(size: int = 10):
    return (MONO_FAMILY, size)


def won(n: int | float) -> str:
    return f"{int(n):,}원"


def guard(parent: tk.Misc | None = None):
    """PosError를 메시지박스로 보여주는 데코레이터."""
    def deco(fn: Callable):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except PosError as e:
                messagebox.showwarning("알림", str(e), parent=parent)
            except ValueError:
                messagebox.showwarning("입력 오류", "숫자 입력란을 확인하세요.", parent=parent)
            return None
        return wrapper
    return deco


def run_safe(parent: tk.Misc, fn: Callable, *args, **kwargs):
    """fn 실행 중 발생한 업무 오류를 메시지박스로 표시.

    성공 시 결과(결과가 None이면 True), 실패 시 None을 반환한다.
    """
    try:
        result = fn(*args, **kwargs)
        return True if result is None else result
    except PosError as e:
        messagebox.showwarning("알림", str(e), parent=parent)
    except ValueError:
        messagebox.showwarning("입력 오류", "숫자 입력란을 확인하세요.", parent=parent)
    return None


def make_tree(parent: tk.Misc, columns: list[tuple[str, str, int]], height: int = 10,
              anchor_right: tuple[str, ...] = ()) -> ttk.Treeview:
    """columns: (key, 제목, 폭) 목록. 스크롤바가 붙은 Treeview를 parent에 pack 한다."""
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings",
                        height=height, selectmode="browse")
    for key, title, width in columns:
        tree.heading(key, text=title)
        tree.column(key, width=width, anchor="e" if key in anchor_right else "w",
                    stretch=True)
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tree.tag_configure("warn", background=COLORS["warn_row"])
    tree.tag_configure("muted", foreground=COLORS["muted"])
    return tree


def fill_tree(tree: ttk.Treeview, rows: list[tuple], iids: list | None = None,
              tags: list | None = None) -> None:
    tree.delete(*tree.get_children())
    for idx, row in enumerate(rows):
        kw = {}
        if iids is not None:
            kw["iid"] = str(iids[idx])
        if tags is not None and tags[idx]:
            kw["tags"] = (tags[idx],)
        tree.insert("", "end", values=row, **kw)


def selected_id(tree: ttk.Treeview) -> int | None:
    sel = tree.selection()
    return int(sel[0]) if sel else None


class FormDialog(tk.Toplevel):
    """간단한 입력 폼. fields: (key, 라벨, 기본값, 선택지 or None).

    result 에 {key: 문자열} 이 담긴다(취소 시 None).
    """

    def __init__(self, parent: tk.Misc, title: str, fields: list[tuple], width: int = 28):
        super().__init__(parent)
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.result: dict | None = None
        self.vars: dict[str, tk.StringVar] = {}
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        first = None
        for r, (key, label, default, choices) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=4, padx=(0, 10))
            var = tk.StringVar(value="" if default is None else str(default))
            if choices:
                w = ttk.Combobox(body, textvariable=var, values=choices, state="readonly",
                                 width=width - 2)
            else:
                w = ttk.Entry(body, textvariable=var, width=width)
            w.grid(row=r, column=1, sticky="ew", pady=4)
            first = first or w
            self.vars[key] = var
        btns = ttk.Frame(body)
        btns.grid(row=len(fields), column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btns, text="확인", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="취소", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        center(self, parent)
        if first:
            first.focus_set()
        self.grab_set()
        self.wait_window()

    def _ok(self):
        self.result = {k: v.get().strip() for k, v in self.vars.items()}
        self.destroy()


def ask_form(parent, title, fields, width=28) -> dict | None:
    return FormDialog(parent, title, fields, width).result


def center(win: tk.Toplevel, parent: tk.Misc) -> None:
    win.update_idletasks()
    top = parent.winfo_toplevel()
    x = top.winfo_rootx() + (top.winfo_width() - win.winfo_reqwidth()) // 2
    y = top.winfo_rooty() + (top.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{max(0, x)}+{max(0, y)}")


class TextDialog(tk.Toplevel):
    """영수증/보고서 표시용 고정폭 텍스트 창.

    printer와 print_name을 주면 '인쇄' 버튼이 생긴다. note는 하단 안내 문구.
    """

    def __init__(self, parent: tk.Misc, title: str, text: str, saved_path: str | None = None,
                 note: str | None = None, printer=None, print_name: str | None = None):
        super().__init__(parent)
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.text, self.printer, self.print_name = text, printer, print_name
        txt = tk.Text(self, width=48, height=min(40, text.count("\n") + 3), font=mono(10),
                      padx=12, pady=10, bg="#fffef7")
        txt.insert("1.0", text)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)
        bar = ttk.Frame(self, padding=6)
        bar.pack(fill="x")
        msg = note or (f"저장됨: {saved_path}" if saved_path else "")
        self.note = ttk.Label(bar, text=msg, foreground=COLORS["muted"], wraplength=360,
                              justify="left")
        self.note.pack(side="left")
        ttk.Button(bar, text="닫기", command=self.destroy).pack(side="right")
        if printer is not None and print_name:
            ttk.Button(bar, text="인쇄", command=self._print).pack(side="right", padx=4)
        center(self, parent)

    def _print(self):
        result = self.printer.print_text(self.text, self.print_name)
        self.note.configure(text=result["message"])


class BarChart(tk.Canvas):
    """간단한 막대 그래프."""

    def __init__(self, parent, height=220, color=None, **kw):
        super().__init__(parent, height=height, bg="white", highlightthickness=0, **kw)
        self.color = color or COLORS["primary"]
        self.data: list[tuple[str, float]] = []
        self.highlight: set[int] = set()
        self.bind("<Configure>", lambda e: self.redraw())

    def set_data(self, data: list[tuple[str, float]], highlight: set[int] | None = None,
                 fmt: Callable[[float], str] = lambda v: f"{int(v):,}") -> None:
        self.data, self.highlight, self.fmt = data, highlight or set(), fmt
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if not self.data or w < 50:
            self.create_text(w // 2, h // 2, text="데이터 없음", fill=COLORS["muted"], font=font(10))
            return
        left, right, top, bottom = 10, 10, 22, 34
        n = len(self.data)
        max_v = max(v for _, v in self.data) or 1
        slot = (w - left - right) / n
        bar_w = max(3, slot * 0.7)
        label_every = max(1, int(40 / slot) + 1) if slot < 40 else 1
        self.create_line(left, h - bottom, w - right, h - bottom, fill="#d1d5db")
        for i, (label, v) in enumerate(self.data):
            x0 = left + i * slot + (slot - bar_w) / 2
            bh = (h - top - bottom) * v / max_v
            color = COLORS["accent"] if i in self.highlight else self.color
            self.create_rectangle(x0, h - bottom - bh, x0 + bar_w, h - bottom, fill=color, width=0)
            if v and (n <= 16 or i in self.highlight):
                self.create_text(x0 + bar_w / 2, h - bottom - bh - 8, text=self.fmt(v),
                                 font=font(8), fill="#374151")
            if i % label_every == 0:
                self.create_text(x0 + bar_w / 2, h - bottom + 12, text=label, font=font(8),
                                 fill="#374151")
