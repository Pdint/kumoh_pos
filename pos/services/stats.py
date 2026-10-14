"""매출 통계.

- 매출(결제)은 결제 시각, 환불(전체·부분)은 환불 시각 기준으로 집계한다.
- 메뉴별/시간대별/유형별 통계와 이익 계산은 전체 환불되지 않은(PAID) 주문만 대상으로 하며,
  부분 환불된 수량과 금액은 뺀다.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from .. import config
from . import PosError, Service

PERIODS = {"today": "오늘", "week": "이번 주", "month": "이번 달", "year": "올해"}
GROUP_UNITS = {"day": "일별", "week": "주별", "month": "월별", "year": "연도별"}


def period_range(kind: str, ref: date) -> tuple[str, str]:
    if kind == "today":
        start = end = ref
    elif kind == "week":
        start = ref - timedelta(days=ref.weekday())
        end = start + timedelta(days=6)
    elif kind == "month":
        start = ref.replace(day=1)
        end = ref.replace(day=calendar.monthrange(ref.year, ref.month)[1])
    elif kind == "year":
        start, end = ref.replace(month=1, day=1), ref.replace(month=12, day=31)
    else:
        raise PosError("알 수 없는 기간입니다.")
    return start.isoformat(), end.isoformat()


COMPARE_METRICS = [
    ("net_sales", "순매출"), ("order_count", "판매 건수"), ("avg_ticket", "평균 객단가"),
    ("discount", "할인 합계"), ("refund_amount", "환불액"), ("cost", "원가"), ("profit", "순이익"),
]


def previous_range(start: str, end: str) -> tuple[str, str]:
    """비교 기간: 한 달 전체면 전월, 한 해 전체면 전년, 월 초부터의 기간이면 지난달 같은 날짜,
    그 외에는 같은 길이의 직전 기간."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    last_day = calendar.monthrange(e.year, e.month)[1]
    if s.day == 1 and s.year == e.year and s.month == e.month and e.day == last_day:
        p_end = s - timedelta(days=1)
        return p_end.replace(day=1).isoformat(), p_end.isoformat()
    if (s.month, s.day) == (1, 1) and (e.month, e.day) == (12, 31) and s.year == e.year:
        return f"{s.year - 1}-01-01", f"{s.year - 1}-12-31"
    if s.day == 1 and s.year == e.year and s.month == e.month:
        # 이번 달 1일~오늘처럼 월 초부터의 기간 → 지난달 같은 날짜 구간
        prev_last = s - timedelta(days=1)
        return (prev_last.replace(day=1).isoformat(),
                prev_last.replace(day=min(e.day, prev_last.day)).isoformat())
    length = (e - s).days + 1
    p_end = s - timedelta(days=1)
    return (p_end - timedelta(days=length - 1)).isoformat(), p_end.isoformat()


def check_range(start: str, end: str) -> None:
    try:
        s = datetime.strptime(start, config.DATE_FMT)
        e = datetime.strptime(end, config.DATE_FMT)
    except ValueError:
        raise PosError("날짜는 YYYY-MM-DD 형식으로 입력하세요.") from None
    if s > e:
        raise PosError("시작일이 종료일보다 늦습니다.")


