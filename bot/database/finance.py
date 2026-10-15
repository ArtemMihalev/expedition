"""Exact money, payment lifecycle and append-only financial records."""
import json
import uuid
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, InvalidOperation

from bot.config import MANAGER_IDS

MOSCOW = timezone(timedelta(hours=3))
CATEGORIES = {"delivery": "Доставка", "packaging": "Упаковка", "advertising": "Реклама", "communication": "Связь", "other": "Прочее"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def rubles_to_kopecks(value) -> int:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number > Decimal("10000000") or number * 100 != (number * 100).to_integral_value():
            raise ValueError
        return int(number * 100)
    except (InvalidOperation, ValueError):
        raise ValueError("Введите положительную сумму с максимум двумя знаками после запятой.") from None


def period_bounds(start: str, end: str) -> tuple[str, str]:
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last:
            raise ValueError
        low = datetime.combine(first, datetime.min.time(), MOSCOW).astimezone(timezone.utc)
        high = datetime.combine(last + timedelta(days=1), datetime.min.time(), MOSCOW).astimezone(timezone.utc)
        return low.strftime("%Y-%m-%d %H:%M:%S"), high.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError):
        raise ValueError("Неверный период: нужны даты YYYY-MM-DD, начало не позже конца.") from None


class FinanceMixin:
    async def init_finance(self):
        async with self.connection() as conn:
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS payments(
                    id TEXT PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
                    provider TEXT NOT NULL, provider_payment_id TEXT UNIQUE, telegram_charge_id TEXT UNIQUE,
                    amount_kopecks INTEGER NOT NULL CHECK(amount_kopecks > 0), currency TEXT NOT NULL DEFAULT 'RUB',
                    status TEXT NOT NULL CHECK(status IN ('pending','succeeded','canceled')),
                    created_at TEXT NOT NULL, confirmed_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_successful_payment ON payments(order_id) WHERE status = 'succeeded';
                CREATE TABLE IF NOT EXISTS refunds(
                    id TEXT PRIMARY KEY, order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
                    payment_id TEXT NOT NULL REFERENCES payments(id), provider_refund_id TEXT UNIQUE,
                    amount_kopecks INTEGER NOT NULL CHECK(amount_kopecks > 0),
                    status TEXT NOT NULL CHECK(status IN ('refund_pending','refund_succeeded','refund_failed')),
                    author_id INTEGER NOT NULL, created_at TEXT NOT NULL, confirmed_at TEXT, error TEXT,
                    attempt_key TEXT
                );
                CREATE TABLE IF NOT EXISTS finance_operations(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL CHECK(type IN ('receipt','refund','expense')),
                    amount_kopecks INTEGER NOT NULL CHECK(amount_kopecks > 0), currency TEXT NOT NULL DEFAULT 'RUB',
                    occurred_at TEXT NOT NULL, created_at TEXT NOT NULL, order_id INTEGER REFERENCES orders(id),
                    external_event_id TEXT UNIQUE, author_id INTEGER NOT NULL, category TEXT,
                    description TEXT NOT NULL DEFAULT '', cancelled_at TEXT, request_id TEXT UNIQUE,
                    replacement_of INTEGER REFERENCES finance_operations(id), cost_kopecks INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_finance_order_event ON finance_operations(order_id,type) WHERE type IN ('receipt','refund');
                CREATE TABLE IF NOT EXISTS finance_audit(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id INTEGER NOT NULL REFERENCES finance_operations(id),
                    action TEXT NOT NULL, author_id INTEGER NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS finance_notifications(
                    operation_id INTEGER NOT NULL REFERENCES finance_operations(id), manager_id INTEGER NOT NULL,
                    sent_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT,
                    PRIMARY KEY(operation_id,manager_id)
                );
            """)
            await conn.execute("BEGIN IMMEDIATE")
            additions = {
                "products": {"price_kopecks": "INTEGER", "cost_kopecks": "INTEGER CHECK(cost_kopecks >= 0)"},
                "orders": {"total_kopecks": "INTEGER"},
                "order_items": {"price_kopecks": "INTEGER", "cost_kopecks": "INTEGER CHECK(cost_kopecks >= 0)"},
            }
            for table, columns in additions.items():
                existing = {r['name'] for r in await (await conn.execute(f"PRAGMA table_info({table})")).fetchall()}
                for name, definition in columns.items():
                    if name not in existing:
                        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            refund_columns = {r['name'] for r in await (await conn.execute('PRAGMA table_info(refunds)')).fetchall()}
            if 'attempt_key' not in refund_columns:
                await conn.execute('ALTER TABLE refunds ADD COLUMN attempt_key TEXT')
            await conn.execute('UPDATE refunds SET attempt_key=id WHERE attempt_key IS NULL')
            for table, old, new in (("products", "price", "price_kopecks"), ("orders", "total", "total_kopecks"), ("order_items", "product_price", "price_kopecks")):
                rows = await (await conn.execute(f"SELECT id, {old} AS value FROM {table} WHERE {new} IS NULL")).fetchall()
                for row in rows:
                    amount = int((Decimal(str(row['value'])) * 100).quantize(Decimal('1')))
                    await conn.execute(f"UPDATE {table} SET {new}=? WHERE id=?", (amount, row['id']))
            costs = {"Рюкзак TrekPro 40L": 290000, "Органайзер TravelCase": 45000, "Адаптер WorldPlug": 70000, "Набор ComfortSet": 25000, "Тревел-набор SafeTrip": 80000, "Сумка PackIt для обуви": 30000}
            for name, cost in costs.items():
                await conn.execute("UPDATE products SET cost_kopecks=? WHERE name=? AND cost_kopecks IS NULL", (cost, name))
            # Историческую себестоимость не выдумываем: старые order_items.cost_kopecks остаются NULL.
            old_paid = await (await conn.execute("""SELECT * FROM orders WHERE payment_status='paid'
                AND paid_at IS NOT NULL AND provider_payment_charge_id IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM finance_operations f WHERE f.order_id=orders.id AND type='receipt')""")).fetchall()
            for row in old_paid:
                await self._confirm_payment(conn, dict(row), 'RUB', row['total_kopecks'], row['telegram_payment_charge_id'], row['provider_payment_charge_id'], 'yookassa_test', row['paid_at'])
            await conn.commit()

    async def _confirm_payment(self, conn, order, currency, amount, telegram_charge, provider_charge, provider, confirmed_at=None):
        if currency != 'RUB' or type(amount) is not int or amount != order['total_kopecks'] or amount <= 0:
            return False
        if provider != order['payment_method'] or provider not in {'yookassa_test', 'demo'}:
            return False
        successful = await (await conn.execute("SELECT * FROM payments WHERE order_id=? AND status='succeeded'", (order['id'],))).fetchone()
        if successful:
            return successful['provider_payment_id'] == provider_charge and successful['telegram_charge_id'] == telegram_charge
        duplicate = await (await conn.execute("SELECT id FROM payments WHERE provider_payment_id=? OR telegram_charge_id=?", (provider_charge, telegram_charge))).fetchone()
        if duplicate:
            return False
        payment = await (await conn.execute("SELECT * FROM payments WHERE order_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1", (order['id'],))).fetchone()
        timestamp = confirmed_at or now()
        payment_id = payment['id'] if payment else str(uuid.uuid4())
        if payment:
            await conn.execute("UPDATE payments SET status='succeeded', provider_payment_id=?, telegram_charge_id=?, confirmed_at=? WHERE id=?", (provider_charge, telegram_charge, timestamp, payment_id))
        else:
            await conn.execute("INSERT INTO payments VALUES(?,?,?,?,?,?,?,'succeeded',?,?)", (payment_id, order['id'], provider, provider_charge, telegram_charge, amount, currency, timestamp, timestamp))
        await conn.execute("UPDATE orders SET payment_status='paid', telegram_payment_charge_id=?, provider_payment_charge_id=?, paid_at=? WHERE id=?", (telegram_charge, provider_charge, timestamp, order['id']))
        items = await (await conn.execute("SELECT cost_kopecks, quantity FROM order_items WHERE order_id=?", (order['id'],))).fetchall()
        cost = None if any(i['cost_kopecks'] is None for i in items) else sum(i['cost_kopecks'] * i['quantity'] for i in items)
        cursor = await conn.execute("""INSERT INTO finance_operations(type,amount_kopecks,occurred_at,created_at,order_id,external_event_id,author_id,description,cost_kopecks)
            VALUES('receipt',?,?,?,?,?,?,?,?)""", (amount, timestamp, now(), order['id'], 'payment:' + provider_charge, order['user_id'], 'Подтверждённая учебная оплата', cost))
        for manager_id in MANAGER_IDS if confirmed_at is None else ():
            await conn.execute("INSERT INTO finance_notifications(operation_id,manager_id) VALUES(?,?)", (cursor.lastrowid, manager_id))
        return True

    async def create_pending_payment(self, conn, order_id, amount, provider):
        if provider not in {'yookassa_test', 'demo'}:
            return
        await conn.execute("INSERT INTO payments(id,order_id,provider,amount_kopecks,status,created_at) VALUES(?,?,?,?,'pending',?)", (str(uuid.uuid4()), order_id, provider, amount, now()))

    async def demo_payment(self, order_id, user_id, outcome):
        if outcome not in {'succeeded', 'canceled'}:
            raise ValueError('Неверное состояние платежа.')
        async with self.connection() as conn:
            await conn.execute('BEGIN IMMEDIATE')
            row = await (await conn.execute('SELECT * FROM orders WHERE id=? AND user_id=?', (order_id, user_id))).fetchone()
            if not row or row['payment_method'] != 'demo' or row['status'] != 'new':
                raise ValueError('Учебный платёж недоступен.')
            payment = await (await conn.execute("SELECT * FROM payments WHERE order_id=? ORDER BY created_at DESC LIMIT 1", (order_id,))).fetchone()
            if not payment or payment['status'] != 'pending':
                raise ValueError('Платёж уже завершён.')
            if outcome == 'canceled':
                await conn.execute("UPDATE payments SET status='canceled',confirmed_at=? WHERE id=?", (now(), payment['id']))
                await conn.execute("UPDATE orders SET payment_status='canceled' WHERE id=?", (order_id,))
            else:
                await self._confirm_payment(conn, dict(row), 'RUB', row['total_kopecks'], 'demo-tg:' + payment['id'], 'demo:' + payment['id'], 'demo')
            await conn.commit()

    async def summary(self, start, end):
        low, high = period_bounds(start, end)
        async with self.connection() as conn:
            operations = await (await conn.execute("SELECT * FROM finance_operations WHERE occurred_at>=? AND occurred_at<? AND cancelled_at IS NULL", (low, high))).fetchall()
        totals = {'receipt': 0, 'refund': 0, 'expense': 0}
        cost_paid = cost_returned = unknown = 0
        for op in operations:
            totals[op['type']] += op['amount_kopecks']
            if op['type'] in {'receipt', 'refund'}:
                if op['cost_kopecks'] is None:
                    unknown += 1
                elif op['type'] == 'receipt':
                    cost_paid += op['cost_kopecks']
                else:
                    cost_returned += op['cost_kopecks']
        cash = totals['receipt'] - totals['refund'] - totals['expense']
        return dict(start=start, end=end, timezone='Europe/Moscow', currency='RUB', receipts=totals['receipt'], refunds=totals['refund'], expenses=totals['expense'], cost_paid=cost_paid, cost_returned=cost_returned, cash_flow=cash,
                    result=None if unknown else cash - cost_paid + cost_returned, unknown_cost_operations=unknown)

    async def finance_orders(self):
        async with self.connection() as conn:
            return [dict(r) for r in await (await conn.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 200')).fetchall()]

    async def finance_order(self, order_id):
        order = await self.get_order(order_id)
        if not order:
            raise ValueError('Заказ не найден.')
        async with self.connection() as conn:
            order['payments'] = [dict(r) for r in await (await conn.execute('SELECT * FROM payments WHERE order_id=?', (order_id,))).fetchall()]
            order['refunds'] = [dict(r) for r in await (await conn.execute('SELECT * FROM refunds WHERE order_id=?', (order_id,))).fetchall()]
        return order

    async def operations(self, start, end):
        low, high = period_bounds(start, end)
        async with self.connection() as conn:
            rows = [dict(r) for r in await (await conn.execute('SELECT * FROM finance_operations WHERE occurred_at>=? AND occurred_at<? ORDER BY occurred_at DESC,id DESC LIMIT 500', (low, high))).fetchall()]
            for row in rows:
                row['history'] = [dict(r) for r in await (await conn.execute('SELECT * FROM finance_audit WHERE operation_id=? ORDER BY id', (row['id'],))).fetchall()]
            return rows

    async def save_expense(self, data, author_id, replace_id=None):
        amount = rubles_to_kopecks(data.get('amount', ''))
        category, description = data.get('category'), data.get('description', '')
        if category not in CATEGORIES or not isinstance(description, str) or not 1 <= len(description.strip()) <= 200:
            raise ValueError('Выберите категорию и описание от 1 до 200 символов.')
        day = data.get('date', '')
        period_bounds(day, day)
        occurred = datetime.combine(date.fromisoformat(day), datetime.min.time(), MOSCOW).replace(hour=12).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        request_id = data.get('request_id')
        try:
            uuid.UUID(request_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Некорректный идентификатор запроса.') from None
        async with self.connection() as conn:
            await conn.execute('BEGIN IMMEDIATE')
            previous = await (await conn.execute('SELECT * FROM finance_operations WHERE request_id=?', (request_id,))).fetchone()
            if previous:
                if (previous['amount_kopecks'], previous['category'], previous['description'], previous['occurred_at'], previous['author_id'], previous['replacement_of']) != (amount, category, description.strip(), occurred, author_id, replace_id):
                    raise ValueError('Повторный запрос отличается от исходного.')
                return previous['id']
            if replace_id is not None:
                await self._cancel_expense(conn, replace_id, author_id, 'corrected')
            cur = await conn.execute("""INSERT INTO finance_operations(type,amount_kopecks,occurred_at,created_at,author_id,category,description,request_id,replacement_of)
                VALUES('expense',?,?,?,?,?,?,?,?)""", (amount, occurred, now(), author_id, category, description.strip(), request_id, replace_id))
            await conn.execute('INSERT INTO finance_audit(operation_id,action,author_id,created_at,detail) VALUES(?,?,?,?,?)', (cur.lastrowid, 'created', author_id, now(), json.dumps(data, ensure_ascii=False)))
            await conn.commit()
            return cur.lastrowid

    async def _cancel_expense(self, conn, operation_id, author_id, action='cancelled'):
        row = await (await conn.execute("SELECT * FROM finance_operations WHERE id=? AND type='expense'", (operation_id,))).fetchone()
        if not row or row['cancelled_at']:
            raise ValueError('Расход не найден или уже отменён.')
        await conn.execute('UPDATE finance_operations SET cancelled_at=? WHERE id=?', (now(), operation_id))
        await conn.execute('INSERT INTO finance_audit(operation_id,action,author_id,created_at,detail) VALUES(?,?,?,?,?)', (operation_id, action, author_id, now(), 'Исходная запись сохранена'))

    async def cancel_expense(self, operation_id, author_id):
        async with self.connection() as conn:
            await conn.execute('BEGIN IMMEDIATE')
            await self._cancel_expense(conn, operation_id, author_id)
            await conn.commit()

    async def start_refund(self, order_id, author_id):
        async with self.connection() as conn:
            await conn.execute('BEGIN IMMEDIATE')
            order = await (await conn.execute('SELECT * FROM orders WHERE id=?', (order_id,))).fetchone()
            payment = await (await conn.execute("SELECT * FROM payments WHERE order_id=? AND status='succeeded'", (order_id,))).fetchone()
            if not order or order['payment_status'] != 'paid' or not payment:
                raise ValueError('Возврат возможен только после успешной оплаты.')
            refund = await (await conn.execute('SELECT * FROM refunds WHERE order_id=?', (order_id,))).fetchone()
            if refund:
                if refund['status'] == 'refund_succeeded':
                    raise ValueError('Полный возврат уже проведён.')
                if refund['status'] == 'refund_failed':
                    await conn.execute("UPDATE refunds SET status='refund_pending',error=NULL,provider_refund_id=NULL,attempt_key=? WHERE id=?", (str(uuid.uuid4()), refund['id']))
                refund_id = refund['id']
            else:
                refund_id = str(uuid.uuid4())
                await conn.execute("""INSERT INTO refunds(id,order_id,payment_id,amount_kopecks,status,author_id,created_at,attempt_key)
                    VALUES(?,?,?,?,'refund_pending',?,?,?)""", (refund_id, order_id, payment['id'], payment['amount_kopecks'], author_id, now(), refund_id))
            await conn.commit()
        return await self.refund_detail(refund_id)

    async def refund_detail(self, refund_id):
        async with self.connection() as conn:
            row = await (await conn.execute('SELECT r.*,p.provider,p.provider_payment_id FROM refunds r JOIN payments p ON p.id=r.payment_id WHERE r.id=?', (refund_id,))).fetchone()
            if not row:
                raise ValueError('Возврат не найден.')
            return dict(row)

    async def refund_failed(self, refund_id, error):
        async with self.connection() as conn:
            await conn.execute("UPDATE refunds SET error=? WHERE id=? AND status='refund_pending'", (error, refund_id))
            await conn.commit()

    async def complete_refund(self, refund_id, external_id, amount, currency, confirmed_at=None):
        async with self.connection() as conn:
            await conn.execute('BEGIN IMMEDIATE')
            refund = await (await conn.execute('SELECT * FROM refunds WHERE id=?', (refund_id,))).fetchone()
            if not refund or currency != 'RUB' or type(amount) is not int or amount != refund['amount_kopecks']:
                raise ValueError('Сумма или валюта возврата не совпадает.')
            if refund['status'] == 'refund_succeeded':
                return False
            if refund['status'] != 'refund_pending':
                raise ValueError('Возврат не ожидает подтверждения.')
            timestamp = confirmed_at or now()
            receipt = await (await conn.execute("SELECT * FROM finance_operations WHERE order_id=? AND type='receipt'", (refund['order_id'],))).fetchone()
            await conn.execute("UPDATE refunds SET status='refund_succeeded',provider_refund_id=?,confirmed_at=?,error=NULL WHERE id=?", (external_id, timestamp, refund_id))
            await conn.execute("""INSERT INTO finance_operations(type,amount_kopecks,occurred_at,created_at,order_id,external_event_id,author_id,description,cost_kopecks)
                VALUES('refund',?,?,?,?,?,?,?,?)""", (amount, timestamp, now(), refund['order_id'], 'refund:' + external_id, refund['author_id'], 'Подтверждённый полный возврат', receipt['cost_kopecks']))
            order = await (await conn.execute('SELECT * FROM orders WHERE id=?', (refund['order_id'],))).fetchone()
            if order['stock_deducted']:
                items = await (await conn.execute('SELECT product_id,quantity FROM order_items WHERE order_id=?', (order['id'],))).fetchall()
                for item in items:
                    if item['product_id'] is not None:
                        await conn.execute('UPDATE products SET stock=stock+? WHERE id=?', (item['quantity'], item['product_id']))
            await conn.execute("UPDATE orders SET payment_status='refunded',status='cancelled',stock_deducted=0 WHERE id=?", (refund['order_id'],))
            await conn.commit()
            return True
