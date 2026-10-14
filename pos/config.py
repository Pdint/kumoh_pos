"""가게 정보 및 시스템 제한사항(정책) 상수.

docs/요구사항명세.md 의 '제한사항' 항목이 이 파일의 값과 1:1로 대응한다.
"""

STORE_NAME = "금오분식"
STORE_SUBTITLE = "Kumoh Snack Bar"
STORE_ADDRESS = "경북 구미시 대학로 61"
STORE_TEL = "054-478-0000"
BUSINESS_NO = "123-45-67890"

DEFAULT_ADMIN_PASSWORD = "0000"

# ---- 주문 ----
MAX_ITEM_QTY = 99            # 한 주문 항목의 최대 수량
MAX_TABLES = 30              # 등록 가능한 최대 테이블 수
MAX_ORDER_ITEMS = 50         # 한 주문의 최대 항목(라인) 수
MAX_MEMO_LENGTH = 40         # 주문 항목 요청사항(메모) 최대 글자 수
MAX_OPTION_PRICE = 10_000    # 메뉴 옵션 추가금 상한(원)

# ---- 결제 ----
PAYMENT_METHODS = {"CASH": "현금", "CARD": "카드", "EASY": "간편결제", "TRANSFER": "계좌이체"}
# 계좌이체 결제 시 손님에게 안내할 입금 계좌
STORE_BANK_ACCOUNT = "농협 351-0000-0000-00 (예금주: 금오분식)"
ORDER_TYPES = {"DINE_IN": "매장", "TAKEOUT": "포장"}
MAX_SPLIT_COUNT = 10         # 분할 결제 최대 횟수

# ---- 할인 ----
DISCOUNT_UNIT = 10           # 할인 금액은 10원 단위 절사
MAX_MENU_DISCOUNT_RATE = 50  # 메뉴별 할인율 상한(%)
MAX_TIME_DISCOUNT_RATE = 30  # 시간대 할인율 상한(%)

# ---- 회원 / 포인트 ----
# (등급명, 누적 결제액 하한, 적립률 %)
MEMBER_GRADES = [
    ("골드", 1_000_000, 3),
    ("실버", 300_000, 2),
    ("일반", 0, 1),
]
MIN_POINT_USE = 1_000        # 포인트는 1,000P 이상부터 사용 가능
POINT_USE_UNIT = 100         # 100P 단위로 사용

# ---- 직원 / 급여 ----
MINIMUM_WAGE = 10_320        # 2026년 최저시급(원)
FULLTIME_MONTHLY_HOURS = 209 # 월 소정근로시간(통상시급 산정 기준)
OVERTIME_RATE = 1.5          # 연장근로 가산율
DAILY_REGULAR_HOURS = 8      # 1일 소정근로시간
WEEKLY_HOLIDAY_MIN_HOURS = 15  # 주휴수당 지급 최소 주 근무시간
MAX_SHIFT_HOURS = 16         # 1회 근무 최대 시간(오입력 방지)

# ---- 재고 ----
DEFAULT_STOCK_THRESHOLD = 10

# ---- 예약 ----
RESERVATION_ALERT_MINUTES = 10   # 예약 시간 10분 전부터 알림
RESERVATION_MIN_LEAD_MINUTES = 30  # 최소 30분 이후 시각만 예약 가능

# ---- 보안 ----
MAX_LOGIN_ATTEMPTS = 3
LOGIN_LOCK_SECONDS = 30

DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
DATE_FMT = "%Y-%m-%d"
