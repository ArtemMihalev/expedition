import asyncio
import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramAPIError

from bot.config import MANAGER_IDS, DEMO_PAYMENTS_ENABLED, MINI_APP_URL
from bot.database.finance import MOSCOW, now
from bot.database.db import Database
from bot.keyboards.inline import keyboards
from bot.ui import show_text

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command('id'))
async def own_id(message: Message):
    await message.answer(f'Ваш Telegram ID: <code>{message.from_user.id}</code>')


def money(amount):
    return 'не определён (нет исторической себестоимости)' if amount is None else f'{amount / 100:,.2f} ₽'.replace(',', ' ')


@router.message(Command('summary'))
async def summary_command(message: Message, db: Database):
    if message.from_user.id not in MANAGER_IDS:
        await message.answer('Финансовая сводка доступна только менеджеру.')
        return
    today = datetime.now(MOSCOW).date()
    args = message.text.split()[1:]
    try:
        if args and len(args) != 2:
            raise ValueError('Формат: /summary YYYY-MM-DD YYYY-MM-DD')
        start, end = args if args else (today.replace(day=1).isoformat(), today.isoformat())
        value = await db.summary(start, end)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        f'<b>Expedition · учебные финансы</b>\n{start} — {end}, Москва\n\n'
        f'Поступления: {money(value["receipts"])}\nВозвраты: {money(value["refunds"])}\n'
        f'Операционные расходы: {money(value["expenses"])}\nСебестоимость продаж: {money(value["cost_paid"])}\n'
        f'Себестоимость возвратов: {money(value["cost_returned"])}\n\n'
        f'<b>Денежный поток: {money(value["cash_flow"])}</b>\n'
        f'<b>Учебный результат: {money(value["result"])}</b>\n\n'
        'Поток = поступления − возвраты − расходы.\nРезультат = поток − себестоимость продаж + себестоимость возвратов.',
        reply_markup=keyboards.finance_cabinet(),
    )


@router.message(Command('cabinet'))
async def cabinet(message: Message):
    if message.from_user.id not in MANAGER_IDS:
        await message.answer('Кабинет доступен только менеджеру.')
        return
    await message.answer('Финансовый кабинет Expedition' if MINI_APP_URL else 'Добавьте HTTPS-адрес в MINI_APP_URL файла .env и перезапустите бота.', reply_markup=keyboards.finance_cabinet())


@router.callback_query(F.data == 'finance_help')
async def finance_help(callback: CallbackQuery):
    if callback.from_user.id not in MANAGER_IDS:
        await callback.answer('Доступ только для менеджера.', show_alert=True)
        return
    await callback.answer('Для Mini App задайте MINI_APP_URL с HTTPS в .env. Сводка доступна командой /summary.', show_alert=True)


@router.callback_query(F.data.startswith('demo_pay:'))
async def demo_pay(callback: CallbackQuery, db: Database):
    if not DEMO_PAYMENTS_ENABLED:
        await callback.answer('Имитатор выключен.', show_alert=True)
        return
    try:
        _, order_id, outcome = callback.data.split(':')
        await db.demo_payment(int(order_id), callback.from_user.id, outcome)
    except (ValueError, TypeError):
        await callback.answer('Учебный платёж уже завершён или недоступен.', show_alert=True)
        return
    from bot.handlers.orders import format_order
    order = await db.get_order(int(order_id), callback.from_user.id)
    await show_text(callback.message, format_order(order), reply_markup=keyboards.order_actions(order))
    await callback.answer('Учебный платёж завершён. Реальные деньги не переводились.')


async def notification_worker(bot, db):
    """Durable queue: receipt and notification task are committed together."""
    while True:
        try:
            async with db.connection() as conn:
                rows = await (await conn.execute("""SELECT n.*,f.order_id,f.amount_kopecks FROM finance_notifications n
                    JOIN finance_operations f ON f.id=n.operation_id WHERE sent_at IS NULL
                    AND (next_attempt_at IS NULL OR next_attempt_at<=?) LIMIT 20""", (now(),))).fetchall()
            for row in rows:
                if row['manager_id'] not in MANAGER_IDS:
                    continue
                try:
                    await bot.send_message(row['manager_id'], f'💳 Заказ №{row["order_id"]} оплачен.\nУчебное поступление: {money(row["amount_kopecks"])}', reply_markup=keyboards.finance_cabinet())
                    async with db.connection() as conn:
                        await conn.execute('UPDATE finance_notifications SET sent_at=? WHERE operation_id=? AND manager_id=?', (now(), row['operation_id'], row['manager_id']))
                        await conn.commit()
                except TelegramAPIError:
                    async with db.connection() as conn:
                        await conn.execute("UPDATE finance_notifications SET attempts=attempts+1,next_attempt_at=datetime('now','+60 seconds') WHERE operation_id=? AND manager_id=?", (row['operation_id'], row['manager_id']))
                        await conn.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning('Не удалось обработать очередь уведомлений; повтор будет позже.')
        await asyncio.sleep(2)
