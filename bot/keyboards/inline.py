from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import ORDER_STATUS_LABELS, PICKUP_OPTIONS


class Keyboards:
    @staticmethod
    def main_menu(is_manager: bool = False) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🛍 Каталог", callback_data="catalog"),
            InlineKeyboardButton(text="🛒 Корзина", callback_data="cart"),
        )
        builder.row(
            InlineKeyboardButton(text="📦 Мои заказы", callback_data="my_orders"),
        )
        if is_manager:
            builder.row(
                InlineKeyboardButton(
                    text="👔 Новые заказы", callback_data="manager_orders"
                )
            )
        return builder.as_markup()

    @staticmethod
    def back_to_menu() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="◀️ В меню", callback_data="menu")
        )
        return builder.as_markup()

    @staticmethod
    def catalog_categories(categories: list[tuple[str, str]]) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        for cat_id, cat_label in categories:
            builder.row(
                InlineKeyboardButton(
                    text=cat_label, callback_data=f"cat:{cat_id}"
                )
            )
        builder.row(
            InlineKeyboardButton(text="📋 Весь каталог", callback_data="cat:all")
        )
        builder.row(
            InlineKeyboardButton(text="◀️ В меню", callback_data="menu")
        )
        return builder.as_markup()

    @staticmethod
    def product_list(products: list[dict]) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        for p in products:
            builder.row(
                InlineKeyboardButton(
                    text=f"{p['name']} — {p['price']:.0f} ₽",
                    callback_data=f"product:{p['id']}",
                )
            )
        builder.row(
            InlineKeyboardButton(text="◀️ К категориям", callback_data="catalog"),
            InlineKeyboardButton(text="🛒 Корзина", callback_data="cart"),
        )
        return builder.as_markup()

    @staticmethod
    def product_card(product_id: int) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="➕ В корзину", callback_data=f"add:{product_id}:1"
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="➕ Добавить 2 шт.", callback_data=f"add:{product_id}:2"
            )
        )
        builder.row(
            InlineKeyboardButton(text="◀️ Назад", callback_data="catalog"),
            InlineKeyboardButton(text="🛒 Корзина", callback_data="cart"),
        )
        return builder.as_markup()

    @staticmethod
    def cart(cart_items: list[dict]) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        for item in cart_items:
            pid = item["product_id"]
            builder.row(
                InlineKeyboardButton(
                    text="➖", callback_data=f"cart_dec:{pid}"
                ),
                InlineKeyboardButton(
                    text=f"{item['quantity']} шт.", callback_data=f"cart_qty:{pid}"
                ),
                InlineKeyboardButton(
                    text="➕", callback_data=f"cart_inc:{pid}"
                ),
                InlineKeyboardButton(
                    text="🗑", callback_data=f"cart_del:{pid}"
                ),
            )
        if cart_items:
            builder.row(
                InlineKeyboardButton(
                    text="✅ Оформить заказ", callback_data="checkout_start"
                )
            )
        builder.row(
            InlineKeyboardButton(text="🛍 Каталог", callback_data="catalog"),
            InlineKeyboardButton(text="◀️ В меню", callback_data="menu"),
        )
        return builder.as_markup()

    @staticmethod
    def pickup_options() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        for key, label in PICKUP_OPTIONS.items():
            builder.row(
                InlineKeyboardButton(
                    text=label, callback_data=f"pickup:{key}"
                )
            )
        builder.row(
            InlineKeyboardButton(text="◀️ Отмена", callback_data="cart")
        )
        return builder.as_markup()

    @staticmethod
    def confirm_order(confirm_token: str) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="✅ Подтвердить заказ",
                callback_data=f"confirm:{confirm_token}",
            )
        )
        builder.row(
            InlineKeyboardButton(text="◀️ Изменить корзину", callback_data="cart")
        )
        return builder.as_markup()

    @staticmethod
    def orders_list(orders: list[dict]) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        for order in orders[:10]:
            builder.row(
                InlineKeyboardButton(
                    text=f"Заказ №{order['id']} — {order['total']:.0f} ₽",
                    callback_data=f"order:{order['id']}",
                )
            )
        builder.row(
            InlineKeyboardButton(text="◀️ В меню", callback_data="menu")
        )
        return builder.as_markup()

    @staticmethod
    def manager_orders(orders: list[dict]) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        for order in orders:
            name = order.get("customer_name", "—")
            builder.row(
                InlineKeyboardButton(
                    text=f"№{order['id']} | {name} | {order['total']:.0f} ₽",
                    callback_data=f"mgr_order:{order['id']}",
                )
            )
        builder.row(
            InlineKeyboardButton(text="◀️ В меню", callback_data="menu")
        )
        return builder.as_markup()

    @staticmethod
    def manager_status(order_id: int) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        for status, label in ORDER_STATUS_LABELS.items():
            if status == "new":
                continue
            builder.row(
                InlineKeyboardButton(
                    text=f"→ {label}",
                    callback_data=f"mgr_status:{order_id}:{status}",
                )
            )
        builder.row(
            InlineKeyboardButton(
                text="◀️ К списку", callback_data="manager_orders"
            )
        )
        return builder.as_markup()


keyboards = Keyboards()
