from decimal import Decimal
import aiosqlite
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from bot.config import (
    ORDER_STATUS_NEW,
    PAYMENT_METHOD,
    PICKUP_OPTIONS,
    ORDER_STATUS_LABELS,
)

SEED_PRODUCTS = [
    (
        "Рюкзак TrekPro 40L",
        "Лёгкий трекинговый рюкзак 40 литров с системой вентиляции спины. "
        "Подходит для походов на 2–3 дня.",
        4990.0,
        "backpacks",
    ),
    (
        "Органайзер TravelCase",
        "Компактный органайзер для документов, карт и мелочей. "
        "Водоотталкивающий материал, 12 отделений.",
        890.0,
        "organizers",
    ),
    (
        "Адаптер WorldPlug",
        "Универсальный travel-адаптер для 150+ стран. "
        "2 USB-порта, защита от перегрузки.",
        1290.0,
        "adapters",
    ),
    (
        "Набор ComfortSet",
        "Маска для сна, беруши и мягкая подушка для шеи. "
        "Идеально для длительных перелётов.",
        590.0,
        "accessories",
    ),
    (
        "Тревел-набор SafeTrip",
        "Аптечка первой помощи для путешествий: 28 предметов. "
        "Компактный кейс, список содержимого внутри.",
        1490.0,
        "kits",
    ),
    (
        "Сумка PackIt для обуви",
        "Складная сумка для обуви с вентиляцией. "
        "Не пачкает вещи в рюкзаке.",
        690.0,
        "accessories",
    ),
]

CATEGORY_LABELS = {
    "backpacks": "Рюкзаки",
    "organizers": "Органайзеры",
    "adapters": "Адаптеры",
    "accessories": "Дорожные аксессуары",
    "kits": "Тревел-наборы",
}

