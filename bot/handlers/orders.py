from aiogram import Router
from aiogram.types import CallbackQuery

from bot.config import ORDER_STATUS_LABELS, PAYMENT_LABEL, PICKUP_OPTIONS
from bot.database.db import Database
from bot.keyboards.inline import keyboards

router = Router()


def format_order(order: dict) -> str:
    pickup_label = PICKUP_OPTIONS.get(order["pickup_method"], order["pickup_method"])
    status_label = ORDER_STATUS_LABELS.get(order["status"], order["status"])
    lines = [
        f"📦 <b>Заказ №{order['id']}</b>\n",
        f"📅 Дата: {order['created_at']}",
        f"👤 Имя: {order['customer_name']}",
        f"📍 Получение: {pickup_label}",
        f"💳 {PAYMENT_LABEL}",
        f"📊 Статус: <b>{status_label}</b>\n",
        "<b>Состав:</b>",
    ]
    for item in order["items"]:
        subtotal = item["product_price"] * item["quantity"]
        lines.append(
            f"• {item['product_name']} — {item['quantity']} × "
            f"{item['product_price']:.0f} ₽ = {subtotal:.0f} ₽"
        )
    lines.append(f"\n💰 <b>Итого: {order['total']:.0f} ₽</b>")
    return "\n".join(lines)


@router.callback_query(lambda c: c.data == "my_orders")
async def my_orders(callback: CallbackQuery, db: Database) -> None:
    orders = await db.get_user_orders(callback.from_user.id)
    if not orders:
        await callback.message.edit_text(
            "📦 У вас пока нет заказов.\n\nПерейдите в каталог, чтобы сделать первый заказ.",
            reply_markup=keyboards.back_to_menu(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "📦 <b>Мои заказы</b>\n\nВыберите заказ для просмотра:",
        reply_markup=keyboards.orders_list(orders),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("order:"))
async def show_order(callback: CallbackQuery, db: Database) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id, callback.from_user.id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    await callback.message.edit_text(
        format_order(order), reply_markup=keyboards.back_to_menu()
    )
    await callback.answer()
