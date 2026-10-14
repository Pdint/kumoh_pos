"""영수증·주방 주문서 출력.

- Windows: 텍스트 파일을 만든 뒤 운영체제의 '인쇄' 동작(os.startfile(path, "print"))으로
  기본 프린터에 보낸다. 별도 라이브러리가 필요 없다.
- macOS/Linux: lp 또는 lpr 명령이 있으면 사용한다.
- 프린터 사용을 끄거나 인쇄에 실패하면 파일만 저장하고 경로를 알려준다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Callable

from . import Service


def system_print(path: str) -> None:
    """운영체제 기본 프린터로 파일을 보낸다. 실패하면 OSError."""
    if sys.platform.startswith("win"):
        os.startfile(path, "print")  # type: ignore[attr-defined]
        return
    for cmd in ("lp", "lpr"):
        if shutil.which(cmd):
            subprocess.run([cmd, path], check=True, capture_output=True, timeout=20)
            return
    raise OSError("사용 가능한 인쇄 명령(lp/lpr)이 없습니다.")


class PrinterService(Service):
    def __init__(self, db, out_dir: str = ".", backend: Callable[[str], None] = system_print):
        super().__init__(db)
        self.out_dir = out_dir
        self.backend = backend

    @property
    def enabled(self) -> bool:
        return self.db.get_setting("printer_enabled", "1") == "1"

    def set_enabled(self, enabled: bool) -> None:
        self.db.set_setting("printer_enabled", "1" if enabled else "0")
        self.db.log("프린터 설정", "사용" if enabled else "사용 안 함")
        self.db.commit()

    def print_text(self, text: str, name: str) -> dict:
        """text를 prints/{name}.txt 로 저장하고 (설정 시) 인쇄한다.

        반환: {"printed": bool, "path": 저장 경로, "message": 안내 문구}
        """
        folder = os.path.join(self.out_dir, "prints")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{name}.txt")
        # 메모장 등에서 한글이 깨지지 않도록 BOM 포함 UTF-8로 저장
        with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(text + "\n")
        if not self.enabled:
            return {"printed": False, "path": path,
                    "message": f"프린터 사용이 꺼져 있어 파일로 저장했습니다.\n{path}"}
        try:
            self.backend(path)
        except (OSError, subprocess.SubprocessError) as e:
            self.db.log("인쇄 실패", f"{name}: {e}")
            self.db.commit()
            reason = getattr(e, "strerror", None) or str(e)
            return {"printed": False, "path": path,
                    "message": f"인쇄하지 못해 파일로 저장했습니다.\n원인: {reason}\n저장 위치: {path}"}
        self.db.log("인쇄", name)
        self.db.commit()
        return {"printed": True, "path": path, "message": "프린터로 보냈습니다."}
