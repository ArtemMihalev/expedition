from bot.ui import show_text
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import MANAGER_IDS, ORDER_STATUS_LABELS, PAYMENT_LABEL, PAYMENT_LABELS, YOOKASSA_PROVIDER_TOKEN, PICKUP_OPTIONS, DEMO_PAYMENTS_ENABLED
from bot.database.db import Database
from bot.keyboards.inline import keyboards
from bot.states import CheckoutStates

router = Router()


def format_review(
    cart: list[dict],
    total: float,
    customer_name: str,
    pickup_key: str,
    payment_method: str = "cash_on_pickup",
) -> str:
    pickup_label = PICKUP_OPTIONS.get(pickup_key, pickup_key)
    lines = [
        "📋 <b>Проверьте заказ</b>\n",
        f"👤 Имя: <b>{escape(customer_name)}</b>",
        f"📍 Получение: {pickup_label}",
        f"💳 Расчёт: {PAYMENT_LABELS[payment_method]}\n",
        "<b>Состав:</b>",
    ]
    for item in cart:
        subtotal = item["price"] * item["quantity"]
        lines.append(
            f"• {item['name']} — {item['quantity']} × {item['price']:.2f} ₽ "
            f"= {subtotal:.2f} ₽"
        )
    lines.append(f"\n💰 <b>Итого: {total:.2f} ₽</b>")
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
    await show_text(callback.message,
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

    await state.update_data(pickup_key=pickup_key)
    await state.set_state(CheckoutStates.waiting_payment)
    await show_text(callback.message, "💳 Выберите способ оплаты:", reply_markup=keyboards.payment_options())
    await callback.answer()


@router.callback_query(CheckoutStates.waiting_payment, F.data.startswith("payment:"))
async def checkout_payment(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    payment_method = callback.data.split(":", 1)[1]
    if payment_method not in PAYMENT_LABELS:
        await callback.answer("Неверный способ оплаты.", show_alert=True)
        return
    if payment_method == 'demo' and not DEMO_PAYMENTS_ENABLED:
        await callback.answer('Учебный имитатор выключен.', show_alert=True)
        return
    if payment_method == "yookassa_test" and not YOOKASSA_PROVIDER_TOKEN:
        await callback.answer("Тестовая ЮKassa пока не настроена. Выберите оплату при получении.", show_alert=True)
        return
    user_id = callback.from_user.id
    cart = await db.get_cart(user_id)
    if not cart:
        await state.clear()
        await callback.answer("Корзина пуста.", show_alert=True)
        return
    data = await state.get_data()
    pickup_key = data["pickup_key"]
    customer_name = data["customer_name"]
    total = await db.get_cart_total(user_id)
    confirm_token = await db.create_checkout_token(user_id)

    await state.update_data(pickup_key=pickup_key, confirm_token=confirm_token, payment_method=payment_method)
    await state.set_state(CheckoutStates.review)

    text = format_review(cart, total, customer_name, pickup_key, payment_method)
    await show_text(callback.message,
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
        data.get("payment_method", "cash_on_pickup"),
    )

    if status == "already_confirmed":
        await callback.answer("Заказ уже оформлен.", show_alert=True)
        order = await db.get_order(order_id, user_id)
        await _show_success(callback, order_id, order)
        await state.clear()
        return

    if order_id is None:
        await callback.answer(status, show_alert=True)
        if not status.startswith("Склад занят"):
            await state.clear()
        return

    await callback.answer("✅ Заказ оформлен!")
    order = await db.get_order(order_id, user_id)
    await _show_success(callback, order_id, order)
    await state.clear()
    if order["payment_method"] == "yookassa_test":
        from bot.handlers.payments import send_order_invoice
        await send_order_invoice(callback.message, order)


async def _show_success(callback: CallbackQuery, order_id: int, order: dict | None) -> None:
    from bot.handlers.orders import format_order
    await show_text(callback.message, format_order(order), reply_markup=keyboards.order_actions(order))


@router.callback_query(F.data.startswith("confirm:"))
async def stale_confirmation(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка уже обработана или устарела. Проверьте «Мои заказы».", show_alert=True)
