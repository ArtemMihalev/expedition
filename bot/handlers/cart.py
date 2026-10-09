from bot.ui import show_text
from aiogram import Router
from aiogram.types import CallbackQuery

from bot.database.db import Database
from bot.keyboards.inline import keyboards

router = Router()


def format_cart_text(cart: list[dict], total: float) -> str:
    if not cart:
        return (
            "🛒 <b>Корзина пуста</b>\n\n"
            "Добавьте товары из каталога, чтобы оформить заказ."
        )

    lines = ["🛒 <b>Ваша корзина</b>\n"]
    for item in cart:
        subtotal = item["price"] * item["quantity"]
        lines.append(
            f"• {item['name']}\n"
            f"  {item['quantity']} × {item['price']:.0f} ₽ = <b>{subtotal:.0f} ₽</b>"
        )
        if not item['is_available'] or item['quantity'] > item['stock']:
            lines.append(f"  ⚠️ Доступно на складе: {item['stock']} шт. Уменьшите количество или удалите позицию.")
    lines.append(f"\n💰 <b>Итого: {total:.0f} ₽</b>")
    return "\n".join(lines)


@router.callback_query(lambda c: c.data == "cart")
async def show_cart(callback: CallbackQuery, db: Database) -> None:
    user_id = callback.from_user.id
    cart = await db.get_cart(user_id)
    total = await db.get_cart_total(user_id)
    text = format_cart_text(cart, total)
    await show_text(callback.message, text, reply_markup=keyboards.cart(cart))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("cart_inc:"))
async def cart_increase(callback: CallbackQuery, db: Database) -> None:
    product_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    cart = await db.get_cart(user_id)
    item = next((i for i in cart if i["product_id"] == product_id), None)
    if not item:
        await callback.answer("Позиция не найдена.", show_alert=True)
        return

    ok, msg = await db.update_cart_quantity(user_id, product_id, item["quantity"] + 1)
    if not ok:
        await callback.answer(msg, show_alert=True)
        return

    cart = await db.get_cart(user_id)
    total = await db.get_cart_total(user_id)
    await show_text(callback.message,
        format_cart_text(cart, total), reply_markup=keyboards.cart(cart)
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("cart_dec:"))
async def cart_decrease(callback: CallbackQuery, db: Database) -> None:
    product_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    cart = await db.get_cart(user_id)
    item = next((i for i in cart if i["product_id"] == product_id), None)
    if not item:
        await callback.answer("Позиция не найдена.", show_alert=True)
        return

    new_qty = item["quantity"] - 1
    if new_qty <= 0:
        await db.remove_from_cart(user_id, product_id)
    else:
        ok, msg = await db.update_cart_quantity(user_id, product_id, new_qty)
        if not ok:
            await callback.answer(msg, show_alert=True)
            return

    cart = await db.get_cart(user_id)
    total = await db.get_cart_total(user_id)
    await show_text(callback.message,
        format_cart_text(cart, total), reply_markup=keyboards.cart(cart)
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("cart_qty:"))
async def cart_qty_info(callback: CallbackQuery) -> None:
    await callback.answer("Используйте ➕ и ➖ для изменения количества.")


@router.callback_query(lambda c: c.data and c.data.startswith("cart_del:"))
async def cart_delete(callback: CallbackQuery, db: Database) -> None:
    product_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    await db.remove_from_cart(user_id, product_id)
    cart = await db.get_cart(user_id)
    total = await db.get_cart_total(user_id)
    await show_text(callback.message,
        format_cart_text(cart, total), reply_markup=keyboards.cart(cart)
    )
    await callback.answer("Позиция удалена")