PRODUCT_IMAGES = dict(zip(
    (product[0] for product in SEED_PRODUCTS),
    (f"assets/products/{key}.png" for key in ("trekpro", "travelcase", "worldplug", "comfortset", "safetrip", "packit")),
))


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON")
            yield conn

    async def init_db(self) -> None:
        async with self.connection() as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    platform TEXT NOT NULL DEFAULT 'telegram',
                    username TEXT,
                    first_name TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    price REAL NOT NULL CHECK(price > 0),
                    category TEXT NOT NULL,
                    is_available INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS cart_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL CHECK(quantity > 0),
                    UNIQUE(user_id, product_id),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (product_id) REFERENCES products(id)
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    customer_name TEXT NOT NULL,
                    pickup_method TEXT NOT NULL,
                    payment_method TEXT NOT NULL DEFAULT 'cash_on_pickup',
                    total REAL NOT NULL CHECK(total >= 0),
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    product_name TEXT NOT NULL,
                    product_price REAL NOT NULL,
                    quantity INTEGER NOT NULL CHECK(quantity > 0),
                    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS checkout_tokens (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    order_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (order_id) REFERENCES orders(id)
                );
                """
            )
            columns = {row["name"] for row in await (await conn.execute("PRAGMA table_info(orders)")).fetchall()}
            for name, definition in {
                "payment_status": "TEXT NOT NULL DEFAULT 'unpaid'",
                "telegram_payment_charge_id": "TEXT",
                "provider_payment_charge_id": "TEXT",
                "paid_at": "TEXT",
                "stock_deducted": "INTEGER NOT NULL DEFAULT 0",
            }.items():
                if name not in columns:
                    await conn.execute(f"ALTER TABLE orders ADD COLUMN {name} {definition}")
            product_columns = {row["name"] for row in await (await conn.execute("PRAGMA table_info(products)")).fetchall()}
            for name, definition in {
                "stock": "INTEGER NOT NULL DEFAULT 10 CHECK(stock >= 0)",
                "image_path": "TEXT",
            }.items():
                if name not in product_columns:
                    await conn.execute(f"ALTER TABLE products ADD COLUMN {name} {definition}")
            item_columns = {row["name"] for row in await (await conn.execute("PRAGMA table_info(order_items)")).fetchall()}
            if "product_id" not in item_columns:
                await conn.execute("ALTER TABLE order_items ADD COLUMN product_id INTEGER REFERENCES products(id)")
            await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS payment_charge_unique ON orders(telegram_payment_charge_id)")
            await conn.commit()

            cursor = await conn.execute("SELECT COUNT(*) AS cnt FROM products")
            row = await cursor.fetchone()
            if row["cnt"] == 0:
                await conn.executemany(
                    """
                    INSERT INTO products (name, description, price, category)
                    VALUES (?, ?, ?, ?)
                    """,
                    SEED_PRODUCTS,
                )
                await conn.commit()
            for name, image_path in PRODUCT_IMAGES.items():
                await conn.execute("UPDATE products SET image_path = ? WHERE name = ? AND image_path IS NULL", (image_path, name))
            await conn.commit()

    async def upsert_user(
        self, user_id: int, username: str | None, first_name: str | None
    ) -> None:
        async with self.connection() as conn:
            await conn.execute(
                """
                INSERT INTO users (id, platform, username, first_name)
                VALUES (?, 'telegram', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name
                """,
                (user_id, username, first_name),
            )
            await conn.commit()

    async def get_products(self, category: str | None = None) -> list[dict]:
        async with self.connection() as conn:
            if category:
                cursor = await conn.execute(
                    """
                    SELECT * FROM products
                    WHERE is_available = 1 AND category = ?
                    ORDER BY id
                    """,
                    (category,),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT * FROM products
                    WHERE is_available = 1
                    ORDER BY category, id
                    """
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_product(self, product_id: int) -> dict | None:
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM products WHERE id = ? AND is_available = 1",
                (product_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_categories(self) -> list[tuple[str, str]]:
        async with self.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT DISTINCT category FROM products
                WHERE is_available = 1
                ORDER BY category
                """
            )
            rows = await cursor.fetchall()
            return [(r["category"], CATEGORY_LABELS.get(r["category"], r["category"])) for r in rows]

    async def add_to_cart(self, user_id: int, product_id: int, quantity: int = 1) -> bool:
        if type(quantity) is not int or quantity <= 0:
            return False
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            product = await (await conn.execute("SELECT stock, is_available FROM products WHERE id = ?", (product_id,))).fetchone()
            if not product or not product["is_available"]:
                return False
            cursor = await conn.execute(
                "SELECT quantity FROM cart_items WHERE user_id = ? AND product_id = ?",
                (user_id, product_id),
            )
            row = await cursor.fetchone()
            if (row["quantity"] if row else 0) + quantity > product["stock"]:
                return False
            if row:
                await conn.execute(
                    """
                    UPDATE cart_items SET quantity = quantity + ?
                    WHERE user_id = ? AND product_id = ?
                    """,
                    (quantity, user_id, product_id),
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO cart_items (user_id, product_id, quantity)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, product_id, quantity),
                )
            await conn.commit()
        return True

    async def get_cart(self, user_id: int) -> list[dict]:
        async with self.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT ci.id AS cart_item_id, ci.quantity,
                       p.id AS product_id, p.name, p.price, p.description, p.stock, p.is_available
                FROM cart_items ci
                JOIN products p ON p.id = ci.product_id
                WHERE ci.user_id = ?
                ORDER BY ci.id
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_cart_total(self, user_id: int) -> float:
        cart = await self.get_cart(user_id)
        return sum(item["price"] * item["quantity"] for item in cart)

    async def update_cart_quantity(
        self, user_id: int, product_id: int, quantity: int
    ) -> tuple[bool, str]:
        if type(quantity) is not int or quantity <= 0:
            return False, "Количество должно быть больше нуля."
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            cursor = await conn.execute(
                """
                SELECT ci.id, p.stock, p.is_available FROM cart_items ci
                JOIN products p ON p.id = ci.product_id
                WHERE ci.user_id = ? AND ci.product_id = ?
                """,
                (user_id, product_id),
            )
            item = await cursor.fetchone()
            if not item:
                return False, "Позиция не найдена в корзине."
            if not item["is_available"] or quantity > item["stock"]:
                return False, f"Недостаточно товара. Доступно: {item['stock']} шт."
            await conn.execute(
                """
                UPDATE cart_items SET quantity = ?
                WHERE user_id = ? AND product_id = ?
                """,
                (quantity, user_id, product_id),
            )
            await conn.commit()
        return True, ""

    async def remove_from_cart(self, user_id: int, product_id: int) -> bool:
        async with self.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM cart_items WHERE user_id = ? AND product_id = ?",
                (user_id, product_id),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def clear_cart(self, user_id: int) -> None:
        async with self.connection() as conn:
            await conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
            await conn.commit()

    async def create_checkout_token(self, user_id: int) -> str:
        import uuid

        token = str(uuid.uuid4())
        async with self.connection() as conn:
            await conn.execute(
                "INSERT INTO checkout_tokens (token, user_id) VALUES (?, ?)",
                (token, user_id),
            )
            await conn.commit()
        return token

    async def confirm_order(
        self,
        user_id: int,
        confirm_token: str,
        customer_name: str,
        pickup_method: str,
        payment_method: str = PAYMENT_METHOD,
    ) -> tuple[int | None, str]:
        """Атомарно переносит корзину в заказ. Защита от дубликатов по токену."""
        if payment_method not in {PAYMENT_METHOD, "yookassa_test"}:
            return None, "Неверный способ оплаты."
        async with self.connection() as conn:
            try:
                await conn.execute("BEGIN IMMEDIATE")

                cursor = await conn.execute(
                    """
                    SELECT token, order_id FROM checkout_tokens
                    WHERE token = ? AND user_id = ?
                    """,
                    (confirm_token, user_id),
                )
                token_row = await cursor.fetchone()

                if not token_row:
                    await conn.execute("ROLLBACK")
                    return None, "Сессия оформления не найдена. Начните заново."

                if token_row["order_id"]:
                    await conn.execute("ROLLBACK")
                    return token_row["order_id"], "already_confirmed"

                cursor = await conn.execute(
                    """
                    SELECT ci.product_id, ci.quantity, p.name, p.price, p.stock, p.is_available
                    FROM cart_items ci
                    JOIN products p ON p.id = ci.product_id
                    WHERE ci.user_id = ?
                    """,
                    (user_id,),
                )
                cart_items = await cursor.fetchall()

                if not cart_items:
                    await conn.execute("ROLLBACK")
                    return None, "Корзина пуста. Добавьте товары перед оформлением."

                for item in cart_items:
                    if not item["is_available"] or item["quantity"] > item["stock"]:
                        await conn.execute("ROLLBACK")
                        return None, f"Недостаточно товара «{item['name']}»: доступно {item['stock']} шт. Измените корзину."

                total = sum(r["price"] * r["quantity"] for r in cart_items)

                cursor = await conn.execute(
                    """
                    INSERT INTO orders
                        (user_id, customer_name, pickup_method, payment_method, total, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        customer_name,
                        pickup_method,
                        payment_method,
                        total,
                        ORDER_STATUS_NEW,
                    ),
                )
                order_id = cursor.lastrowid
                await conn.execute(
                    "UPDATE orders SET payment_status = ? WHERE id = ?",
                    ("pending" if payment_method == "yookassa_test" else "unpaid", order_id),
                )

                for item in cart_items:
                    await conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (item["quantity"], item["product_id"]))
                    await conn.execute(
                        """
                        INSERT INTO order_items
                            (order_id, product_name, product_price, quantity, product_id)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (order_id, item["name"], item["price"], item["quantity"], item["product_id"]),
                    )

                await conn.execute("UPDATE orders SET stock_deducted = 1 WHERE id = ?", (order_id,))

                await conn.execute(
                    "DELETE FROM cart_items WHERE user_id = ?", (user_id,)
                )
                await conn.execute(
                    """
                    UPDATE checkout_tokens SET order_id = ? WHERE token = ?
                    """,
                    (order_id, confirm_token),
                )
                await conn.execute("COMMIT")
                return order_id, "ok"

            except Exception:
                await conn.execute("ROLLBACK")
                raise

    async def get_user_orders(self, user_id: int) -> list[dict]:
        async with self.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM orders
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
            orders = [dict(r) for r in await cursor.fetchall()]
            for order in orders:
                c = await conn.execute(
                    "SELECT * FROM order_items WHERE order_id = ?",
                    (order["id"],),
                )
                order["items"] = [dict(i) for i in await c.fetchall()]
            return orders

    async def get_order(self, order_id: int, user_id: int | None = None) -> dict | None:
        async with self.connection() as conn:
            if user_id is not None:
                cursor = await conn.execute(
                    """
                    SELECT * FROM orders
                    WHERE id = ? AND user_id = ?
                    """,
                    (order_id, user_id),
                )
            else:
                cursor = await conn.execute(
                    "SELECT * FROM orders WHERE id = ?",
                    (order_id,),
                )
            row = await cursor.fetchone()
            if not row:
                return None
            order = dict(row)
            c = await conn.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
            )
            order["items"] = [dict(i) for i in await c.fetchall()]
            return order

    async def get_new_orders(self) -> list[dict]:
        async with self.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT o.*, u.username, u.first_name
                FROM orders o
                JOIN users u ON u.id = o.user_id
                WHERE o.status IN (?, 'ready')
                ORDER BY o.created_at ASC
                """,
                (ORDER_STATUS_NEW,),
            )
            orders = [dict(r) for r in await cursor.fetchall()]
            for order in orders:
                c = await conn.execute(
                    "SELECT * FROM order_items WHERE order_id = ?",
                    (order["id"],),
                )
                order["items"] = [dict(i) for i in await c.fetchall()]
            return orders

    async def update_order_status(self, order_id: int, status: str, user_id: int | None = None) -> bool:
        if status not in ORDER_STATUS_LABELS:
            return False
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            order = await (await conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,))).fetchone()
            if user_id is not None and (not order or order["user_id"] != user_id or status != "cancelled"):
                return False
            if not order or order["status"] == status or order["status"] in {"completed", "cancelled"}:
                return False
            if order["payment_method"] == "yookassa_test" and order["payment_status"] != "paid" and status != "cancelled":
                return False
            if status == "cancelled" and order["stock_deducted"]:
                items = await (await conn.execute("SELECT product_id, quantity FROM order_items WHERE order_id = ?", (order_id,))).fetchall()
                for item in items:
                    if item["product_id"] is not None:
                        await conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (item["quantity"], item["product_id"]))
                await conn.execute("UPDATE orders SET stock_deducted = 0 WHERE id = ?", (order_id,))
            await conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
            await conn.commit()
            return True

    async def get_stock_products(self) -> list[dict]:
        async with self.connection() as conn:
            return [dict(row) for row in await (await conn.execute("SELECT * FROM products ORDER BY id")).fetchall()]

    async def adjust_stock(self, product_id: int, delta: int) -> bool:
        if type(delta) is not int or delta == 0:
            return False
        async with self.connection() as conn:
            cursor = await conn.execute("UPDATE products SET stock = stock + ? WHERE id = ? AND stock + ? >= 0", (delta, product_id, delta))
            await conn.commit()
            return cursor.rowcount > 0


    @staticmethod
    def amount_kopecks(order: dict) -> int:
        return int((Decimal(str(order["total"])) * 100).quantize(Decimal("1")))

    async def validate_payment(self, order_id: int, user_id: int, currency: str, amount: int) -> bool:
        order = await self.get_order(order_id, user_id)
        return bool(order and order["payment_method"] == "yookassa_test"
                    and order["payment_status"] == "pending" and order["status"] == "new"
                    and currency == "RUB" and amount == self.amount_kopecks(order))

    async def record_payment(self, order_id: int, user_id: int, currency: str, amount: int,
                             telegram_charge: str, provider_charge: str) -> bool:
        if not telegram_charge or not provider_charge:
            return False
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            row = await (await conn.execute("SELECT * FROM orders WHERE id = ? AND user_id = ?",
                                           (order_id, user_id))).fetchone()
            if not row:
                return False
            order = dict(row)
            if order["payment_method"] != "yookassa_test" or currency != "RUB" or amount != self.amount_kopecks(order):
                return False
            if order["payment_status"] == "paid":
                return (order["telegram_payment_charge_id"] == telegram_charge
                        and order["provider_payment_charge_id"] == provider_charge)
            existing = await (await conn.execute("SELECT id FROM orders WHERE telegram_payment_charge_id = ?",
                                                (telegram_charge,))).fetchone()
            if existing:
                return False
            # Успешный платёж фиксируется даже если менеджер успел изменить статус заказа.
            await conn.execute("""UPDATE orders SET payment_status = 'paid',
                telegram_payment_charge_id = ?, provider_payment_charge_id = ?, paid_at = datetime('now')
                WHERE id = ?""", (telegram_charge, provider_charge, order_id))
            await conn.commit()
            return True
