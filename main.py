"""금오분식 POS 실행 진입점.

    python main.py              # GUI 실행 (data/pos.db 사용)
    python main.py --demo 60    # 최근 60일 샘플 데이터 생성 후 실행
    python main.py --db my.db   # 다른 DB 파일 사용

PyInstaller로 만든 exe에서도 같은 옵션을 쓸 수 있다. (예: KumohPOS.exe --demo 60)
"""
import argparse
import os
import sys
import traceback

FROZEN = getattr(sys, "frozen", False)
# exe로 실행하면 데이터(data/, receipts/, reports/ ...)를 exe 파일 옆에 저장한다.
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
# 아이콘 등 번들 리소스 위치 (onefile exe는 임시 폴더에 풀린다)
RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RESOURCE_DIR)


def log_error(text: str) -> str:
    path = os.path.join(BASE_DIR, "data", "error.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
    return path


def say(msg: str) -> None:
    """콘솔 출력. 콘솔이 없거나(windowed exe) 한글을 못 쓰는 인코딩이어도 실패하지 않는다."""
    try:
        if sys.stdout:
            print(msg)
    except (OSError, UnicodeError, ValueError):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="금오분식 POS 시스템")
    parser.add_argument("--db", default=os.path.join(BASE_DIR, "data", "pos.db"),
                        help="SQLite DB 파일 경로")
    parser.add_argument("--demo", type=int, metavar="DAYS",
                        help="최근 DAYS일치 샘플 판매 데이터를 생성")
    args = parser.parse_args()

    from pos.system import PosSystem
    pos = PosSystem(args.db, out_dir=BASE_DIR)
    if args.demo:
        from pos.demo import generate
        say(f"샘플 데이터 생성 중... ({args.demo}일)")
        say(f"완료: 주문 {generate(pos, args.demo)}건")

    try:
        from pos.gui.app import run
    except ImportError as e:  # tkinter 미설치 환경
        say(f"GUI를 실행할 수 없습니다: {e}\n"
            "Linux에서는 'sudo apt install python3-tk'로 tkinter를 설치하세요.")
        sys.exit(1)
    icon = os.path.join(RESOURCE_DIR, "assets", "kumoh_pos.ico")
    run(pos, icon=icon if os.path.exists(icon) else None, error_log=log_error)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        path = log_error(traceback.format_exc())
        if FROZEN:
            # 콘솔이 없으므로 메시지 창으로 알린다.
            try:
                import tkinter
                from tkinter import messagebox
                root = tkinter.Tk()
                root.withdraw()
                messagebox.showerror("금오분식 POS 오류",
                                     f"프로그램 실행 중 오류가 발생했습니다.\n자세한 내용: {path}")
            except Exception:
                pass
        raise
