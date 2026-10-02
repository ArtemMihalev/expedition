from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import MANAGER_IDS, ORDER_STATUS_LABELS, PAYMENT_LABEL, PICKUP_OPTIONS
from bot.database.db import Database
from bot.keyboards.inline import keyboards
from bot.states import CheckoutStates

router = Router()


def format_review(
    cart: list[dict],
    total: float,
    customer_name: str,
    pickup_key: str,
) -> str:
    pickup_label = PICKUP_OPTIONS.get(pickup_key, pickup_key)
    lines = [
        "📋 <b>Проверьте заказ</b>\n",
        f"👤 Имя: <b>{customer_name}</b>",
        f"📍 Получение: {pickup_label}",
        f"💳 Расчёт: {PAYMENT_LABEL}\n",
        "<b>Состав:</b>",
    ]
    for item in cart:
        subtotal = item["price"] * item["quantity"]
        lines.append(
            f"• {item['name']} — {item['quantity']} × {item['price']:.0f} ₽ "
            f"= {subtotal:.0f} ₽"
        )
    lines.append(f"\n💰 <b>Итого: {total:.0f} ₽</b>")
    lines.append(
        "\nНажмите «Подтвердить заказ», чтобы оформить покупку."
    )
    return "\n".join(lines)


@router.callback_query(lambda c: c.data == "checkout_start")
async def checkout_start(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    user_id = callback.from_user.id
    cart = await db.get_cart(user_id)
    if not cart:
        await callback.answer(
            "Корзина пуста. Добавьте товары перед оформлением.", show_alert=True
        )
        return

    await state.set_state(CheckoutStates.waiting_name)
    await callback.message.edit_text(
        "✏️ Введите ваше имя или псевдоним для заказа:",
        reply_markup=keyboards.back_to_menu(),
    )
    await callback.answer()


@router.message(CheckoutStates.waiting_name)
async def checkout_name(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, отправьте текстовое имя или псевдоним.")
        return
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("Имя слишком короткое. Введите минимум 2 символа.")
        return
    if len(name) > 64:
        await message.answer("Имя слишком длинное. Максимум 64 символа.")
        return

    await state.update_data(customer_name=name)
    await state.set_state(CheckoutStates.waiting_pickup)
    await message.answer(
        "📍 Выберите способ получения:",
        reply_markup=keyboards.pickup_options(),
    )


@router.callback_query(
    CheckoutStates.waiting_pickup, lambda c: c.data and c.data.startswith("pickup:")
)
async def checkout_pickup(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    pickup_key = callback.data.split(":")[1]
    if pickup_key not in PICKUP_OPTIONS:
        await callback.answer("Неверный способ получения.", show_alert=True)
        return

    user_id = callback.from_user.id
    cart = await db.get_cart(user_id)
    if not cart:
        await state.clear()
        await callback.answer("Корзина пуста.", show_alert=True)
        return

    data = await state.get_data()
    customer_name = data["customer_name"]
    total = await db.get_cart_total(user_id)
    confirm_token = await db.create_checkout_token(user_id)

    await state.update_data(pickup_key=pickup_key, confirm_token=confirm_token)
    await state.set_state(CheckoutStates.review)

    text = format_review(cart, total, customer_name, pickup_key)
    await callback.message.edit_text(
        text, reply_markup=keyboards.confirm_order(confirm_token)
    )
    await callback.answer()


@router.callback_query(
    CheckoutStates.review, lambda c: c.data and c.data.startswith("confirm:")
)
async def checkout_confirm(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    token = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    data = await state.get_data()

    if data.get("confirm_token") != token:
        await callback.answer(
            "Устаревшая кнопка. Начните оформление заново.", show_alert=True
        )
        return

    order_id, status = await db.confirm_order(
        user_id,
        token,
        data["customer_name"],
        data["pickup_key"],
    )

    if status == "already_confirmed":
        await callback.answer("Заказ уже оформлен.", show_alert=True)
        order = await db.get_order(order_id, user_id)
        await _show_success(callback, order_id, order)
        await state.clear()
        return

    if order_id is None:
        await callback.answer(status, show_alert=True)
        await state.clear()
        return

    await callback.answer("✅ Заказ оформлен!")
    order = await db.get_order(order_id, user_id)
    await _show_success(callback, order_id, order)
    await state.clear()


async def _show_success(callback: CallbackQuery, order_id: int, order: dict | None) -> None:
    pickup_label = PICKUP_OPTIONS.get(
        order["pickup_method"] if order else "", "—"
    )
    status_label = ORDER_STATUS_LABELS.get(
        order["status"] if order else "new", "Новый"
    )
    total = order["total"] if order else 0

    text = (
        f"🎉 <b>Заказ №{order_id} оформлен!</b>\n\n"
        f"💰 Сумма: <b>{total:.0f} ₽</b>\n"
        f"📍 Получение: {pickup_label}\n"
        f"💳 {PAYMENT_LABEL}\n"
        f"📊 Статус: <b>{status_label}</b>\n\n"
        "Мы сообщим, когда заказ будет готов к выдаче."
    )
    await callback.message.edit_text(
        text,
        reply_markup=keyboards.main_menu(
            is_manager=callback.from_user.id in MANAGER_IDS
        ),
    )
