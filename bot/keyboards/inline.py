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
                    text="👔 Заказы в работе", callback_data="manager_orders"
                )
            )
            builder.row(InlineKeyboardButton(text="📦 Склад", callback_data="stock"))
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
                    text=f"{p['name']} — {p['price']:.0f} ₽ | {p['stock']} шт.",
                    callback_data=f"product:{p['id']}",
                )
            )
        builder.row(
            InlineKeyboardButton(text="◀️ К категориям", callback_data="catalog"),
            InlineKeyboardButton(text="🛒 Корзина", callback_data="cart"),
        )
        return builder.as_markup()

    @staticmethod
    def product_card(product_id: int, stock: int = 10) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        if stock >= 1:
            builder.row(
                InlineKeyboardButton(
                    text="➕ В корзину", callback_data=f"add:{product_id}:1"
                )
            )
        builder.row(
            InlineKeyboardButton(text="◀️ Назад", callback_data="catalog"),
            InlineKeyboardButton(text="🛒 Корзина", callback_data="cart"),
        )
        return builder.as_markup()

    @staticmethod
    def stock_products(products: list[dict]) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        for product in products:
            builder.button(text=f"{product['name']} — {product['stock']} шт.", callback_data=f"stock_product:{product['id']}")
        builder.button(text="◀️ В меню", callback_data="menu")
        builder.adjust(1)
        return builder.as_markup()

    @staticmethod
    def stock_actions(product_id: int) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        for delta in (-1, 1, 5, 10):
            builder.button(text=f"{delta:+d} шт.", callback_data=f"stock_adjust:{product_id}:{delta}")
        builder.adjust(2)
        builder.row(InlineKeyboardButton(text="◀️ Склад", callback_data="stock"))
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
    def payment_options() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="При получении (по умолчанию)", callback_data="payment:cash_on_pickup")
        builder.button(text="ЮKassa — тестовая оплата", callback_data="payment:yookassa_test")
        builder.button(text="◀️ Отмена", callback_data="cart")
        builder.adjust(1)
        return builder.as_markup()

    @staticmethod
    def order_actions(order: dict) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        if order["payment_method"] == "yookassa_test" and order["payment_status"] == "pending" and order["status"] == "new":
            builder.button(text="Оплатить через ЮKassa (тест)", callback_data=f"pay:{order['id']}")
        if order["status"] in {"new", "ready"}:
            builder.button(text="Отменить заказ", callback_data=f"cancel_order:{order['id']}")
        builder.button(text="Мои заказы", callback_data="my_orders")
        builder.button(text="◀️ В меню", callback_data="menu")
        builder.adjust(1)
        return builder.as_markup()

    @staticmethod
    def cancel_order_confirmation(order_id: int) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="Да, отменить заказ", callback_data=f"cancel_confirm:{order_id}")
        builder.button(text="Нет, вернуться к заказу", callback_data=f"order:{order_id}")
        builder.adjust(1)
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
