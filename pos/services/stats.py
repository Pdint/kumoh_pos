"""매출 통계.

- 매출(결제)은 결제 시각(paid_at), 환불은 환불 시각(cancelled_at) 기준으로 집계한다.
- 메뉴별/시간대별/유형별 통계와 이익 계산은 환불되지 않은(PAID) 주문만 대상으로 한다.
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
        refund_amount = q("SELECT COALESCE(SUM(amount),0) FROM payments "
                          "WHERE substr(refunded_at,1,10) BETWEEN ? AND ?", (start, end))
        refund_count = q("SELECT COUNT(*) FROM orders WHERE status='REFUNDED' "
                         "AND substr(cancelled_at,1,10) BETWEEN ? AND ?", (start, end))
        cancel_row = self.db.query_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(subtotal),0) AS amt FROM orders "
            "WHERE status='CANCELLED' AND substr(cancelled_at,1,10) BETWEEN ? AND ?", (start, end))
        row = self.db.query_one(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(o.total),0) AS sales, "
            f"COALESCE(SUM(o.subtotal),0) AS before_dc, "
            f"COALESCE(SUM(o.menu_discount+o.time_discount+o.coupon_discount),0) AS dc, "
            f"COALESCE(SUM(o.points_used),0) AS pts FROM orders o WHERE {PAID_IN}",
            (start, end))
        cost = q(f"SELECT COALESCE(SUM(i.unit_cost*i.qty),0) FROM order_items i "
                 f"JOIN orders o ON o.id=i.order_id WHERE {PAID_IN}", (start, end))
        n = row["n"]
        return {
            "start": start, "end": end,
            "gross_sales": gross,                 # 기간 내 결제 총액(이후 환불된 건 포함)
            "refund_amount": refund_amount,
            "refund_count": refund_count,
            "net_sales": gross - refund_amount,   # 순매출
            "cancel_count": cancel_row["n"],      # 결제 전 취소
            "cancel_amount": cancel_row["amt"],
            "order_count": n,                     # 정상 판매 완료 주문 수
            "sales": row["sales"],                # 정상 판매 주문의 실결제액
            "before_discount": row["before_dc"],
            "discount": row["dc"],
            "points_used": row["pts"],
            "avg_ticket": row["sales"] // n if n else 0,
            "cost": cost,
            "profit": row["sales"] - cost,        # 순이익 = 실결제액 - 원가
        }

    def menu_stats(self, start: str, end: str, sort: str = "qty") -> list[dict]:
        """메뉴별 판매수량/주문수/매출/원가/이익. sort: qty | revenue | profit"""
        check_range(start, end)
        rows = self.db.query(
            f"SELECT i.menu_id, i.name, SUM(i.qty) AS qty, COUNT(DISTINCT i.order_id) AS orders, "
            f"SUM(i.unit_price*i.qty) AS gross, "
            f"SUM((i.unit_price*i.discount_rate/100/{config.DISCOUNT_UNIT})*{config.DISCOUNT_UNIT}*i.qty) AS dc, "
            f"SUM(i.unit_cost*i.qty) AS cost "
            f"FROM order_items i JOIN orders o ON o.id=i.order_id WHERE {PAID_IN} "
            f"GROUP BY i.menu_id", (start, end))
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
            f"SUM(o.total) AS sales FROM orders o WHERE {PAID_IN} GROUP BY h", (start, end))
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
            f"SELECT o.order_type, COUNT(*) AS n, SUM(o.total) AS sales FROM orders o "
            f"WHERE {PAID_IN} GROUP BY o.order_type", (start, end))
        data = {r["order_type"]: r for r in rows}
        total = sum(r["sales"] for r in rows) or 1
        return [{"type": t, "label": label, "count": data[t]["n"] if t in data else 0,
                 "sales": data[t]["sales"] if t in data else 0,
                 "ratio": (data[t]["sales"] if t in data else 0) / total * 100}
                for t, label in config.ORDER_TYPES.items()]

    def by_payment_method(self, start: str, end: str) -> list[dict]:
        """결제수단별 건수/금액/비율 (환불된 결제 제외)."""
        check_range(start, end)
        rows = self.db.query(
            "SELECT method, COUNT(*) AS n, SUM(amount) AS amt FROM payments "
            "WHERE refunded_at IS NULL AND substr(paid_at,1,10) BETWEEN ? AND ? GROUP BY method",
            (start, end))
        data = {r["method"]: r for r in rows}
        total = sum(r["amt"] for r in rows)
        count_total = sum(r["n"] for r in rows)
        result = []
        for m, label in config.PAYMENT_METHODS.items():
            amt = data[m]["amt"] if m in data else 0
            n = data[m]["n"] if m in data else 0
            result.append({"method": m, "label": label, "count": n, "amount": amt,
                           "ratio": amt / total * 100 if total else 0,
                           "count_ratio": n / count_total * 100 if count_total else 0})
        return result

    def trend(self, start: str, end: str, unit: str = "day") -> list[dict]:
        """일별/주별/월별/연도별 매출 추이."""
        check_range(start, end)
        if unit not in GROUP_UNITS:
            raise PosError("알 수 없는 집계 단위입니다.")
        buckets: dict[str, dict] = {}
        rows = self.db.query(
            f"SELECT substr(o.paid_at,1,10) AS d, COUNT(*) AS n, SUM(o.total) AS sales "
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

    def daily_report(self, business_date: str) -> dict:
        s = self.summary(business_date, business_date)
        s["methods"] = self.by_payment_method(business_date, business_date)
        s["types"] = self.by_order_type(business_date, business_date)
        s["top_menus"] = self.menu_stats(business_date, business_date, "qty")[:5]
        s["all_orders"] = self.db.scalar(
            "SELECT COUNT(*) FROM orders WHERE substr(created_at,1,10)=?", (business_date,))
        return s
