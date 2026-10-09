from bot.ui import show_text
import logging
from html import escape

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

from bot.config import MANAGER_IDS, ORDER_STATUS_LABELS, PICKUP_OPTIONS
from bot.database.db import Database
from bot.handlers.orders import format_order
from bot.keyboards.inline import keyboards

router = Router()
logger = logging.getLogger(__name__)


def is_manager(user_id: int) -> bool:
    return user_id in MANAGER_IDS


@router.callback_query(lambda c: c.data == "manager_orders")
async def manager_orders(callback: CallbackQuery, db: Database) -> None:
    if not is_manager(callback.from_user.id):
        await callback.answer("Доступ только для менеджера.", show_alert=True)
        return

    orders = await db.get_new_orders()
    if not orders:
        await show_text(callback.message,
            "👔 <b>Заказы в работе</b>\n\nЗаказов в работе пока нет.",
            reply_markup=keyboards.back_to_menu(),
        )
        await callback.answer()
        return

    await show_text(callback.message,
        f"👔 <b>Заказы в работе ({len(orders)})</b>\n\nВыберите заказ:",
        reply_markup=keyboards.manager_orders(orders),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("mgr_order:"))
async def manager_order_detail(callback: CallbackQuery, db: Database) -> None:
    if not is_manager(callback.from_user.id):
        await callback.answer("Доступ только для менеджера.", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    text = format_order(order) + f"\n\n🆔 Telegram ID покупателя: {order['user_id']}"
    await show_text(callback.message,
        text, reply_markup=keyboards.manager_status(order_id)
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("mgr_status:"))
async def manager_change_status(callback: CallbackQuery, db: Database) -> None:
    if not is_manager(callback.from_user.id):
        await callback.answer("Доступ только для менеджера.", show_alert=True)
        return

    _, order_id_str, status = callback.data.split(":")
    order_id = int(order_id_str)

    if status not in ORDER_STATUS_LABELS:
        await callback.answer("Неверный статус.", show_alert=True)
        return

    ok = await db.update_order_status(order_id, status)
    if not ok:
        await callback.answer("Статус уже установлен, заказ закрыт или ожидает оплаты. До оплаты возможна только отмена.", show_alert=True)
        return

    label = ORDER_STATUS_LABELS[status]
    order = await db.get_order(order_id)
    if order:
        delivered = await notify_customer(callback.bot, order, status)
        await callback.answer(f"Статус изменён: {label}" + (". Уведомление отправлено." if delivered else ". Уведомление не доставлено."), show_alert=not delivered)
        text = format_order(order)
        if order["status"] in {"new", "ready"}:
            markup = keyboards.manager_status(order_id)
        else:
            markup = keyboards.back_to_menu()
        await show_text(callback.message, text, reply_markup=markup)


async def notify_customer(bot, order: dict, status: str) -> bool:
    text = f"📦 Заказ №{order['id']}: <b>{ORDER_STATUS_LABELS[status]}</b>."
    if status == "ready":
        text += f"\nПолучение: {escape(PICKUP_OPTIONS.get(order['pickup_method'], '—'))}"
    try:
        await bot.send_message(order["user_id"], text, reply_markup=keyboards.order_actions(order))
        return True
    except TelegramAPIError:
        logger.warning("Уведомление заказа %s не доставлено", order["id"])
        return False


@router.callback_query(lambda c: c.data == "stock")
async def manager_stock(callback: CallbackQuery, db: Database) -> None:
    if not is_manager(callback.from_user.id):
        await callback.answer("Доступ только для менеджера.", show_alert=True)
        return
    products = await db.get_stock_products()
    await show_text(callback.message, "📦 <b>Склад</b>\nСвободные остатки (без оформленных заказов). Выберите товар:", reply_markup=keyboards.stock_products(products))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("stock_product:"))
async def manager_stock_product(callback: CallbackQuery, db: Database) -> None:
    if not is_manager(callback.from_user.id):
        await callback.answer("Доступ только для менеджера.", show_alert=True)
        return
    value = callback.data.partition(":")[2]
    products = await db.get_stock_products()
    product = next((p for p in products if str(p['id']) == value), None)
    if not product:
        await callback.answer("Товар не найден.", show_alert=True)
        return
    await show_text(callback.message, f"📦 <b>{escape(product['name'])}</b>\nСвободный остаток: {product['stock']} шт.", reply_markup=keyboards.stock_actions(product['id']))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("stock_adjust:"))
async def manager_stock_adjust(callback: CallbackQuery, db: Database) -> None:
    if not is_manager(callback.from_user.id):
        await callback.answer("Доступ только для менеджера.", show_alert=True)
        return
    try:
        _, product_id, delta = callback.data.split(":")
        product_id, delta = int(product_id), int(delta)
    except ValueError:
        await callback.answer("Устаревшая кнопка.", show_alert=True)
        return
    if delta not in {-1, 1, 5, 10} or not await db.adjust_stock(product_id, delta):
        await callback.answer("Нельзя установить отрицательный остаток или товар не найден.", show_alert=True)
        return
    products = await db.get_stock_products()
    product = next(p for p in products if p['id'] == product_id)
    await show_text(callback.message, f"📦 <b>{escape(product['name'])}</b>\nСвободный остаток: {product['stock']} шт.", reply_markup=keyboards.stock_actions(product_id))
    await callback.answer("Остаток обновлён.")
