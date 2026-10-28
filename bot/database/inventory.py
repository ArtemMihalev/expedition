"""SQLite is the lock: every inventory transition uses BEGIN IMMEDIATE."""
import asyncio
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone


class InventoryMixin:
    def training_crash(self, point):
        if self.training and getattr(self,'crash_point',None) == point:
            os._exit({'after_late_commit':74,'during_checkout':75}.get(point,73))

    def timestamp(self):
        return self.clock().astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')

    async def begin(self, conn):
        for attempt in range(4):
            try:
                await conn.execute('BEGIN IMMEDIATE')
                return
            except sqlite3.OperationalError as exc:
                if 'locked' not in str(exc) and 'busy' not in str(exc):
                    raise
                if attempt == 3:
                    raise ValueError('Склад занят. Безопасно повторите запрос с тем же ключом.') from None
                await asyncio.sleep(.025 * (attempt + 1))

    async def movement(self, conn, product, kind, hand, reserved, order=None, author=0, key=None):
        await conn.execute('''INSERT INTO stock_movements(product_id,kind,on_hand_delta,reserved_delta,
            order_id,author_id,operation_key,created_at) VALUES(?,?,?,?,?,?,?,?)''',
            (product, kind, hand, reserved, order, author, key, self.timestamp()))

    async def init_inventory(self):
        async with self.connection() as conn:
            await self.begin(conn)
            columns = {r['name'] for r in await (await conn.execute('PRAGMA table_info(products)')).fetchall()}
            if 'reserved' not in columns:
                await conn.execute('ALTER TABLE products ADD COLUMN reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved>=0)')
            for table, name, definition in [('orders','conflict_reason','TEXT'), ('refunds','reason',"TEXT NOT NULL DEFAULT 'sale'"),
                    ('refunds','attempts','INTEGER NOT NULL DEFAULT 0'), ('refunds','next_attempt_at','TEXT'),
                    ('refunds','lease_until','TEXT'), ('refunds','lease_token','TEXT'), ('refunds','first_attempt_at','TEXT')]:
                names = {r['name'] for r in await (await conn.execute(f'PRAGMA table_info({table})')).fetchall()}
                if name not in names:
                    await conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
            statements = [
                '''CREATE TABLE IF NOT EXISTS inventory_schema(version INTEGER PRIMARY KEY)''',
                '''CREATE TABLE IF NOT EXISTS reservations(order_id INTEGER PRIMARY KEY REFERENCES orders(id),
                    state TEXT NOT NULL CHECK(state IN ('active','consumed','cancelled','expired')),
                    expires_at TEXT NOT NULL,created_at TEXT NOT NULL)''',
                '''CREATE INDEX IF NOT EXISTS due_reservations ON reservations(state,expires_at)''',
                '''CREATE TABLE IF NOT EXISTS reservation_items(order_id INTEGER REFERENCES reservations(order_id),
                    product_id INTEGER REFERENCES products(id),quantity INTEGER NOT NULL CHECK(quantity>0),
                    PRIMARY KEY(order_id,product_id))''',
                '''CREATE TABLE IF NOT EXISTS checkout_requests(user_id INTEGER REFERENCES users(id),request_key TEXT,
                    fingerprint TEXT NOT NULL,order_id INTEGER REFERENCES orders(id),PRIMARY KEY(user_id,request_key))''',
                '''CREATE TABLE IF NOT EXISTS stock_movements(id INTEGER PRIMARY KEY,product_id INTEGER NOT NULL REFERENCES products(id),
                    kind TEXT NOT NULL,on_hand_delta INTEGER NOT NULL,reserved_delta INTEGER NOT NULL,
                    order_id INTEGER REFERENCES orders(id),author_id INTEGER NOT NULL,operation_key TEXT,created_at TEXT NOT NULL,
                    UNIQUE(product_id,operation_key))''',
                '''CREATE TRIGGER IF NOT EXISTS inventory_bounds_update BEFORE UPDATE OF stock,reserved ON products
                    WHEN typeof(NEW.stock)!='integer' OR typeof(NEW.reserved)!='integer' OR NEW.stock<0 OR NEW.reserved<0 OR NEW.reserved>NEW.stock
                    BEGIN SELECT RAISE(ABORT,'inventory bounds'); END''',
                '''CREATE TRIGGER IF NOT EXISTS inventory_bounds_insert BEFORE INSERT ON products
                    WHEN typeof(NEW.stock)!='integer' OR typeof(NEW.reserved)!='integer' OR NEW.stock<0 OR NEW.reserved<0 OR NEW.reserved>NEW.stock
                    BEGIN SELECT RAISE(ABORT,'inventory bounds'); END''',
                '''CREATE TABLE IF NOT EXISTS demo_provider_refunds(operation_key TEXT PRIMARY KEY,external_id TEXT UNIQUE,
                    amount_kopecks INTEGER NOT NULL,status TEXT NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS demo_refund_faults(refund_id TEXT PRIMARY KEY REFERENCES refunds(id),
                    mode TEXT NOT NULL CHECK(mode IN ('temporary','lost_response')),remaining INTEGER NOT NULL)''',
            ]
            for sql in statements:
                await conn.execute(sql)
            if not await (await conn.execute('SELECT 1 FROM inventory_schema WHERE version=1')).fetchone():
                for p in await (await conn.execute('SELECT id,stock FROM products')).fetchall():
                    await self.movement(conn,p['id'],'opening',p['stock'],0,key='opening')
                # Old paid/completed orders stay historical; only old unpaid holds are converted.
                rows = await (await conn.execute("SELECT * FROM orders WHERE stock_deducted=1 AND payment_status NOT IN ('paid','refunded') AND status IN ('new','ready')")).fetchall()
                for row in rows:
                    items = await (await conn.execute('SELECT product_id,quantity FROM order_items WHERE order_id=? AND product_id IS NOT NULL',(row['id'],))).fetchall()
                    await self._new_reservation(conn,row['id'],items)
                    for item in items:
                        await conn.execute('UPDATE products SET stock=stock+?,reserved=reserved+? WHERE id=?',(item['quantity'],item['quantity'],item['product_id']))
                        await self.movement(conn,item['product_id'],'legacy_hold',item['quantity'],item['quantity'],row['id'],key=f"legacy:{row['id']}")
                    await conn.execute('UPDATE orders SET stock_deducted=0 WHERE id=?',(row['id'],))
                await conn.execute('INSERT INTO inventory_schema VALUES(1)')
            await conn.commit()

    async def _new_reservation(self, conn, order_id, items):
        stamp = self.timestamp()
        expiry = (datetime.strptime(stamp,'%Y-%m-%d %H:%M:%S.%f') + timedelta(seconds=self.reserve_seconds)).strftime('%Y-%m-%d %H:%M:%S.%f')
        await conn.execute("INSERT INTO reservations VALUES(?,'active',?,?)",(order_id,expiry,stamp))
        for item in items:
            await conn.execute('INSERT INTO reservation_items VALUES(?,?,?)',(order_id,item['product_id'],item['quantity']))

    async def _transition_reservation(self, conn, order_id, target):
        row = await (await conn.execute('SELECT * FROM reservations WHERE order_id=?',(order_id,))).fetchone()
        if not row or row['state'] != 'active':
            return False
        if target == 'consumed' and self.timestamp() >= row['expires_at']:
            await self._transition_reservation(conn,order_id,'expired')
            return False
        if target == 'expired' and self.timestamp() < row['expires_at']:
            return False
        items = await (await conn.execute('SELECT * FROM reservation_items WHERE order_id=? ORDER BY product_id',(order_id,))).fetchall()
        for item in items:
            hand = -item['quantity'] if target == 'consumed' else 0
            await conn.execute('UPDATE products SET stock=stock+?,reserved=reserved-? WHERE id=?',(hand,item['quantity'],item['product_id']))
            await self.movement(conn,item['product_id'],target,hand,-item['quantity'],order_id,key=f'{target}:{order_id}')
        await conn.execute('UPDATE reservations SET state=? WHERE order_id=?',(target,order_id))
        if target == 'consumed':
            await conn.execute('UPDATE orders SET stock_deducted=1 WHERE id=?',(order_id,))
        else:
            await conn.execute("UPDATE orders SET status='cancelled' WHERE id=?",(order_id,))
        return True

    async def expire_reservations(self):
        async with self.connection() as conn:
            await self.begin(conn)
            rows = await (await conn.execute("SELECT order_id FROM reservations WHERE state='active' AND expires_at<=?",(self.timestamp(),))).fetchall()
            count = 0
            for row in rows:
                count += await self._transition_reservation(conn,row['order_id'],'expired')
            await conn.commit()
            return count

    @staticmethod
    def normalize_items(items):
        if not isinstance(items,list) or not 1 <= len(items) <= 100:
            raise ValueError('Нужен непустой состав заказа.')
        result = {}
        for item in items:
            if not isinstance(item,dict) or set(item) != {'product_id','quantity'}:
                raise ValueError('Некорректный состав заказа.')
            p,q = item.get('product_id'),item.get('quantity')
            if type(p) is not int or p<=0 or type(q) is not int or not 1<=q<=10000 or p in result:
                raise ValueError('Некорректный товар или количество.')
            result[p]=q
        return sorted(result.items())

    async def place_order(self,user_id,key,items,name,pickup,method='demo',token=None):
        normalized = self.normalize_items(items)
        if not isinstance(key,str) or not 1<=len(key)<=128 or not isinstance(name,str) or not 1<=len(name)<=100 or pickup not in {'store','pickup_point'} or method not in {'demo','yookassa_test','cash_on_pickup'}:
            raise ValueError('Некорректные параметры заказа.')
        fingerprint=json.dumps([normalized,name,pickup,method],ensure_ascii=False)
        async with self.connection() as conn:
            await self.begin(conn)
            prior=await (await conn.execute('SELECT * FROM checkout_requests WHERE user_id=? AND request_key=?',(user_id,key))).fetchone()
            if prior:
                if prior['fingerprint']!=fingerprint:
                    raise ValueError('Повторный ключ использован с другим составом или параметрами.')
                return prior['order_id'],'already_confirmed'
            products=[]
            for product,quantity in normalized:
                row=await (await conn.execute('SELECT * FROM products WHERE id=?',(product,))).fetchone()
                if not row or not row['is_available'] or row['stock']-row['reserved']<quantity:
                    raise ValueError('Сейчас нет в наличии нужного количества. Обновите корзину.')
                if row['cost_kopecks'] is None:
                    raise ValueError('Не задана себестоимость товара.')
                products.append((row,quantity))
            total=sum(p['price_kopecks']*q for p,q in products)
            cur=await conn.execute('''INSERT INTO orders(user_id,customer_name,pickup_method,payment_method,total,total_kopecks,payment_status,status)
                VALUES(?,?,?,?,?,?,?,'new')''',(user_id,name,pickup,method,total/100,total,'unpaid' if method=='cash_on_pickup' else 'pending'))
            oid=cur.lastrowid
            await self._new_reservation(conn,oid,[{'product_id':p,'quantity':q} for p,q in normalized])
            for p,q in products:
                await conn.execute('UPDATE products SET reserved=reserved+? WHERE id=?',(q,p['id']))
                await conn.execute('''INSERT INTO order_items(order_id,product_name,product_price,quantity,product_id,price_kopecks,cost_kopecks)
                    VALUES(?,?,?,?,?,?,?)''',(oid,p['name'],p['price_kopecks']/100,q,p['id'],p['price_kopecks'],p['cost_kopecks']))
                await self.movement(conn,p['id'],'reserve',0,q,oid,user_id,f'reserve:{oid}')
                self.training_crash('during_checkout')
            await conn.execute('INSERT INTO checkout_requests VALUES(?,?,?,?)',(user_id,key,fingerprint,oid))
            if token:
                await conn.execute('UPDATE checkout_tokens SET order_id=? WHERE token=? AND user_id=?',(oid,token,user_id))
                await conn.execute('DELETE FROM cart_items WHERE user_id=?',(user_id,))
            await self.create_pending_payment(conn,oid,total,method)
            await conn.commit()
            return oid,'ok'

    async def checkout(self,user_id,confirm_token,customer_name,pickup_method,payment_method):
        async with self.connection() as conn:
            token=await (await conn.execute('SELECT * FROM checkout_tokens WHERE token=? AND user_id=?',(confirm_token,user_id))).fetchone()
            if not token:
                return None,'Сессия оформления не найдена.'
            cart=await (await conn.execute('SELECT product_id,quantity FROM cart_items WHERE user_id=? ORDER BY product_id',(user_id,))).fetchall()
            if not cart and token['order_id']:
                cart=await (await conn.execute('SELECT product_id,quantity FROM order_items WHERE order_id=?',(token['order_id'],))).fetchall()
        try:
            return await self.place_order(user_id,confirm_token,[dict(r) for r in cart],customer_name,pickup_method,payment_method,token=confirm_token)
        except ValueError as exc:
            return None,str(exc)

    async def receive_stock(self,product,quantity,author,key):
        if type(product) is not int or type(quantity) is not int or not 1<=quantity<=1000000 or not isinstance(key,str) or not 1<=len(key)<=128:
            raise ValueError('Поступление: укажите товар, целое положительное количество и ключ запроса.')
        async with self.connection() as conn:
            await self.begin(conn)
            prior=await (await conn.execute('SELECT * FROM stock_movements WHERE operation_key=?',('receipt:'+key,))).fetchone()
            if prior:
                if (prior['product_id'],prior['on_hand_delta'],prior['author_id']) != (product,quantity,author):
                    raise ValueError('Ключ поступления использован с другими данными.')
                return prior['id']
            cur=await conn.execute('UPDATE products SET stock=stock+? WHERE id=?',(quantity,product))
            if not cur.rowcount:
                raise ValueError('Товар не найден.')
            await self.movement(conn,product,'receipt',quantity,0,author=author,key='receipt:'+key)
            await conn.commit()
        return True

    async def inventory_view(self):
        async with self.connection() as conn:
            await conn.execute('BEGIN')
            products=[dict(r) for r in await (await conn.execute('SELECT id,name,stock AS on_hand,reserved,stock-reserved AS available FROM products ORDER BY id')).fetchall()]
            reservations=[dict(r) for r in await (await conn.execute("SELECT r.*,o.user_id FROM reservations r JOIN orders o ON o.id=r.order_id WHERE r.state='active' ORDER BY expires_at")).fetchall()]
            movements=[dict(r) for r in await (await conn.execute('SELECT * FROM stock_movements ORDER BY id DESC LIMIT 300')).fetchall()]
            problems=[dict(r) for r in await (await conn.execute("SELECT r.*,o.conflict_reason FROM refunds r JOIN orders o ON o.id=r.order_id WHERE r.status!='refund_succeeded' OR r.reason='late' ORDER BY r.created_at DESC")).fetchall()]
            return dict(products=products,reservations=reservations,movements=movements,problems=problems,server_time=self.timestamp())
