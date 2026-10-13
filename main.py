"""금오분식 POS 실행 진입점.

    python main.py              # GUI 실행 (data/pos.db 사용)
    python main.py --demo 60    # 최근 60일 샘플 데이터 생성 후 실행
    python main.py --db my.db   # 다른 DB 파일 사용
"""
import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


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
        print(f"샘플 데이터 생성 중... ({args.demo}일)")
        print(f"완료: 주문 {generate(pos, args.demo)}건")

    try:
        from pos.gui.app import run
    except ImportError as e:  # tkinter 미설치 환경
        print(f"GUI를 실행할 수 없습니다: {e}\n"
              "Linux에서는 'sudo apt install python3-tk'로 tkinter를 설치하세요.")
        sys.exit(1)
    run(pos)


if __name__ == "__main__":
    main()
