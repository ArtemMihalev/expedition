import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

_manager_raw = os.getenv("MANAGER_IDS", "")
MANAGER_IDS: set[int] = {
    int(x.strip()) for x in _manager_raw.split(",") if x.strip().isdigit()
}

DATABASE_PATH = os.getenv("DATABASE_PATH", "data/shop.db")

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
