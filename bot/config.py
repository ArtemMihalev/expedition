import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / '.env')

BOT_TOKEN = os.getenv("BOT_TOKEN")

_manager_raw = os.getenv("MANAGER_IDS", "")
MANAGER_IDS: set[int] = {
    int(x.strip()) for x in _manager_raw.split(",") if x.strip().isdigit()
}

DATABASE_PATH = os.getenv("DATABASE_PATH", "data/shop.db")
if not Path(DATABASE_PATH).is_absolute():
    DATABASE_PATH = str(PROJECT_ROOT / DATABASE_PATH)

# Статусы заказа
ORDER_STATUS_NEW = "new"
ORDER_STATUS_READY = "ready"
ORDER_STATUS_COMPLETED = "completed"
ORDER_STATUS_CANCELLED = "cancelled"

ORDER_STATUS_LABELS = {
    ORDER_STATUS_NEW: "Новый",
    ORDER_STATUS_READY: "Готов к выдаче",
    ORDER_STATUS_COMPLETED: "Выдан",
    ORDER_STATUS_CANCELLED: "Отменён",
}

PICKUP_OPTIONS = {
    "store": "Самовывоз: ул. Путешественников, 1",
    "pickup_point": "Пункт выдачи: ТЦ «Глобус»",
}

PAYMENT_METHOD = "cash_on_pickup"
PAYMENT_LABEL = "Оплата при получении"

# Только тестовый провайдер Telegram Payments.
YOOKASSA_PROVIDER_TOKEN = os.getenv("YOOKASSA_PROVIDER_TOKEN", "").strip()
if YOOKASSA_PROVIDER_TOKEN and ":TEST:" not in YOOKASSA_PROVIDER_TOKEN:
    raise ValueError("Для учебного проекта требуется тестовый токен ЮKassa (:TEST:).")
PAYMENT_LABELS = {
    PAYMENT_METHOD: PAYMENT_LABEL,
    "yookassa_test": "ЮKassa — тестовая оплата",
    "demo": "Демонстрация без платёжного провайдера",
}
PAYMENT_STATUS_LABELS = {
    "unpaid": "При получении",
    "pending": "Ожидает тестовой оплаты",
    "paid": "Оплачено (тестовый платёж)",
    "refunded": "Полный возврат подтверждён",
    "canceled": "Учебный платёж отменён",
}

MINI_APP_URL = os.getenv("MINI_APP_URL", "").strip().rstrip("/")
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
INIT_DATA_TTL = int(os.getenv("INIT_DATA_TTL", "3600"))
DEMO_PAYMENTS_ENABLED = os.getenv("DEMO_PAYMENTS_ENABLED", "true").lower() == "true"
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "").strip()
if YOOKASSA_SECRET_KEY and not YOOKASSA_SECRET_KEY.startswith("test_"):
    raise ValueError("Допускается только секретный ключ тестового магазина ЮKassa (test_).")

RESERVE_SECONDS = int(os.getenv('RESERVE_SECONDS', '120'))
if not 1 <= RESERVE_SECONDS <= 86400:
    raise ValueError('RESERVE_SECONDS должен быть от 1 до 86400.')
PAYMENT_STATUS_LABELS['refund_pending'] = 'Резерв закончился, оформляется возврат'
