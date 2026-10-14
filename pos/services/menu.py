"""메뉴 / 분류 / 재료(재고) / 레시피 관리."""
from __future__ import annotations

import math

from .. import config
from . import PosError, Service


class MenuService(Service):
    # ------------------------------------------------------------ 분류
    def categories(self):
        return self.db.query("SELECT * FROM categories ORDER BY sort, id")

    def add_category(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise PosError("분류 이름을 입력하세요.")
        if self.db.query_one("SELECT 1 FROM categories WHERE name=?", (name,)):
            raise PosError("이미 존재하는 분류입니다.")
        sort = (self.db.scalar("SELECT MAX(sort) FROM categories") or 0) + 1
        cur = self.db.execute("INSERT INTO categories(name, sort) VALUES(?,?)", (name, sort))
        self.db.log("분류 추가", name)
        self.db.commit()
        return cur.lastrowid

    def delete_category(self, category_id: int) -> None:
        if self.db.scalar(
            "SELECT COUNT(*) FROM menus WHERE category_id=? AND active=1", (category_id,)
        ):
            raise PosError("메뉴가 남아있는 분류는 삭제할 수 없습니다.")
        if self.db.scalar("SELECT COUNT(*) FROM menus WHERE category_id=?", (category_id,)):
            raise PosError("삭제된 메뉴의 판매 이력이 남아있어 분류를 삭제할 수 없습니다.")
        self.db.execute("DELETE FROM categories WHERE id=?", (category_id,))
        self.db.log("분류 삭제", str(category_id))
        self.db.commit()

    # ------------------------------------------------------------ 메뉴
    def menus(self, category_id: int | None = None, include_inactive: bool = False):
        sql = (
            "SELECT m.*, c.name AS category FROM menus m "
            "JOIN categories c ON c.id = m.category_id WHERE 1=1"
        )
        params: list = []
        if not include_inactive:
            sql += " AND m.active=1"
        if category_id is not None:
            sql += " AND m.category_id=?"
            params.append(category_id)
        return self.db.query(sql + " ORDER BY c.sort, m.id", params)

    def get(self, menu_id: int):
        row = self.db.query_one("SELECT * FROM menus WHERE id=?", (menu_id,))
        if not row:
            raise PosError("존재하지 않는 메뉴입니다.")
        return row

    def _validate(self, name: str, price: int, cost: int, discount_rate: int) -> None:
        if not name.strip():
            raise PosError("메뉴 이름을 입력하세요.")
        if price <= 0 or price % 100:
            raise PosError("가격은 100원 단위의 양수여야 합니다.")
        if cost < 0 or cost > price:
            raise PosError("원가는 0 이상, 판매가 이하여야 합니다.")
        if not 0 <= discount_rate <= config.MAX_MENU_DISCOUNT_RATE:
            raise PosError(f"메뉴 할인율은 0~{config.MAX_MENU_DISCOUNT_RATE}% 사이여야 합니다.")

    def add_menu(self, name: str, category_id: int, price: int, cost: int = 0,
                 discount_rate: int = 0, recipe: dict[int, float] | None = None) -> int:
        self._validate(name, price, cost, discount_rate)
        if self.db.query_one("SELECT 1 FROM menus WHERE name=?", (name.strip(),)):
            raise PosError("이미 존재하는 메뉴 이름입니다.")
        cur = self.db.execute(
            "INSERT INTO menus(name, category_id, price, cost, discount_rate) VALUES(?,?,?,?,?)",
            (name.strip(), category_id, price, cost, discount_rate),
        )
        if recipe:
            self._save_recipe(cur.lastrowid, recipe)
        self.db.log("메뉴 추가", f"{name} {price}원")
        self.db.commit()
        return cur.lastrowid

    def update_menu(self, menu_id: int, name: str, category_id: int, price: int,
                    cost: int, discount_rate: int, recipe: dict[int, float] | None = None) -> None:
        self._validate(name, price, cost, discount_rate)
        dup = self.db.query_one(
            "SELECT 1 FROM menus WHERE name=? AND id<>?", (name.strip(), menu_id)
        )
        if dup:
            raise PosError("이미 존재하는 메뉴 이름입니다.")
        self.db.execute(
            "UPDATE menus SET name=?, category_id=?, price=?, cost=?, discount_rate=? WHERE id=?",
            (name.strip(), category_id, price, cost, discount_rate, menu_id),
        )
        if recipe is not None:
            self._save_recipe(menu_id, recipe)
        self.db.log("메뉴 수정", f"#{menu_id} {name} {price}원")
        self.db.commit()

    def delete_menu(self, menu_id: int) -> None:
        """판매 이력 보존을 위해 실제 삭제 대신 비활성화한다."""
        self.db.execute("UPDATE menus SET active=0 WHERE id=?", (menu_id,))
        self.db.log("메뉴 삭제", str(menu_id))
        self.db.commit()

    def set_manual_soldout(self, menu_id: int, soldout: bool) -> None:
        self.db.execute("UPDATE menus SET manual_soldout=? WHERE id=?", (int(soldout), menu_id))
        self.db.log("품절 설정" if soldout else "품절 해제", str(menu_id))
        self.db.commit()

    # ------------------------------------------------------------ 레시피 / 재고
    def recipe(self, menu_id: int):
        return self.db.query(
            "SELECT r.ingredient_id, r.qty, i.name, i.unit FROM recipes r "
            "JOIN ingredients i ON i.id=r.ingredient_id WHERE r.menu_id=? ORDER BY i.name",
            (menu_id,),
        )

    def _save_recipe(self, menu_id: int, recipe: dict[int, float]) -> None:
        self.db.execute("DELETE FROM recipes WHERE menu_id=?", (menu_id,))
        for ing_id, qty in recipe.items():
            if qty <= 0:
                raise PosError("레시피 사용량은 0보다 커야 합니다.")
            self.db.execute(
                "INSERT INTO recipes(menu_id, ingredient_id, qty) VALUES(?,?,?)",
                (menu_id, ing_id, qty),
            )

    # ------------------------------------------------------------ 옵션
    def options(self, menu_id: int, include_inactive: bool = False):
        sql = ("SELECT o.*, i.name AS ingredient_name, i.unit AS ingredient_unit "
               "FROM menu_options o LEFT JOIN ingredients i ON i.id=o.ingredient_id "
               "WHERE o.menu_id=?")
        if not include_inactive:
            sql += " AND o.active=1"
        return self.db.query(sql + " ORDER BY o.sort, o.id", (menu_id,))

    def add_option(self, menu_id: int, name: str, price: int = 0, group_name: str = "",
                   ingredient_id: int | None = None, ingredient_qty: float = 0) -> int:
        name, group_name = name.strip(), group_name.strip()
        if not name:
            raise PosError("옵션 이름을 입력하세요.")
        if price < 0 or price > config.MAX_OPTION_PRICE or price % 100:
            raise PosError(f"옵션 추가금은 0~{config.MAX_OPTION_PRICE:,}원, 100원 단위입니다.")
        if ingredient_id is not None and ingredient_qty <= 0:
            raise PosError("옵션 재료 사용량은 0보다 커야 합니다.")
        if self.db.query_one("SELECT 1 FROM menu_options WHERE menu_id=? AND name=? AND active=1",
                             (menu_id, name)):
            raise PosError("이미 있는 옵션 이름입니다.")
        sort = (self.db.scalar("SELECT MAX(sort) FROM menu_options WHERE menu_id=?",
                               (menu_id,)) or 0) + 1
        cur = self.db.execute(
            "INSERT INTO menu_options(menu_id, group_name, name, price, ingredient_id, "
            "ingredient_qty, sort) VALUES(?,?,?,?,?,?,?)",
            (menu_id, group_name, name, price, ingredient_id,
             ingredient_qty if ingredient_id else 0, sort))
        self.db.log("옵션 추가", f"메뉴 {menu_id} {name} +{price}")
        self.db.commit()
        return cur.lastrowid

    def delete_option(self, option_id: int) -> None:
        """판매 이력 보존을 위해 비활성화한다."""
        self.db.execute("UPDATE menu_options SET active=0 WHERE id=?", (option_id,))
        self.db.log("옵션 삭제", str(option_id))
        self.db.commit()

    def resolve_options(self, menu_id: int, option_ids) -> tuple[list, int, str]:
        """선택한 옵션을 검증하고 (옵션 행 목록, 추가금 합계, 표시 문자열)을 반환한다.

        같은 그룹(예: 맵기)에서는 하나만 고를 수 있다.
        """
        option_ids = sorted({int(i) for i in option_ids})
        if not option_ids:
            return [], 0, ""
        valid = {o["id"]: o for o in self.options(menu_id)}
        rows = []
        groups = set()
        for oid in option_ids:
            if oid not in valid:
                raise PosError("선택할 수 없는 옵션입니다.")
            o = valid[oid]
            if o["group_name"]:
                if o["group_name"] in groups:
                    raise PosError(f"'{o['group_name']}'은(는) 하나만 선택할 수 있습니다.")
                groups.add(o["group_name"])
            rows.append(o)
        rows.sort(key=lambda o: (o["sort"], o["id"]))
        label = ", ".join(o["name"] for o in rows)
        return rows, sum(o["price"] for o in rows), label

    # ------------------------------------------------------------ 재고 계산
    def _requirements(self, menu_id: int, option_ids=()) -> dict[int, float]:
        """메뉴 1개(+옵션)를 만드는 데 필요한 재료량 {재료 id: 수량}."""
        need: dict[int, float] = {}
        for r in self.db.query("SELECT ingredient_id, qty FROM recipes WHERE menu_id=?", (menu_id,)):
            need[r["ingredient_id"]] = need.get(r["ingredient_id"], 0) + r["qty"]
        ids = [int(i) for i in option_ids]
        if ids:
            marks = ",".join("?" * len(ids))
            for o in self.db.query(
                    f"SELECT ingredient_id, ingredient_qty FROM menu_options WHERE id IN ({marks}) "
                    "AND ingredient_id IS NOT NULL", ids):
                need[o["ingredient_id"]] = need.get(o["ingredient_id"], 0) + o["ingredient_qty"]
        return need

    def available_qty(self, menu_id: int, option_ids=()) -> int:
        """현재 재고로 만들 수 있는 최대 수량. 레시피가 없으면 무제한(9999)."""
        need = self._requirements(menu_id, option_ids)
        if not need:
            return 9999
        stock = {r["id"]: r["stock"] for r in self.db.query("SELECT id, stock FROM ingredients")}
        return max(0, min(math.floor(stock.get(i, 0) / q + 1e-9) for i, q in need.items()))

    def is_soldout(self, menu_id: int) -> bool:
        menu = self.get(menu_id)
        return bool(menu["manual_soldout"]) or self.available_qty(menu_id) <= 0

    def consume(self, menu_id: int, qty: int, reason: str, option_ids=()) -> list[str]:
        """주문 수량만큼 재료(옵션 재료 포함)를 차감(qty<0 이면 복구)하고 재고 경고를 반환한다.

        커밋은 호출자가 담당한다.
        """
        if qty > 0:
            avail = self.available_qty(menu_id, option_ids)
            if avail < qty:
                menu = self.get(menu_id)
                raise PosError(f"'{menu['name']}' 재고가 부족합니다. (주문 가능 {avail}개)")
        now = self.db.now_str()
        need = self._requirements(menu_id, option_ids)
        for ing_id, per in need.items():
            delta = -per * qty
            self.db.execute("UPDATE ingredients SET stock=stock+? WHERE id=?", (delta, ing_id))
            self.db.execute(
                "INSERT INTO stock_logs(ingredient_id, delta, reason, at) VALUES(?,?,?,?)",
                (ing_id, delta, reason, now),
            )
        if qty <= 0:
            return []
        return [w for w in self.low_stock_warnings() if any(
            w.startswith(f"[재고 경고] {name} ") for name in self._names(need))]

    def _names(self, need: dict[int, float]) -> list[str]:
        if not need:
            return []
        marks = ",".join("?" * len(need))
        return [r["name"] for r in self.db.query(
            f"SELECT name FROM ingredients WHERE id IN ({marks})", list(need))]

    def low_stock_warnings(self, menu_id: int | None = None) -> list[str]:
        sql = "SELECT i.* FROM ingredients i WHERE i.stock <= i.threshold"
        params: tuple = ()
        if menu_id is not None:
            sql += " AND i.id IN (SELECT ingredient_id FROM recipes WHERE menu_id=?)"
            params = (menu_id,)
        return [
            f"[재고 경고] {r['name']} 잔량 {r['stock']:g}{r['unit']} (기준 {r['threshold']:g}{r['unit']})"
            for r in self.db.query(sql + " ORDER BY i.name", params)
        ]

    # ------------------------------------------------------------ 재료
    def ingredients(self):
        return self.db.query("SELECT * FROM ingredients ORDER BY name")

    def add_ingredient(self, name: str, unit: str, stock: float,
                       threshold: float = config.DEFAULT_STOCK_THRESHOLD) -> int:
        name = name.strip()
        if not name:
            raise PosError("재료 이름을 입력하세요.")
        if stock < 0 or threshold < 0:
            raise PosError("재고와 경고 기준은 0 이상이어야 합니다.")
        if self.db.query_one("SELECT 1 FROM ingredients WHERE name=?", (name,)):
            raise PosError("이미 존재하는 재료입니다.")
        cur = self.db.execute(
            "INSERT INTO ingredients(name, unit, stock, threshold) VALUES(?,?,?,?)",
            (name, unit.strip() or "개", stock, threshold),
        )
        self.db.log("재료 추가", f"{name} {stock:g}{unit}")
        self.db.commit()
        return cur.lastrowid

    def update_ingredient(self, ingredient_id: int, name: str, unit: str, threshold: float) -> None:
        if threshold < 0:
            raise PosError("경고 기준은 0 이상이어야 합니다.")
        self.db.execute(
            "UPDATE ingredients SET name=?, unit=?, threshold=? WHERE id=?",
            (name.strip(), unit.strip() or "개", threshold, ingredient_id),
        )
        self.db.commit()

    def adjust_stock(self, ingredient_id: int, delta: float, reason: str = "입고") -> None:
        """입고(+) / 폐기·실사 조정(-). 재고가 음수가 될 수 없다."""
        row = self.db.query_one("SELECT * FROM ingredients WHERE id=?", (ingredient_id,))
        if not row:
            raise PosError("존재하지 않는 재료입니다.")
        if row["stock"] + delta < 0:
            raise PosError("재고는 0 미만이 될 수 없습니다.")
        self.db.execute("UPDATE ingredients SET stock=stock+? WHERE id=?", (delta, ingredient_id))
        self.db.execute(
            "INSERT INTO stock_logs(ingredient_id, delta, reason, at) VALUES(?,?,?,?)",
            (ingredient_id, delta, reason, self.db.now_str()),
        )
        self.db.log("재고 조정", f"{row['name']} {delta:+g} ({reason})")
        self.db.commit()

    def delete_ingredient(self, ingredient_id: int) -> None:
        if self.db.scalar(
            "SELECT COUNT(*) FROM recipes r JOIN menus m ON m.id=r.menu_id "
            "WHERE r.ingredient_id=? AND m.active=1",
            (ingredient_id,),
        ):
            raise PosError("판매 중인 메뉴의 레시피에 사용되는 재료는 삭제할 수 없습니다.")
        self.db.execute("DELETE FROM ingredients WHERE id=?", (ingredient_id,))
        self.db.commit()
