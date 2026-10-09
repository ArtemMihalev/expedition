from bot.ui import show_text
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import MANAGER_IDS
from bot.database.db import Database
from bot.keyboards.inline import keyboards

router = Router()

WELCOME_TEXT = (
    "🌍 <b>Expedition</b> — магазин товаров для путешествий\n\n"
    "У нас вы найдёте рюкзаки, органайзеры, адаптеры, "
    "дорожные аксессуары и тревел-наборы для комфортных поездок.\n\n"
    "Выберите действие в меню:"
)


async def show_menu(
    target: Message | CallbackQuery, db: Database, state: FSMContext | None = None
) -> None:
    if state:
        await state.clear()
    user = target.from_user
    await db.upsert_user(user.id, user.username, user.first_name)
    is_manager = user.id in MANAGER_IDS
    markup = keyboards.main_menu(is_manager=is_manager)

    if isinstance(target, CallbackQuery):
        await show_text(target.message, WELCOME_TEXT, reply_markup=markup)
        await target.answer()
    else:
        await target.answer(WELCOME_TEXT, reply_markup=markup)


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, state: FSMContext) -> None:
    await show_menu(message, db, state)


@router.message(Command("menu"))
async def cmd_menu(message: Message, db: Database, state: FSMContext) -> None:
    await show_menu(message, db, state)


@router.callback_query(lambda c: c.data == "menu")
async def cb_menu(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await show_menu(callback, db, state)
