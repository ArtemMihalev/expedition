"""Migration, constraint, contention and failed sale-refund regression checks."""
import sqlite3
import tempfile
import unittest
import sys
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from bot.database.db import Database
from bot.inventory_worker import process_refund,set_demo_fault
from bot.refunds import YooKassaRefunds


class WarehouseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.tmp.name)/'test.db')
        self.db=Database(self.path,training=True)
        await self.db.init_db()
        await self.db.receive_stock(1,10,999,'initial')
        await self.db.upsert_user(101,None,'Тест')

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def order(self,method='demo',key='purchase'):
        return (await self.db.place_order(101,key,[{'product_id':1,'quantity':2}],'Тест','store',method))[0]

    async def test_database_constraint_and_bounded_busy(self):
        for sql in ['UPDATE products SET reserved=11 WHERE id=1','UPDATE products SET stock=-1 WHERE id=1','UPDATE products SET reserved=0.5 WHERE id=1']:
            with self.assertRaises(sqlite3.IntegrityError):
                async with self.db.connection() as conn:
                    await conn.execute(sql)
        async with self.db.connection() as locked:
            await locked.execute('BEGIN IMMEDIATE')
            async with self.db.connection() as contender:
                await contender.execute('PRAGMA busy_timeout=1')
                with self.assertRaisesRegex(ValueError,'тем же ключом'):
                    await self.db.begin(contender)

    async def test_run_entrypoint_starts_shared_http_server(self):
        import importlib
        with patch('bot.config.BOT_TOKEN','123456:local-test-only'):
            main=importlib.import_module('bot.main')
        fake_bot=SimpleNamespace(session=SimpleNamespace(close=AsyncMock()))
        fake_dispatcher=SimpleNamespace(update=SimpleNamespace(middleware=Mock()),include_router=Mock(),start_polling=AsyncMock())
        with patch.object(main,'BOT_TOKEN','123456:local-test-only'),patch.object(main,'Database',return_value=self.db),patch.object(main,'Bot',return_value=fake_bot),patch.object(main,'Dispatcher',return_value=fake_dispatcher),patch.object(main,'DATABASE_PATH',self.path),patch.object(main,'WEB_HOST','127.0.0.1'),patch.object(main,'WEB_PORT',0),patch.object(main,'notification_worker',new=AsyncMock()):
            await main.main()
        fake_dispatcher.start_polling.assert_awaited_once()
        fake_bot.session.close.assert_awaited_once()

    async def test_pending_sale_refund_error_does_not_restock(self):
        oid=await self.order()
        await self.db.demo_payment(oid,101,'succeeded')
        refund=await self.db.start_refund(oid,999)
        await set_demo_fault(self.db,refund['id'],'temporary')
        with self.assertRaises(ValueError):
            await process_refund(self.db,refund['id'],YooKassaRefunds())
        p=await self.db.get_product(1)
        self.assertEqual((p['on_hand'],p['reserved']),(8,0))
        pending=await self.db.summary('2020-01-01','2099-12-31')
        self.assertEqual(pending['refunds'],0)
        self.assertEqual(pending['cost_returned'],0)
        await process_refund(self.db,refund['id'],YooKassaRefunds())
        await process_refund(self.db,refund['id'],YooKassaRefunds())
        self.assertEqual((await self.db.get_product(1))['on_hand'],10)

    async def test_paid_cancellation_never_releases_consumed_inventory(self):
        oid=await self.order()
        await self.db.demo_payment(oid,101,'succeeded')
        self.assertFalse(await self.db.update_order_status(oid,'cancelled',user_id=101))
        self.assertFalse(await self.db.update_order_status(oid,'cancelled'))
        p=await self.db.get_product(1)
        self.assertEqual((p['on_hand'],p['reserved']),(8,0))

    async def test_yookassa_lost_response_queries_provider_without_second_post(self):
        oid=await self.order('yookassa_test')
        order=await self.db.get_order(oid)
        amount=order['total_kopecks']
        await self.db.record_payment(oid,101,'RUB',amount,'telegram','payment')
        refund=await self.db.start_refund(oid,999)
        service=YooKassaRefunds('shop','test_key')
        payment={'id':'payment','test':True,'status':'succeeded','amount':{'currency':'RUB','value':str(amount/100)}}
        external={'id':'refund-provider','payment_id':'payment','status':'succeeded','amount':payment['amount'],'description':f'Учебный полный возврат заказа {oid}'}
        service.request=AsyncMock(side_effect=[payment,ValueError('Ответ потерян')])
        with self.assertRaises(ValueError):
            await process_refund(self.db,refund['id'],service)
        key=(await self.db.refund_detail(refund['id']))['attempt_key']
        service.request=AsyncMock(side_effect=[payment,{'items':[external]},external])
        await process_refund(self.db,refund['id'],service)
        self.assertTrue(all(c.args[0]=='GET' for c in service.request.call_args_list))
        self.assertEqual((await self.db.refund_detail(refund['id']))['attempt_key'],key)
        self.assertEqual((await self.db.get_product(1))['on_hand'],10)

    async def test_stage2_migration_keeps_paid_stock_and_snapshots(self):
        # Construct the exact pre-reservation schema, with paid and unpaid deductions.
        path=str(Path(self.tmp.name)/'legacy-stage2.db')
        with sqlite3.connect(path) as conn:
            conn.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY,platform TEXT DEFAULT 'telegram',username TEXT,first_name TEXT,created_at TEXT);
                CREATE TABLE products(id INTEGER PRIMARY KEY,name TEXT,description TEXT,price REAL,category TEXT,is_available INTEGER DEFAULT 1,stock INTEGER NOT NULL DEFAULT 10 CHECK(stock>=0));
                CREATE TABLE orders(id INTEGER PRIMARY KEY,user_id INTEGER,customer_name TEXT,pickup_method TEXT,payment_method TEXT,total REAL,status TEXT,created_at TEXT,payment_status TEXT,stock_deducted INTEGER,paid_at TEXT,telegram_payment_charge_id TEXT,provider_payment_charge_id TEXT);
                CREATE TABLE order_items(id INTEGER PRIMARY KEY,order_id INTEGER,product_name TEXT,product_price REAL,quantity INTEGER,product_id INTEGER,price_kopecks INTEGER,cost_kopecks INTEGER);
                INSERT INTO users VALUES(101,'telegram',NULL,'Тест','2026-10-01');
                INSERT INTO products VALUES(1,'Legacy','Legacy',1200,'accessories',1,6);
                INSERT INTO orders VALUES(1,101,'Тест','store','yookassa_test',2400,'new','2026-10-01','paid',1,'2026-10-01 12:00:00','old-tg','old-provider');
                INSERT INTO orders VALUES(2,101,'Тест','store','demo',2400,'new','2026-10-01','pending',1,NULL,NULL,NULL);
                INSERT INTO order_items VALUES(1,1,'Snapshot',1200,2,1,120000,70000);
                INSERT INTO order_items VALUES(2,2,'Snapshot',1200,2,1,120000,70000);
            """)
        conn.close()
        ids=[1,2]
        upgraded=Database(path)
        await upgraded.init_db()
        await upgraded.init_db()
        p=await upgraded.get_product(1)
        self.assertEqual((p['on_hand'],p['reserved'],p['available']),(8,2,6))
        item=(await upgraded.get_order(ids[0]))['items'][0]
        self.assertEqual((item['product_name'],item['price_kopecks'],item['cost_kopecks']),('Snapshot',120000,70000))
        async with upgraded.connection() as conn:
            self.assertIsNone(await (await conn.execute('SELECT * FROM reservations WHERE order_id=?',(ids[0],))).fetchone())
            self.assertEqual((await (await conn.execute("SELECT COUNT(*) FROM finance_operations WHERE type='receipt'")).fetchone())[0],1)


if __name__=='__main__':
    unittest.main()
