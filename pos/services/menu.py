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

    def available_qty(self, menu_id: int) -> int:
        """현재 재고로 만들 수 있는 최대 수량. 레시피가 없으면 무제한(9999)."""
        rows = self.db.query(
            "SELECT r.qty, i.stock FROM recipes r JOIN ingredients i ON i.id=r.ingredient_id "
            "WHERE r.menu_id=?",
            (menu_id,),
        )
        if not rows:
            return 9999
        return max(0, min(math.floor(r["stock"] / r["qty"] + 1e-9) for r in rows))

    def is_soldout(self, menu_id: int) -> bool:
        menu = self.get(menu_id)
        return bool(menu["manual_soldout"]) or self.available_qty(menu_id) <= 0

    def consume(self, menu_id: int, qty: int, reason: str) -> list[str]:
        """주문 수량만큼 재료를 차감(qty<0 이면 복구)하고, 재고 경고 메시지를 반환한다.

        커밋은 호출자가 담당한다.
        """
        if qty > 0 and self.available_qty(menu_id) < qty:
            menu = self.get(menu_id)
            raise PosError(
                f"'{menu['name']}' 재고가 부족합니다. (주문 가능 {self.available_qty(menu_id)}개)"
            )
        now = self.db.now_str()
        for r in self.db.query("SELECT ingredient_id, qty FROM recipes WHERE menu_id=?", (menu_id,)):
            delta = -r["qty"] * qty
            self.db.execute("UPDATE ingredients SET stock=stock+? WHERE id=?",
                            (delta, r["ingredient_id"]))
            self.db.execute(
                "INSERT INTO stock_logs(ingredient_id, delta, reason, at) VALUES(?,?,?,?)",
                (r["ingredient_id"], delta, reason, now),
            )
        return self.low_stock_warnings(menu_id) if qty > 0 else []

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
