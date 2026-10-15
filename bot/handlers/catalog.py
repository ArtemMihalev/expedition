from bot.ui import show_text
from aiogram import Router
from aiogram.types import CallbackQuery

from bot.database.db import CATEGORY_LABELS, Database
from bot.keyboards.inline import keyboards
from bot.ui import show_product_card

router = Router()


@router.callback_query(lambda c: c.data == "catalog")
async def show_catalog(callback: CallbackQuery, db: Database) -> None:
    categories = await db.get_categories()
    text = (
        "🛍 <b>Каталог Expedition</b>\n\n"
        "Выберите категорию или просмотрите весь ассортимент:"
    )
    await show_text(callback.message,
        text, reply_markup=keyboards.catalog_categories(categories)
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("cat:"))
async def show_category(callback: CallbackQuery, db: Database) -> None:
    category = callback.data.split(":", 1)[1]
    if category == "all":
        products = await db.get_products()
        title = "Весь каталог"
    else:
        products = await db.get_products(category=category)
        title = CATEGORY_LABELS.get(category, category)

    if not products:
        await callback.answer("В этой категории пока нет товаров.", show_alert=True)
        return

    text = f"📂 <b>{title}</b>\n\nВыберите товар:"
    await show_text(callback.message,
        text, reply_markup=keyboards.product_list(products)
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("product:"))
async def show_product(callback: CallbackQuery, db: Database) -> None:
    product_id = int(callback.data.split(":")[1])
    product = await db.get_product(product_id)
    if not product:
        await callback.answer("Товар не найден.", show_alert=True)
        return

    cat_label = CATEGORY_LABELS.get(product["category"], product["category"])
    text = (
        f"🏷 <b>{product['name']}</b>\n"
        f"📂 Категория: {cat_label}\n"
        f"💰 Цена: <b>{product['price']:.2f} ₽</b>\n\n"
        f"📦 В наличии: {product['stock']} шт.\n\n"
        f"{product['description']}"
    )
    await show_product_card(callback.message, product, text, keyboards.product_card(product_id, product['stock']))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("add:"))
async def add_to_cart(callback: CallbackQuery, db: Database) -> None:
    _, product_id_str, qty_str = callback.data.split(":")
    product_id = int(product_id_str)
    quantity = int(qty_str)

    if quantity <= 0:
        await callback.answer("Количество должно быть больше нуля.", show_alert=True)
        return

    ok = await db.add_to_cart(callback.from_user.id, product_id, quantity)
    if not ok:
        await callback.answer("Товар недоступен или выбранное количество превышает остаток на складе.", show_alert=True)
        return

    product = await db.get_product(product_id)
    await callback.answer(f"✅ {product['name']} добавлен в корзину ({quantity} шт.)")
