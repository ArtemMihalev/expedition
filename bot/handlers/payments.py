import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from bot.config import YOOKASSA_PROVIDER_TOKEN
from bot.database.db import Database
from bot.keyboards.inline import keyboards

router = Router()
logger = logging.getLogger(__name__)


def parse_payload(payload: str) -> int | None:
    prefix, separator, value = payload.partition(":")
    if prefix == "expedition" and separator and value.isascii() and value.isdigit():
        return int(value)
    return None


async def send_order_invoice(message: Message, order: dict) -> None:
    if not YOOKASSA_PROVIDER_TOKEN:
        await message.answer("Тестовая ЮKassa пока не настроена. Заказ сохранён.")
        return
    try:
        await message.bot.send_invoice(
            chat_id=order["user_id"],
            title=f"Expedition: заказ №{order['id']}",
            description="Тестовая покупка товаров для путешествий. Реальные деньги не списываются.",
            payload=f"expedition:{order['id']}",
            provider_token=YOOKASSA_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=f"Заказ №{order['id']}", amount=Database.amount_kopecks(order))],
            start_parameter=f"order-{order['id']}",
        )
    except TelegramAPIError:
        # Не выводим исключение: в запросе может присутствовать токен провайдера.
        logger.warning("Не удалось отправить счёт заказа %s", order["id"])
        await message.answer("Не удалось выставить счёт. Заказ сохранён: повторите оплату в «Моих заказах».")


@router.callback_query(F.data.startswith("pay:"))
async def pay_order(callback: CallbackQuery, db: Database) -> None:
    value = callback.data.partition(":")[2]
    order = await db.get_order(int(value), callback.from_user.id) if value.isdigit() else None
    if not order or not await db.validate_payment(order["id"], callback.from_user.id, "RUB", db.amount_kopecks(order)):
        await callback.answer("Заказ недоступен для оплаты или уже оплачен.", show_alert=True)
        return
    await callback.answer()
    await send_order_invoice(callback.message, order)


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, db: Database) -> None:
    order_id = parse_payload(query.invoice_payload)
    valid = bool(order_id and await db.validate_payment(order_id, query.from_user.id, query.currency, query.total_amount))
    await query.answer(ok=valid, **({} if valid else {"error_message": "Заказ недоступен, уже оплачен или сумма неверна. Откройте «Мои заказы»."}))


@router.message(F.successful_payment)
async def successful_payment(message: Message, db: Database) -> None:
    payment = message.successful_payment
    order_id = parse_payload(payment.invoice_payload)
    valid = bool(order_id and await db.record_payment(
        order_id, message.from_user.id, payment.currency, payment.total_amount,
        payment.telegram_payment_charge_id, payment.provider_payment_charge_id,
    ))
    if not valid:
        logger.error("Платёж не сопоставлен с заказом %s", order_id)
        await message.answer("Платёж получен, но не сопоставлен с заказом. Обратитесь к менеджеру.")
        return
    order = await db.get_order(order_id, message.from_user.id)
    from bot.handlers.orders import format_order
    await message.answer(format_order(order), reply_markup=keyboards.order_actions(order))