def _minutes(start: str, end: str) -> float:
    fmt = config.DATETIME_FMT
    return (datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds() / 60


PAID_IN = "o.status='PAID' AND substr(o.paid_at,1,10) BETWEEN ? AND ?"


class StatsService(Service):
    def summary(self, start: str, end: str) -> dict:
        check_range(start, end)
        q = self.db.scalar
        gross = q("SELECT COALESCE(SUM(amount),0) FROM payments "
                  "WHERE substr(paid_at,1,10) BETWEEN ? AND ?", (start, end))
        refund_row = self.db.query_one(
            "SELECT COALESCE(SUM(amount),0) AS amt, COUNT(DISTINCT order_id) AS n, "
            "COUNT(DISTINCT CASE WHEN partial=1 THEN order_id END) AS partial_n "
            "FROM refunds WHERE substr(at,1,10) BETWEEN ? AND ?", (start, end))
        cancel_row = self.db.query_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(subtotal),0) AS amt FROM orders "
            "WHERE status='CANCELLED' AND substr(cancelled_at,1,10) BETWEEN ? AND ?", (start, end))
        row = self.db.query_one(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(o.total - o.refunded_amount),0) AS sales, "
            f"COALESCE(SUM(o.subtotal),0) AS before_dc, "
            f"COALESCE(SUM(o.menu_discount+o.time_discount+o.coupon_discount),0) AS dc, "
            f"COALESCE(SUM(o.points_used),0) AS pts FROM orders o WHERE {PAID_IN}",
            (start, end))
        cost = q(f"SELECT COALESCE(SUM(i.unit_cost*(i.qty-i.refunded_qty)),0) FROM order_items i "
                 f"JOIN orders o ON o.id=i.order_id WHERE {PAID_IN}", (start, end))
        n = row["n"]
        return {
            "start": start, "end": end,
            "gross_sales": gross,                 # 기간 내 결제 총액(이후 환불된 건 포함)
            "refund_amount": refund_row["amt"],   # 기간 내 환불액(전체+부분)
            "refund_count": refund_row["n"],      # 환불이 있었던 주문 수
            "partial_refund_count": refund_row["partial_n"],
            "net_sales": gross - refund_row["amt"],  # 순매출
            "cancel_count": cancel_row["n"],      # 결제 전 취소
            "cancel_amount": cancel_row["amt"],
            "order_count": n,                     # 판매 완료 주문 수(전체 환불 제외)
            "sales": row["sales"],                # 판매 완료 주문의 실결제액(부분 환불 차감)
            "before_discount": row["before_dc"],
            "discount": row["dc"],
            "points_used": row["pts"],
            "avg_ticket": row["sales"] // n if n else 0,
            "cost": cost,
            "profit": row["sales"] - cost,        # 순이익 = 실결제액 - 원가
        }

    def menu_stats(self, start: str, end: str, sort: str = "qty") -> list[dict]:
        """메뉴별 판매수량/주문수/매출/원가/이익(부분 환불 수량 제외). sort: qty | revenue | profit"""
        check_range(start, end)
        rows = self.db.query(
            f"SELECT i.menu_id, MAX(i.name) AS name, SUM(i.qty-i.refunded_qty) AS qty, "
            f"COUNT(DISTINCT i.order_id) AS orders, "
            f"SUM(i.unit_price*(i.qty-i.refunded_qty)) AS gross, "
            f"SUM((i.unit_price*i.discount_rate/100/{config.DISCOUNT_UNIT})*{config.DISCOUNT_UNIT}"
            f"*(i.qty-i.refunded_qty)) AS dc, "
            f"SUM(i.unit_cost*(i.qty-i.refunded_qty)) AS cost "
            f"FROM order_items i JOIN orders o ON o.id=i.order_id "
            f"WHERE {PAID_IN} AND i.qty > i.refunded_qty GROUP BY i.menu_id", (start, end))
        result = []
        for r in rows:
            revenue = r["gross"] - r["dc"]
            result.append({"menu_id": r["menu_id"], "name": r["name"], "qty": r["qty"],
                           "orders": r["orders"], "revenue": revenue, "cost": r["cost"],
                           "profit": revenue - r["cost"]})
        key = {"qty": "qty", "revenue": "revenue", "profit": "profit"}.get(sort, "qty")
        result.sort(key=lambda d: (-d[key], d["name"]))
        for rank, d in enumerate(result, 1):
            d["rank"] = rank
        return result

    def hourly(self, start: str, end: str) -> dict:
        """시간대(주문 접수 시각)별 주문 수와 매출, 피크 시간대."""
        check_range(start, end)
        rows = self.db.query(
            f"SELECT CAST(substr(o.created_at,12,2) AS INTEGER) AS h, COUNT(*) AS n, "
            f"SUM(o.total - o.refunded_amount) AS sales FROM orders o WHERE {PAID_IN} GROUP BY h",
            (start, end))
        hours = {h: {"count": 0, "sales": 0} for h in range(24)}
        for r in rows:
            hours[r["h"]] = {"count": r["n"], "sales": r["sales"]}
        busiest = max(hours, key=lambda h: (hours[h]["count"], hours[h]["sales"]))
        richest = max(hours, key=lambda h: (hours[h]["sales"], hours[h]["count"]))
        has_data = any(v["count"] for v in hours.values())
        return {"hours": hours,
                "peak_count_hour": busiest if has_data else None,
                "peak_sales_hour": richest if has_data else None}

    def by_order_type(self, start: str, end: str) -> list[dict]:
        check_range(start, end)
        rows = self.db.query(
            f"SELECT o.order_type, COUNT(*) AS n, SUM(o.total - o.refunded_amount) AS sales "
            f"FROM orders o "
            f"WHERE {PAID_IN} GROUP BY o.order_type", (start, end))
        data = {r["order_type"]: r for r in rows}
        total = sum(r["sales"] for r in rows) or 1
        return [{"type": t, "label": label, "count": data[t]["n"] if t in data else 0,
                 "sales": data[t]["sales"] if t in data else 0,
                 "ratio": (data[t]["sales"] if t in data else 0) / total * 100}
                for t, label in config.ORDER_TYPES.items()]

    def by_payment_method(self, start: str, end: str) -> list[dict]:
        """결제수단별 건수/순금액(결제 − 환불)/비율. 건수는 결제 건수."""
        check_range(start, end)
        paid = {r["method"]: r for r in self.db.query(
            "SELECT method, COUNT(*) AS n, SUM(amount) AS amt FROM payments "
            "WHERE substr(paid_at,1,10) BETWEEN ? AND ? GROUP BY method", (start, end))}
        refunded = {r["method"]: r["amt"] for r in self.db.query(
            "SELECT method, SUM(amount) AS amt FROM refunds "
            "WHERE substr(at,1,10) BETWEEN ? AND ? GROUP BY method", (start, end))}
        rows = []
        for m, label in config.PAYMENT_METHODS.items():
            amt = (paid[m]["amt"] if m in paid else 0) - refunded.get(m, 0)
            rows.append({"method": m, "label": label, "count": paid[m]["n"] if m in paid else 0,
                         "amount": amt, "refund": refunded.get(m, 0)})
        total = sum(max(0, r["amount"]) for r in rows)
        count_total = sum(r["count"] for r in rows)
        for r in rows:
            r["ratio"] = max(0, r["amount"]) / total * 100 if total else 0
            r["count_ratio"] = r["count"] / count_total * 100 if count_total else 0
        return rows

    def trend(self, start: str, end: str, unit: str = "day") -> list[dict]:
        """일별/주별/월별/연도별 매출 추이."""
        check_range(start, end)
        if unit not in GROUP_UNITS:
            raise PosError("알 수 없는 집계 단위입니다.")
        buckets: dict[str, dict] = {}
        rows = self.db.query(
            f"SELECT substr(o.paid_at,1,10) AS d, COUNT(*) AS n, "
            f"SUM(o.total - o.refunded_amount) AS sales "
            f"FROM orders o WHERE {PAID_IN} GROUP BY d ORDER BY d", (start, end))
        for r in rows:
            d = date.fromisoformat(r["d"])
            if unit == "day":
                key = r["d"]
            elif unit == "week":
                monday = d - timedelta(days=d.weekday())
                key = f"{monday.isoformat()}~"
            elif unit == "month":
                key = r["d"][:7]
            else:
                key = r["d"][:4]
            b = buckets.setdefault(key, {"label": key, "count": 0, "sales": 0})
            b["count"] += r["n"]
            b["sales"] += r["sales"]
        return list(buckets.values())

    def processing_times(self, start: str, end: str) -> dict:
        """평균 주문 처리 시간(접수→전체 조리 완료)과 메뉴별 평균 조리 시간(분)."""
        check_range(start, end)
        orders = self.db.query(
            "SELECT created_at, completed_at FROM orders WHERE completed_at IS NOT NULL "
            "AND status IN ('OPEN','PAID') AND substr(created_at,1,10) BETWEEN ? AND ?",
            (start, end))
        times = [_minutes(o["created_at"], o["completed_at"]) for o in orders]
        items = self.db.query(
            "SELECT i.name, i.ordered_at, i.done_at FROM order_items i "
            "JOIN orders o ON o.id=i.order_id WHERE i.done_at IS NOT NULL "
            "AND o.status IN ('OPEN','PAID') AND substr(i.ordered_at,1,10) BETWEEN ? AND ?",
            (start, end))
        per_menu: dict[str, list[float]] = {}
        for it in items:
            per_menu.setdefault(it["name"], []).append(_minutes(it["ordered_at"], it["done_at"]))
        menu_avg = sorted(
            ({"name": k, "count": len(v), "avg_min": sum(v) / len(v)} for k, v in per_menu.items()),
            key=lambda d: -d["avg_min"])
        return {"order_count": len(times),
                "avg_order_min": sum(times) / len(times) if times else 0.0,
                "max_order_min": max(times) if times else 0.0,
                "menus": menu_avg}

    def compare(self, start: str, end: str) -> dict:
        """기간 비교: 선택 기간과 직전 비교 기간의 주요 지표·메뉴별 판매 증감."""
        check_range(start, end)
        ps, pe = previous_range(start, end)
        cur, prev = self.summary(start, end), self.summary(ps, pe)
        metrics = []
        for key, label in COMPARE_METRICS:
            a, b = cur[key], prev[key]
            metrics.append({"key": key, "label": label, "current": a, "previous": b,
                            "diff": a - b, "rate": (a - b) / b * 100 if b else None})
        cm = {m["name"]: m for m in self.menu_stats(start, end)}
        pm = {m["name"]: m for m in self.menu_stats(ps, pe)}
        menus = []
        for name in set(cm) | set(pm):
            a = cm.get(name, {"qty": 0, "revenue": 0})
            b = pm.get(name, {"qty": 0, "revenue": 0})
            menus.append({"name": name, "qty": a["qty"], "prev_qty": b["qty"],
                          "qty_diff": a["qty"] - b["qty"], "revenue": a["revenue"],
                          "prev_revenue": b["revenue"], "revenue_diff": a["revenue"] - b["revenue"]})
        menus.sort(key=lambda d: (-d["revenue_diff"], d["name"]))
        return {"start": start, "end": end, "prev_start": ps, "prev_end": pe,
                "metrics": metrics, "menus": menus}

    def daily_report(self, business_date: str) -> dict:
        s = self.summary(business_date, business_date)
        s["methods"] = self.by_payment_method(business_date, business_date)
        s["types"] = self.by_order_type(business_date, business_date)
        s["top_menus"] = self.menu_stats(business_date, business_date, "qty")[:5]
        s["all_orders"] = self.db.scalar(
            "SELECT COUNT(*) FROM orders WHERE substr(created_at,1,10)=?", (business_date,))
        return s
