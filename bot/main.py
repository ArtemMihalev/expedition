import asyncio
import logging
import os
from contextlib import suppress
from aiohttp import web
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import BOT_TOKEN, DATABASE_PATH, WEB_HOST, WEB_PORT, RESERVE_SECONDS

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан. Создайте файл .env на основе .env.example")
from bot.database.db import Database
from bot.handlers import setup_routers
from bot.middlewares import DatabaseMiddleware
from bot.ui import PROJECT_ROOT
from bot.web.server import create_app
from bot.handlers.finance import notification_worker


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    db = Database(DATABASE_PATH, reserve_seconds=RESERVE_SECONDS)
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

    runner = web.AppRunner(create_app(db), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, WEB_HOST, WEB_PORT).start()
        logging.info("Mini App: http://%s:%s", WEB_HOST, WEB_PORT)
        notifications = asyncio.create_task(notification_worker(bot, db))
        try:
            logging.info("Бот Expedition запущен")
            await dp.start_polling(bot, close_bot_session=False)
        finally:
            notifications.cancel()
            with suppress(asyncio.CancelledError):
                await notifications
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
