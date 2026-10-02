"""Создание базы данных и загрузка начального каталога."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import DATABASE_PATH
from bot.database.db import Database


async def main() -> None:
    db = Database(DATABASE_PATH)
    await db.init_db()
    products = await db.get_products()
    print(f"База создана: {DATABASE_PATH}")
    print(f"Товаров в каталоге: {len(products)}")
    for p in products:
        print(f"  - {p['name']}: {p['price']:.0f} rub")


if __name__ == "__main__":
    asyncio.run(main())
