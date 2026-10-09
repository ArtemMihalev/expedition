import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import BOT_TOKEN, DATABASE_PATH

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан. Создайте файл .env на основе .env.example")
from bot.database.db import Database
from bot.handlers import setup_routers
from bot.middlewares import DatabaseMiddleware
from bot.ui import PROJECT_ROOT


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    db = Database(DATABASE_PATH)
    await db.init_db()
    logging.info("База данных инициализирована: %s", DATABASE_PATH)
    logging.info("Expedition: карточки с фото включены; проект=%s; PID=%s", PROJECT_ROOT, os.getpid())
    products = await db.get_stock_products()
    image_count = sum(bool(p.get("image_path") and (PROJECT_ROOT / p["image_path"]).is_file()) for p in products)
    logging.info("Найдено изображений товаров: %s из %s", image_count, len(products))

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(DatabaseMiddleware(db))
    dp.include_router(setup_routers())

    logging.info("Бот Expedition запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
