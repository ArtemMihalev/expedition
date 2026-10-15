"""End-to-end financial scenarios against temporary SQLite and real HTTP handlers."""
import asyncio
import hashlib
import hmac
import json
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.database.db import Database
from bot.web.auth import validate_init_data
from bot.web.server import create_app
from bot.refunds import YooKassaRefunds

TOKEN = '123456:local-test-only'


def init_data(user_id=999, timestamp=None, token=TOKEN):
    data = {'auth_date': str(int(time.time()) if timestamp is None else timestamp), 'query_id': 'test', 'user': json.dumps({'id': user_id, 'first_name': 'Student'}, separators=(',', ':'))}
    key = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    data['hash'] = hmac.new(key, '\n'.join(f'{k}={v}' for k, v in sorted(data.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


class AuthTests(unittest.TestCase):
    def test_valid_and_invalid_signatures(self):
        self.assertEqual(validate_init_data(init_data(), TOKEN, {999}), 999)
        for raw in ('', init_data(101), init_data(timestamp=1), init_data(timestamp=int(time.time())+1000), init_data(token='wrong'), init_data()+'&user={}'):
            with self.assertRaises(PermissionError):
                validate_init_data(raw, TOKEN, {999})


class FinanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'finance.db')
        self.db = Database(self.path)
        await self.db.init_db()
        await self.db.upsert_user(101, None, 'Buyer A')
        await self.db.upsert_user(202, None, 'Buyer B')
        async with self.db.connection() as conn:
            await conn.execute('UPDATE products SET price=1200,price_kopecks=120000,cost_kopecks=70000 WHERE id=1')
            await conn.commit()
        self.client = TestClient(TestServer(create_app(self.db, bot_token=TOKEN, managers={999})))
        await self.client.start_server()
        self.headers = {'Authorization': 'tma '+init_data()}

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def make_order(self, method='demo'):
        await self.db.add_to_cart(101, 1)
        token = await self.db.create_checkout_token(101)
        oid, result = await self.db.confirm_order(101, token, 'Buyer A', 'store', method)
        self.assertEqual(result, 'ok')
        return oid

    async def totals(self):
        return await self.db.summary('2020-01-01', '2099-12-31')

    def expense_data(self, amount='100.00'):
        return dict(amount=amount, category='delivery', date='2026-10-09', description='Учебная доставка', request_id=str(uuid.uuid4()))

    async def test_full_demo_financial_path_and_restart(self):
        oid = await self.make_order()
        self.assertEqual((await self.totals())['receipts'], 0)
        with patch('bot.database.finance.MANAGER_IDS', {999}):
            await self.db.demo_payment(oid, 101, 'succeeded')
        totals = await self.totals()
        self.assertEqual((totals['receipts'], totals['cost_paid'], totals['cash_flow'], totals['result']), (120000, 70000, 120000, 50000))
        with self.assertRaises(ValueError):
            await self.db.demo_payment(oid, 101, 'succeeded')
        async with self.db.connection() as conn:
            self.assertEqual((await (await conn.execute('SELECT COUNT(*) FROM finance_notifications')).fetchone())[0], 1)
        data = self.expense_data()
        expense_id = await self.db.save_expense(data, 999)
        self.assertEqual(await self.db.save_expense(data, 999), expense_id)
        totals = await self.totals()
        self.assertEqual((totals['cash_flow'], totals['result']), (110000, 40000))
        refund = await self.db.start_refund(oid, 999)
        self.assertEqual((await self.totals())['refunds'], 0)
        self.assertTrue(await self.db.complete_refund(refund['id'], 'demo-refund:'+refund['id'], 120000, 'RUB'))
        self.assertFalse(await self.db.complete_refund(refund['id'], 'demo-refund:'+refund['id'], 120000, 'RUB'))
        totals = await self.totals()
        self.assertEqual((totals['refunds'], totals['cost_returned'], totals['cash_flow'], totals['result']), (120000,70000,-10000,-10000))
        self.assertEqual((await self.db.get_product(1))['stock'], 10)
        restarted = Database(self.path)
        await restarted.init_db()
        self.assertEqual(await restarted.summary('2020-01-01','2099-12-31'),totals)
        self.assertIsNone(await restarted.get_order(oid, 202))

    async def test_real_payment_duplicate_tampering_and_snapshot(self):
        oid = await self.make_order('yookassa_test')
        self.assertFalse(await self.db.record_payment(oid,101,'RUB',1,'tg','yk'))
        self.assertFalse(await self.db.record_payment(oid,101,'USD',120000,'tg','yk'))
        self.assertFalse(await self.db.record_payment(oid,202,'RUB',120000,'tg','yk'))
        self.assertEqual((await self.totals())['receipts'],0)
        async with self.db.connection() as conn:
            await conn.execute('UPDATE products SET price_kopecks=999999,cost_kopecks=1 WHERE id=1')
            await conn.commit()
        results = await asyncio.gather(*(self.db.record_payment(oid,101,'RUB',120000,'tg','yk') for _ in range(2)))
        self.assertTrue(all(results))
        summary = await self.totals()
        self.assertEqual((summary['receipts'],summary['cost_paid']), (120000,70000))
        ops = await self.db.operations('2020-01-01','2099-12-31')
        self.assertEqual(len(ops),1)

    async def test_canceled_payment_and_refund_before_payment(self):
        oid=await self.make_order()
        with self.assertRaises(ValueError):
            await self.db.start_refund(oid,999)
        with self.assertRaises(ValueError):
            await self.db.demo_payment(oid,202,'succeeded')
        await self.db.demo_payment(oid,101,'canceled')
        with self.assertRaises(ValueError):
            await self.db.demo_payment(oid,101,'succeeded')
        self.assertEqual((await self.totals())['receipts'],0)

    async def test_expense_validation_correction_and_audit(self):
        for amount in ('0','-1','0.001','nan','Infinity','bad'):
            with self.assertRaises(ValueError):
                await self.db.save_expense(self.expense_data(amount),999)
        data=self.expense_data()
        oid=await self.db.save_expense(data,999)
        changed=self.expense_data('200.25')
        replacement=await self.db.save_expense(changed,999,oid)
        self.assertEqual(await self.db.save_expense(changed,999,oid),replacement)
        self.assertEqual((await self.totals())['expenses'],20025)
        ops=await self.db.operations('2020-01-01','2099-12-31')
        self.assertEqual(len(ops),2)
        self.assertTrue(next(op for op in ops if op['id']==oid)['cancelled_at'])
        self.assertEqual(len(next(op for op in ops if op['id']==oid)['history']),2)
        await self.db.cancel_expense(replacement,999)
        self.assertEqual((await self.totals())['expenses'],0)
        with self.assertRaises(ValueError):
            await self.db.cancel_expense(replacement,999)

    async def test_api_denies_all_financial_routes_without_role(self):
        routes=[('GET','summary'),('GET','orders'),('GET','orders/1'),('GET','operations'),('GET','meta'),('POST','expenses'),('POST','expenses/1/correct'),('POST','expenses/1/cancel'),('POST','orders/1/refund'),('POST','refunds/test/check'),('POST','refunds/test/demo')]
        for headers in ({},{'Authorization':'tma '+init_data(101)},{'Authorization':'tma '+init_data(timestamp=1)},{'Authorization':'tma invalid'}):
            for method,route in routes:
                response=await self.client.request(method,'/api/'+route,headers=headers,json={'user_id':999})
                self.assertEqual(response.status,403,route)
        self.assertEqual((await self.totals())['expenses'],0)

    async def test_api_full_path_and_refund_ignores_browser_amount(self):
        oid=await self.make_order()
        await self.db.demo_payment(oid,101,'succeeded')
        response=await self.client.post('/api/expenses',headers=self.headers,json=self.expense_data())
        self.assertEqual(response.status,200,await response.text())
        response=await self.client.post(f'/api/orders/{oid}/refund',headers=self.headers,json={'amount':1,'currency':'USD'})
        self.assertEqual(response.status,200,await response.text())
        refund=await response.json()
        self.assertEqual(refund['amount_kopecks'],120000)
        response=await self.client.post('/api/refunds/'+refund['id']+'/demo',headers=self.headers,json={'outcome':'succeeded','amount':1})
        self.assertEqual(response.status,200,await response.text())
        self.assertEqual((await self.totals())['refunds'],120000)
        response=await self.client.get('/api/orders/'+str(oid),headers=self.headers)
        self.assertEqual(response.status,200)
        self.assertEqual((await response.json())['payment_status'],'refunded')

    async def test_api_expense_bad_body_and_amount(self):
        for data in (self.expense_data('0'),self.expense_data('-100'),self.expense_data('1.111'),[],{'amount':100,'user_id':999}):
            response=await self.client.post('/api/expenses',headers=self.headers,json=data)
            self.assertEqual(response.status,400)
        self.assertEqual((await self.totals())['expenses'],0)

    async def test_refund_from_previous_period(self):
        oid=await self.make_order()
        with patch('bot.database.finance.now',return_value='2026-09-30 12:00:00'):
            await self.db.demo_payment(oid,101,'succeeded')
        refund=await self.db.start_refund(oid,999)
        await self.db.complete_refund(refund['id'],'refund-cross-month',120000,'RUB','2026-10-09 12:00:00')
        september=await self.db.summary('2026-09-01','2026-09-30')
        october=await self.db.summary('2026-10-01','2026-10-31')
        self.assertEqual(september['result'],50000)
        self.assertEqual(october['cash_flow'],-120000)
        self.assertEqual(october['result'],-50000)

    async def test_refund_provider_pending_then_success(self):
        oid=await self.make_order('yookassa_test')
        await self.db.record_payment(oid,101,'RUB',120000,'tg','yk')
        refund=await self.db.start_refund(oid,999)
        service=YooKassaRefunds('test-shop','test_local')
        payment={'id':'yk','status':'succeeded','test':True,'amount':{'value':'1200.00','currency':'RUB'}}
        external={'id':'external-refund','payment_id':'yk','status':'pending','amount':payment['amount'],'created_at':'2026-10-09T12:00:00Z'}
        service.request=AsyncMock(side_effect=[payment,{'id':'external-refund'},external])
        result=await service.process(self.db,refund['id'])
        self.assertEqual(result['status'],'refund_pending')
        self.assertEqual((await self.totals())['refunds'],0)
        external['status']='succeeded'
        service.request=AsyncMock(side_effect=[payment,external])
        await service.process(self.db,refund['id'])
        self.assertEqual((await self.totals())['refunds'],120000)
        self.assertEqual((await self.db.get_product(1))['stock'],10)

    async def test_refund_network_error_does_not_change_finances(self):
        oid=await self.make_order('yookassa_test')
        await self.db.record_payment(oid,101,'RUB',120000,'tg','yk')
        refund=await self.db.start_refund(oid,999)
        service=YooKassaRefunds('test-shop','test_local')
        service.request=AsyncMock(side_effect=ValueError('Нет связи с провайдером'))
        before=await self.totals()
        with self.assertRaises(ValueError):
            await service.process(self.db,refund['id'])
        self.assertEqual(await self.totals(),before)
        self.assertEqual((await self.db.refund_detail(refund['id']))['status'],'refund_pending')

    async def test_refund_provider_mismatch_rejected(self):
        oid=await self.make_order('yookassa_test')
        await self.db.record_payment(oid,101,'RUB',120000,'tg','yk')
        refund=await self.db.start_refund(oid,999)
        service=YooKassaRefunds('test-shop','test_local')
        service.request=AsyncMock(return_value={'id':'yk','status':'succeeded','test':False,'amount':{'value':'1200.00','currency':'RUB'}})
        with self.assertRaises(ValueError):
            await service.process(self.db,refund['id'])
        self.assertEqual((await self.totals())['refunds'],0)

    async def test_financial_transaction_failure_rolls_back_payment(self):
        oid=await self.make_order('yookassa_test')
        async with self.db.connection() as conn:
            await conn.execute("CREATE TRIGGER fail_finance BEFORE INSERT ON finance_operations BEGIN SELECT RAISE(ABORT,'test'); END")
            await conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.record_payment(oid,101,'RUB',120000,'tg','yk')
        order=await self.db.finance_order(oid)
        self.assertEqual(order['payment_status'],'pending')
        self.assertEqual(order['payments'][0]['status'],'pending')
        self.assertEqual((await self.totals())['receipts'],0)

    async def test_notification_delivery_is_persistent_and_not_repeated(self):
        from bot.handlers.finance import notification_worker
        oid = await self.make_order('yookassa_test')
        with patch('bot.database.finance.MANAGER_IDS', {999}):
            await self.db.record_payment(oid,101,'RUB',120000,'tg-notify','yk-notify')
            await self.db.record_payment(oid,101,'RUB',120000,'tg-notify','yk-notify')
        bot = SimpleNamespace(send_message=AsyncMock())
        # One full iteration, then simulate process shutdown at the sleep boundary.
        with patch('bot.handlers.finance.MANAGER_IDS', {999}), patch('bot.handlers.finance.asyncio.sleep', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await notification_worker(bot, Database(self.path))
            with self.assertRaises(asyncio.CancelledError):
                await notification_worker(bot, Database(self.path))
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.call_args.args[0],999)
        self.assertIn(str(oid), bot.send_message.call_args.args[1])
        async with self.db.connection() as conn:
            rows=await (await conn.execute('SELECT sent_at FROM finance_notifications')).fetchall()
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['sent_at'])

    async def test_summary_command_checks_role_and_reads_database(self):
        from bot.handlers.finance import summary_command
        message=SimpleNamespace(from_user=SimpleNamespace(id=101),text='/summary',answer=AsyncMock())
        with patch('bot.handlers.finance.MANAGER_IDS',{999}):
            await summary_command(message,self.db)
            self.assertIn('только менеджеру',message.answer.call_args.args[0])
            oid=await self.make_order()
            await self.db.demo_payment(oid,101,'succeeded')
            message.from_user.id=999
            message.text='/summary 2020-01-01 2099-12-31'
            await summary_command(message,self.db)
        output=message.answer.call_args.args[0]
        self.assertIn('1 200.00',output)
        self.assertIn('500.00',output)

    async def test_legacy_unknown_cost_is_not_invented_on_restart(self):
        oid=await self.make_order('yookassa_test')
        async with self.db.connection() as conn:
            await conn.execute('UPDATE order_items SET cost_kopecks=NULL WHERE order_id=?',(oid,))
            await conn.execute("UPDATE orders SET payment_status='paid',paid_at='2026-10-09 12:00:00',telegram_payment_charge_id='old-tg',provider_payment_charge_id='old-yk' WHERE id=?",(oid,))
            await conn.commit()
        restarted=Database(self.path)
        await restarted.init_db()
        await restarted.init_db()
        totals=await restarted.summary('2020-01-01','2099-12-31')
        self.assertEqual(totals['receipts'],120000)
        self.assertEqual(totals['cash_flow'],120000)
        self.assertIsNone(totals['result'])
        self.assertEqual(totals['unknown_cost_operations'],1)


if __name__ == '__main__':
    unittest.main()
