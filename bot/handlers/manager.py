from aiogram import Router
from aiogram.types import CallbackQuery

from bot.config import MANAGER_IDS, ORDER_STATUS_LABELS, PICKUP_OPTIONS
from bot.database.db import Database
from bot.handlers.orders import format_order
from bot.keyboards.inline import keyboards

router = Router()


def is_manager(user_id: int) -> bool:
    return user_id in MANAGER_IDS


@router.callback_query(lambda c: c.data == "manager_orders")
async def manager_orders(callback: CallbackQuery, db: Database) -> None:
    if not is_manager(callback.from_user.id):
        await callback.answer("Доступ только для менеджера.", show_alert=True)
        return

    orders = await db.get_new_orders()
    if not orders:
        await callback.message.edit_text(
            "👔 <b>Новые заказы</b>\n\nНовых заказов пока нет.",
            reply_markup=keyboards.back_to_menu(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"👔 <b>Новые заказы ({len(orders)})</b>\n\nВыберите заказ:",
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
    await callback.message.edit_text(
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
        await callback.answer("Не удалось обновить статус.", show_alert=True)
        return

    label = ORDER_STATUS_LABELS[status]
    await callback.answer(f"Статус изменён: {label}")

    order = await db.get_order(order_id)
    if order:
        text = format_order(order)
        if order["status"] == "new":
            markup = keyboards.manager_status(order_id)
        else:
            markup = keyboards.back_to_menu()
        await callback.message.edit_text(text, reply_markup=markup)
