"""Inventory transactions and Telegram UI tests using a temporary SQLite file."""
import asyncio
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from bot.database.db import Database, PRODUCT_IMAGES
from bot.handlers.manager import manager_change_status, manager_stock_adjust
from bot.ui import show_text, show_product_card, PROJECT_ROOT
from bot.keyboards.inline import keyboards
from bot.handlers.orders import cancel_order_confirm


class InventoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "shop.db")
        self.db = Database(self.path)
        await self.db.init_db()
        for pid in range(1,7):
            await self.db.receive_stock(pid,10,999,f"fixture:{pid}")
        for user in (101, 202):
            await self.db.upsert_user(user, None, "Student")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def order(self, user=101, quantity=1, product=1, method="cash_on_pickup"):
        self.assertTrue(await self.db.add_to_cart(user, product, quantity))
        token = await self.db.create_checkout_token(user)
        oid, result = await self.db.confirm_order(user, token, "Student", "store", method)
        self.assertEqual(result, "ok")
        return oid, token

    async def test_reserve_cancel_and_restart(self):
        oid, token = await self.order(quantity=3)
        self.assertEqual((await self.db.get_product(1))["stock"], 7)
        await self.db.confirm_order(101, token, "Student", "store")
        self.assertEqual((await self.db.get_product(1))["stock"], 7)
        restarted = Database(self.path)
        await restarted.init_db()
        self.assertEqual((await restarted.get_product(1))["stock"], 7)
        self.assertTrue(await restarted.update_order_status(oid, "cancelled"))
        self.assertEqual((await restarted.get_product(1))["stock"], 10)
        self.assertFalse(await restarted.update_order_status(oid, "cancelled"))
        self.assertFalse(await restarted.update_order_status(oid, "ready"))
        self.assertEqual((await restarted.get_product(1))["stock"], 10)

    async def test_last_item_concurrent_checkouts(self):
        self.assertTrue(await self.db.adjust_stock(1, -9))
        tokens = []
        for user in (101, 202):
            self.assertTrue(await self.db.add_to_cart(user, 1))
            tokens.append(await self.db.create_checkout_token(user))
        results = await asyncio.gather(*(self.db.confirm_order(u, t, "Student", "store") for u, t in zip((101, 202), tokens)))
        self.assertEqual(sum(oid is not None for oid, _ in results), 1)
        self.assertEqual((await self.db.get_product(1))["stock"], 0)
        self.assertFalse(await self.db.add_to_cart(101, 1))
        loser = 101 if results[0][0] is None else 202
        self.assertEqual(len(await self.db.get_cart(loser)), 1)

    async def test_cart_limits_and_unavailable_product(self):
        self.assertFalse(await self.db.add_to_cart(101, 1, 0))
        self.assertFalse(await self.db.add_to_cart(101, 1, -1))
        self.assertFalse(await self.db.add_to_cart(101, 1, 11))
        await self.db.add_to_cart(101, 1, 2)
        self.assertFalse((await self.db.update_cart_quantity(101, 1, 11))[0])
        async with self.db.connection() as conn:
            await conn.execute("UPDATE products SET is_available = 0 WHERE id = 1")
            await conn.commit()
        token = await self.db.create_checkout_token(101)
        self.assertIsNone((await self.db.confirm_order(101, token, "Student", "store"))[0])
        self.assertEqual(await self.db.get_user_orders(101), [])
        self.assertEqual(len(await self.db.get_cart(101)), 1)

    async def test_failed_transaction_rolls_back_stock_and_order(self):
        await self.db.add_to_cart(101, 1, 2)
        token = await self.db.create_checkout_token(101)
        async with self.db.connection() as conn:
            await conn.execute("CREATE TRIGGER fail_items BEFORE INSERT ON order_items BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            await conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.confirm_order(101, token, "Student", "store")
        self.assertEqual((await self.db.get_product(1))["stock"], 10)
        self.assertEqual(await self.db.get_user_orders(101), [])
        self.assertEqual((await self.db.get_cart(101))[0]["quantity"], 2)

    async def test_online_payment_keeps_reservation(self):
        oid, _ = await self.order(quantity=2, method="yookassa_test")
        order = await self.db.get_order(oid, 101)
        self.assertTrue(await self.db.record_payment(oid, 101, "RUB", self.db.amount_kopecks(order), "tg", "yk"))
        self.assertEqual((await self.db.get_product(1))["stock"], 8)
        self.assertTrue(await self.db.update_order_status(oid, "ready"))
        self.assertEqual(len(await self.db.get_new_orders()), 1)
        self.assertTrue(await self.db.update_order_status(oid, "completed"))
        self.assertEqual((await self.db.get_product(1))["stock"], 8)

    def callback(self, oid, user=999, status="ready"):
        return SimpleNamespace(data=f"mgr_status:{oid}:{status}", from_user=SimpleNamespace(id=user),
            bot=SimpleNamespace(send_message=AsyncMock()), answer=AsyncMock(),
            message=SimpleNamespace(photo=None, edit_text=AsyncMock()))

    async def test_manager_notification_and_permissions(self):
        oid, _ = await self.order()
        callback = self.callback(oid)
        with patch("bot.handlers.manager.MANAGER_IDS", {999}):
            await manager_change_status(callback, self.db)
            await manager_change_status(callback, self.db)
        callback.bot.send_message.assert_awaited_once()
        self.assertEqual(callback.bot.send_message.call_args.args[0], 101)
        self.assertIn("Готов к выдаче", callback.bot.send_message.call_args.args[1])
        callback = self.callback(oid, user=202, status="cancelled")
        with patch("bot.handlers.manager.MANAGER_IDS", {999}):
            await manager_change_status(callback, self.db)
            callback.data = "stock_adjust:1:10"
            await manager_stock_adjust(callback, self.db)
        callback.bot.send_message.assert_not_awaited()
        self.assertEqual((await self.db.get_product(1))["stock"], 9)
        self.assertEqual((await self.db.get_order(oid))["status"], "ready")

    async def test_blocked_bot_does_not_undo_status(self):
        oid, _ = await self.order()
        callback = self.callback(oid)
        callback.bot.send_message.side_effect = TelegramForbiddenError(method=SendMessage(chat_id=101, text="test"), message="blocked")
        with patch("bot.handlers.manager.MANAGER_IDS", {999}):
            await manager_change_status(callback, self.db)
        self.assertEqual((await self.db.get_order(oid))["status"], "ready")
        self.assertIn("не доставлено", callback.answer.call_args.args[0])

    async def test_photo_back_navigation(self):
        message = SimpleNamespace(photo=[object()], edit_reply_markup=AsyncMock(), answer=AsyncMock())
        await show_text(message, "Корзина", reply_markup=None)
        message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        message.answer.assert_awaited_once_with("Корзина", reply_markup=None)

    async def test_stock_adjustment_and_image_paths(self):
        self.assertFalse(await self.db.adjust_stock(1, -11))
        self.assertTrue(await self.db.adjust_stock(1, 5))
        self.assertEqual((await self.db.get_product(1))["stock"], 15)
        self.assertEqual(len(PRODUCT_IMAGES), 6)
        self.assertEqual((await self.db.get_product(1))["image_path"], "assets/products/trekpro.png")

    async def test_product_images_and_out_of_stock_card(self):
        for relative_path in PRODUCT_IMAGES.values():
            path = PROJECT_ROOT / relative_path
            self.assertTrue(path.is_file(), str(path))
            self.assertLess(path.stat().st_size, 10 * 1024 * 1024)
            self.assertEqual(path.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        markup = keyboards.product_card(1, stock=0)
        self.assertFalse(any(b.callback_data.startswith('add:') for row in markup.inline_keyboard for b in row))
        message = SimpleNamespace(photo=None, edit_reply_markup=AsyncMock(), answer_photo=AsyncMock())
        product = await self.db.get_product(1)
        await show_product_card(message, product, "Product", markup)
        message.answer_photo.assert_awaited_once()
        self.assertEqual(message.answer_photo.call_args.kwargs['caption'], "Product")

    async def test_old_database_migration_preserves_order(self):
        legacy_path = str(Path(self.tmp.name) / 'legacy.db')
        with sqlite3.connect(legacy_path) as conn:
            conn.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY, platform TEXT DEFAULT 'telegram', username TEXT, first_name TEXT, created_at TEXT);
                CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT, description TEXT, price REAL, category TEXT, is_available INTEGER DEFAULT 1);
                CREATE TABLE orders(id INTEGER PRIMARY KEY, user_id INTEGER, customer_name TEXT, pickup_method TEXT, payment_method TEXT, total REAL, status TEXT, created_at TEXT);
                CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INTEGER, product_name TEXT, product_price REAL, quantity INTEGER);
                INSERT INTO users VALUES(101, 'telegram', NULL, 'Student', '2026-10-01');
                INSERT INTO products VALUES(1, 'Legacy', 'Description', 500, 'backpacks', 1);
                INSERT INTO orders VALUES(1, 101, 'Student', 'store', 'cash_on_pickup', 1000, 'new', '2026-10-01');
                INSERT INTO order_items VALUES(1, 1, 'Legacy', 500, 2);
            """)
        conn.close()
        migrated = Database(legacy_path)
        await migrated.init_db()
        await migrated.receive_stock(1,10,999,'legacy-receipt')
        self.assertEqual((await migrated.get_order(1, 101))['items'][0]['product_price'], 500)
        self.assertTrue(await migrated.update_order_status(1, 'cancelled'))
        self.assertEqual((await migrated.get_product(1))['stock'], 10)
        await migrated.adjust_stock(1, -4)
        await migrated.init_db()
        self.assertEqual((await migrated.get_product(1))['stock'], 6)

    async def test_customer_cancellation_ownership_and_duplicates(self):
        oid, _ = await self.order(quantity=2)
        self.assertFalse(await self.db.update_order_status(oid, 'cancelled', user_id=202))
        self.assertFalse(await self.db.update_order_status(oid, 'ready', user_id=101))
        self.assertEqual((await self.db.get_product(1))['stock'], 8)
        results = await asyncio.gather(
            self.db.update_order_status(oid, 'cancelled', user_id=101),
            self.db.update_order_status(oid, 'cancelled', user_id=101),
        )
        self.assertEqual(sum(results), 1)
        self.assertEqual((await self.db.get_product(1))['stock'], 10)
        self.assertEqual((await self.db.get_order(oid, 101))['status'], 'cancelled')

    async def test_customer_cancellation_paid_ready_and_completed(self):
        oid, _ = await self.order(method='yookassa_test')
        order = await self.db.get_order(oid, 101)
        await self.db.record_payment(oid, 101, 'RUB', self.db.amount_kopecks(order), 'tg', 'yk')
        await self.db.update_order_status(oid, 'ready')
        self.assertFalse(await self.db.update_order_status(oid, 'cancelled', user_id=101))
        self.assertEqual((await self.db.get_order(oid, 101))['payment_status'], 'paid')
        self.assertEqual((await self.db.get_product(1))['stock'], 9)
        refund=await self.db.start_refund(oid,999)
        await self.db.complete_refund(refund['id'],'test-sale-return',refund['amount_kopecks'],'RUB')
        self.assertEqual((await self.db.get_product(1))['stock'],10)
        oid, _ = await self.order()
        await self.db.update_order_status(oid, 'completed')
        self.assertFalse(await self.db.update_order_status(oid, 'cancelled', user_id=101))
        self.assertEqual((await self.db.get_product(1))['stock'], 9)

    async def test_customer_cancel_handler(self):
        oid, _ = await self.order()
        callback = self.callback(oid, user=202)
        callback.data = f'cancel_confirm:{oid}'
        await cancel_order_confirm(callback, self.db)
        callback.message.edit_text.assert_not_awaited()
        callback.from_user.id = 101
        await cancel_order_confirm(callback, self.db)
        self.assertIn('Отменён', callback.message.edit_text.call_args.args[0])
        await cancel_order_confirm(callback, self.db)
        self.assertEqual((await self.db.get_product(1))['stock'], 10)


if __name__ == "__main__":
    unittest.main()
