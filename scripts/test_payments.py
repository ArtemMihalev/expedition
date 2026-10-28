"""Offline payment tests; no tokens, network or working database required."""
import asyncio
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.database.db import Database
from bot.handlers.payments import pre_checkout, send_order_invoice, successful_payment
from types import SimpleNamespace


class PaymentsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'test.db')
        self.db = Database(self.path)
        await self.db.init_db()
        for pid in range(1,7):
            await self.db.receive_stock(pid,10,999,f"fixture:{pid}")
        await self.db.upsert_user(101, None, 'Student')
        await self.db.upsert_user(202, None, 'Other')
        await self.db.add_to_cart(101, 1, 2)
        self.token = await self.db.create_checkout_token(101)
        self.oid, _ = await self.db.confirm_order(101, self.token, 'Student', 'store', 'yookassa_test')
        self.order = await self.db.get_order(self.oid, 101)
        self.amount = self.db.amount_kopecks(self.order)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_payment_validation_and_persistence(self):
        self.assertEqual(self.order['payment_status'], 'pending')
        self.assertTrue(await self.db.validate_payment(self.oid, 101, 'RUB', self.amount))
        for user, currency, amount in [(202, 'RUB', self.amount), (101, 'USD', self.amount), (101, 'RUB', 1)]:
            self.assertFalse(await self.db.validate_payment(self.oid, user, currency, amount))
        self.assertFalse(await self.db.record_payment(self.oid, 202, 'RUB', self.amount, 'tg', 'yk'))
        self.assertTrue(await self.db.record_payment(self.oid, 101, 'RUB', self.amount, 'tg', 'yk'))
        self.assertTrue(await self.db.record_payment(self.oid, 101, 'RUB', self.amount, 'tg', 'yk'))
        self.assertFalse(await self.db.record_payment(self.oid, 101, 'RUB', self.amount, 'other', 'other'))
        restarted = Database(self.path)
        await restarted.init_db()
        order = await restarted.get_order(self.oid, 101)
        self.assertEqual(order['payment_status'], 'paid')
        self.assertEqual(order['telegram_payment_charge_id'], 'tg')
        self.assertEqual(order['items'][0]['quantity'], 2)
        self.assertFalse(await restarted.validate_payment(self.oid, 101, 'RUB', self.amount))
        self.assertIsNone(await restarted.get_order(self.oid, 202))
        self.assertEqual(len(await restarted.get_user_orders(101)), 1)

    async def test_cash_duplicate_empty_and_manager(self):
        self.assertFalse(await self.db.update_order_status(self.oid, 'ready'))
        duplicate, status = await self.db.confirm_order(101, self.token, 'Student', 'store', 'yookassa_test')
        self.assertEqual((duplicate, status), (self.oid, 'already_confirmed'))
        token = await self.db.create_checkout_token(202)
        oid, _ = await self.db.confirm_order(202, token, 'Other', 'store')
        self.assertIsNone(oid)
        await self.db.add_to_cart(202, 2)
        oid, _ = await self.db.confirm_order(202, token, 'Other', 'store')
        order = await self.db.get_order(oid, 202)
        self.assertEqual(order['payment_method'], 'cash_on_pickup')
        self.assertEqual(order['payment_status'], 'unpaid')
        self.assertTrue(await self.db.update_order_status(oid, 'ready'))
        self.assertFalse(await self.db.record_payment(oid, 202, 'RUB', self.db.amount_kopecks(order), 'tg', 'yk'))

    async def test_telegram_handlers(self):
        message = SimpleNamespace(bot=SimpleNamespace(send_invoice=AsyncMock()), answer=AsyncMock(), from_user=SimpleNamespace(id=101))
        with patch('bot.handlers.payments.YOOKASSA_PROVIDER_TOKEN', 'offline:TEST:example'):
            await send_order_invoice(message, self.order)
        invoice = message.bot.send_invoice.call_args.kwargs
        self.assertEqual(invoice['prices'][0].amount, self.amount)
        self.assertEqual(invoice['currency'], 'RUB')
        query = SimpleNamespace(invoice_payload=invoice['payload'], from_user=message.from_user, currency='RUB', total_amount=self.amount, answer=AsyncMock())
        await pre_checkout(query, self.db)
        query.answer.assert_awaited_with(ok=True)
        self.assertEqual((await self.db.get_order(self.oid))['payment_status'], 'pending')
        message.successful_payment = SimpleNamespace(invoice_payload=invoice['payload'], currency='RUB', total_amount=self.amount, telegram_payment_charge_id='telegram-test', provider_payment_charge_id='provider-test')
        await successful_payment(message, self.db)
        await successful_payment(message, self.db)
        await pre_checkout(query, self.db)
        self.assertFalse(query.answer.call_args.kwargs['ok'])
        self.assertEqual((await self.db.get_order(self.oid))['payment_status'], 'paid')


if __name__ == '__main__':
    unittest.main()
