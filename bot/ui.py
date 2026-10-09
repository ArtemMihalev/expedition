"""Navigation between photo cards and text screens."""
from pathlib import Path
import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaPhoto, Message

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


async def show_text(message: Message, text: str, **kwargs) -> None:
    if message.photo:
        await message.edit_reply_markup(reply_markup=None)
        await message.answer(text, **kwargs)
    else:
        try:
            await message.edit_text(text, **kwargs)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise


async def show_product_card(message: Message, product: dict, text: str, markup) -> None:
    relative_path = product.get("image_path")
    image_path = PROJECT_ROOT / relative_path if relative_path else None
    if image_path is None or not image_path.is_file():
        logger.warning("Карточка товара %s без фото: image_path=%r, файл=%s", product.get("id"), relative_path, image_path)
        await show_text(message, text, reply_markup=markup)
        return
    logger.info("Отправка фото товара %s: %s", product["id"], image_path)
    photo = FSInputFile(image_path)
    if message.photo:
        await message.edit_media(InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"), reply_markup=markup)
    else:
        await message.edit_reply_markup(reply_markup=None)
        await message.answer_photo(photo=photo, caption=text, reply_markup=markup)
