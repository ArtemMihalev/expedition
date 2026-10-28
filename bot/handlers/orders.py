from bot.ui import show_text
from html import escape
from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.config import ORDER_STATUS_LABELS, PAYMENT_LABEL, PAYMENT_LABELS, PAYMENT_STATUS_LABELS, PICKUP_OPTIONS
from bot.database.db import Database
from bot.keyboards.inline import keyboards

router = Router()


def format_order(order: dict) -> str:
    pickup_label = PICKUP_OPTIONS.get(order["pickup_method"], order["pickup_method"])
    status_label = ORDER_STATUS_LABELS.get(order["status"], order["status"])
    if order['payment_status'] == 'pending' and order['status'] == 'new':
        status_label = 'Ожидает оплаты'
    elif order['payment_status'] == 'paid' and order['status'] == 'new':
        status_label = 'Оплачен'
    lines = [
        f"📦 <b>Заказ №{order['id']}</b>\n",
        f"📅 Дата: {order['created_at']}",
        f"👤 Имя: {escape(order['customer_name'])}",
        f"📍 Получение: {pickup_label}",
        f"💳 {PAYMENT_LABELS.get(order['payment_method'], PAYMENT_LABEL)}",
        f"Оплата: {PAYMENT_STATUS_LABELS.get(order.get('payment_status', 'unpaid'), '—')}",
        f"📊 Статус: <b>{status_label}</b>\n",
        "<b>Состав:</b>",
    ]
    for item in order["items"]:
        subtotal = item["product_price"] * item["quantity"]
        lines.append(
            f"• {item['product_name']} — {item['quantity']} × "
            f"{item['product_price']:.2f} ₽ = {subtotal:.2f} ₽"
        )
    lines.append(f"\n💰 <b>Итого: {order['total']:.2f} ₽</b>")
    if order.get('expires_at') and order.get('reservation_state') == 'active':
        seconds=max(0,int((datetime.fromisoformat(order['expires_at']).replace(tzinfo=timezone.utc)-datetime.now(timezone.utc)).total_seconds()))
        lines.append(f'\nРезерв: ещё {seconds} с. Обновите заказ для актуального времени.')
        lines.append('Подтверждение после срока запускает возврат, даже если вы нажали оплату раньше.')
    if order.get('conflict_reason'):
        lines.append('\n'+order['conflict_reason'])
    return "\n".join(lines)


@router.callback_query(lambda c: c.data == "my_orders")
async def my_orders(callback: CallbackQuery, db: Database) -> None:
    orders = await db.get_user_orders(callback.from_user.id)
    if not orders:
        await show_text(callback.message,
            "📦 У вас пока нет заказов.\n\nПерейдите в каталог, чтобы сделать первый заказ.",
            reply_markup=keyboards.back_to_menu(),
        )
        await callback.answer()
        return

    await show_text(callback.message,
        "📦 <b>Мои заказы</b>\n\nВыберите заказ для просмотра:",
        reply_markup=keyboards.orders_list(orders),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("cancel_order:"))
async def cancel_order_prompt(callback: CallbackQuery, db: Database) -> None:
    value = callback.data.partition(":")[2]
    order = await db.get_order(int(value), callback.from_user.id) if value.isdigit() else None
    if not order or order["status"] not in {"new", "ready"} or order["payment_status"] in {"paid","refund_pending","refunded"}:
        await callback.answer("Заказ недоступен для отмены или уже закрыт.", show_alert=True)
        return
    text = f"Отменить заказ №{order['id']}? Зарезервированные товары вернутся на склад."
    if order.get("payment_status") == "paid":
        text += "\n\nЗаказ оплачен тестовым платежом. Отмена не выполняет возврат платежа; для возврата обратитесь к менеджеру."
    await show_text(callback.message, text, reply_markup=keyboards.cancel_order_confirmation(order["id"]))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("cancel_confirm:"))
async def cancel_order_confirm(callback: CallbackQuery, db: Database) -> None:
    value = callback.data.partition(":")[2]
    if not value.isdigit():
        await callback.answer("Устаревшая кнопка.", show_alert=True)
        return
    order_id = int(value)
    if not await db.update_order_status(order_id, "cancelled", user_id=callback.from_user.id):
        await callback.answer("Заказ уже закрыт или недоступен для отмены.", show_alert=True)
        return
    order = await db.get_order(order_id, callback.from_user.id)
    await show_text(callback.message, format_order(order), reply_markup=keyboards.order_actions(order))
    await callback.answer("Заказ отменён. Резерв возвращён на склад.", show_alert=True)


@router.callback_query(lambda c: c.data and c.data.startswith("order:"))
async def show_order(callback: CallbackQuery, db: Database) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id, callback.from_user.id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    await show_text(callback.message,
        format_order(order), reply_markup=keyboards.order_actions(order)
    )
    await callback.answer()
