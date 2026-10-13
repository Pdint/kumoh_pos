"""테이블 관리."""
from __future__ import annotations

from .. import config
from . import PosError, Service


class TableService(Service):
    def tables(self, include_inactive: bool = False):
        sql = "SELECT * FROM tables_"
        if not include_inactive:
            sql += " WHERE active=1"
        return self.db.query(sql + " ORDER BY id")

    def get(self, table_id: int):
        row = self.db.query_one("SELECT * FROM tables_ WHERE id=?", (table_id,))
        if not row:
            raise PosError("존재하지 않는 테이블입니다.")
        return row

    def add_table(self, name: str, seats: int = 4) -> int:
        name = name.strip()
        if not name:
            raise PosError("테이블 이름을 입력하세요.")
        if not 1 <= seats <= 20:
            raise PosError("좌석 수는 1~20 사이여야 합니다.")
        if self.db.scalar("SELECT COUNT(*) FROM tables_ WHERE active=1") >= config.MAX_TABLES:
            raise PosError(f"테이블은 최대 {config.MAX_TABLES}개까지 등록할 수 있습니다.")
        row = self.db.query_one("SELECT * FROM tables_ WHERE name=?", (name,))
        if row and row["active"]:
            raise PosError("이미 존재하는 테이블 이름입니다.")
        if row:  # 삭제했던 테이블 이름 재사용
            self.db.execute("UPDATE tables_ SET active=1, seats=? WHERE id=?", (seats, row["id"]))
            table_id = row["id"]
        else:
            table_id = self.db.execute(
                "INSERT INTO tables_(name, seats) VALUES(?,?)", (name, seats)
            ).lastrowid
        self.db.log("테이블 추가", name)
        self.db.commit()
        return table_id

    def update_table(self, table_id: int, name: str, seats: int) -> None:
        if not name.strip():
            raise PosError("테이블 이름을 입력하세요.")
        if not 1 <= seats <= 20:
            raise PosError("좌석 수는 1~20 사이여야 합니다.")
        if self.db.query_one("SELECT 1 FROM tables_ WHERE name=? AND id<>?", (name.strip(), table_id)):
            raise PosError("이미 존재하는 테이블 이름입니다.")
        self.db.execute("UPDATE tables_ SET name=?, seats=? WHERE id=?", (name.strip(), seats, table_id))
        self.db.commit()

    def delete_table(self, table_id: int) -> None:
        if self.db.query_one("SELECT 1 FROM orders WHERE table_id=? AND status='OPEN'", (table_id,)):
            raise PosError("주문이 진행 중인 테이블은 삭제할 수 없습니다.")
        self.db.execute("UPDATE tables_ SET active=0 WHERE id=?", (table_id,))
        self.db.log("테이블 삭제", str(table_id))
        self.db.commit()
