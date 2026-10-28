"""Durable refund jobs; leases coordinate workers, provider keys deduplicate calls."""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


class DemoRefunds:
    async def process(self, db, refund_id):
        refund = await db.refund_detail(refund_id)
        # This table is the isolated simulator's external provider store. It is
        # committed independently, just as a remote provider commits its result.
        async with db.connection() as conn:
            await db.begin(conn)
            existing = await (await conn.execute('SELECT * FROM demo_provider_refunds WHERE operation_key=?', (refund['attempt_key'],))).fetchone()
            if not existing:
                fault = await (await conn.execute('SELECT * FROM demo_refund_faults WHERE refund_id=? AND remaining>0', (refund_id,))).fetchone()
                if fault:
                    await conn.execute('UPDATE demo_refund_faults SET remaining=remaining-1 WHERE refund_id=?', (refund_id,))
                    if fault['mode'] == 'temporary':
                        await conn.commit()
                        raise ValueError('Имитатор временно недоступен. Задание сохранено.')
                external_id = 'demo-refund:' + refund_id
                await conn.execute("INSERT INTO demo_provider_refunds VALUES(?,?,?,'succeeded')", (refund['attempt_key'],external_id,refund['amount_kopecks']))
                await conn.commit()
                if fault and fault['mode'] == 'lost_response':
                    raise ValueError('Ответ имитатора потерян. Следующая попытка сначала проверит существующий возврат.')
            else:
                external_id = existing['external_id']
                if existing['amount_kopecks'] != refund['amount_kopecks']:
                    raise ValueError('Сумма возврата имитатора не совпадает.')
        await db.complete_refund(refund_id,external_id,refund['amount_kopecks'],'RUB')
        return await db.refund_detail(refund_id)


async def set_demo_fault(db, refund_id, mode):
    if not db.training or mode not in {'temporary','lost_response'}:
        raise ValueError('Переключатели ошибок доступны только на учебном стенде.')
    refund = await db.refund_detail(refund_id)
    if refund['provider'] != 'demo' or refund['status'] == 'refund_succeeded':
        raise ValueError('Недоступный возврат.')
    async with db.connection() as conn:
        await conn.execute('INSERT INTO demo_refund_faults VALUES(?,?,1) ON CONFLICT(refund_id) DO UPDATE SET mode=excluded.mode,remaining=1', (refund_id,mode))
        await conn.commit()


async def process_refund(db, refund_id, yookassa):
    lease = str(uuid.uuid4())
    async with db.connection() as conn:
        await db.begin(conn)
        stamp = db.timestamp()
        until = (datetime.strptime(stamp,'%Y-%m-%d %H:%M:%S.%f')+timedelta(seconds=5)).strftime('%Y-%m-%d %H:%M:%S.%f')
        cur = await conn.execute("""UPDATE refunds SET lease_token=?,lease_until=?,attempts=attempts+1,
            first_attempt_at=COALESCE(first_attempt_at,?) WHERE id=? AND status='refund_pending'
            AND (lease_until IS NULL OR lease_until<=?)""", (lease,until,stamp,refund_id,stamp))
        claimed = bool(cur.rowcount)
        await conn.commit()
    if not claimed:
        return await db.refund_detail(refund_id)
    try:
        refund = await db.refund_detail(refund_id)
        service = DemoRefunds() if refund['provider']=='demo' else yookassa
        return await service.process(db,refund_id)
    except ValueError as exc:
        await db.refund_failed(refund_id,str(exc))
        raise
    finally:
        async with db.connection() as conn:
            await conn.execute('UPDATE refunds SET lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=?',(refund_id,lease))
            await conn.commit()


async def tick(db, service):
    await db.expire_reservations()
    async with db.connection() as conn:
        ids = [r[0] for r in await (await conn.execute("SELECT id FROM refunds WHERE status='refund_pending' AND (next_attempt_at IS NULL OR next_attempt_at<=?)", (db.timestamp(),))).fetchall()]
    # A slow provider cannot postpone expiration or unrelated refunds.
    async def run(rid):
        try:
            await process_refund(db,rid,service)
        except ValueError:
            pass  # The durable job contains the visible error.
    await asyncio.gather(*(run(rid) for rid in ids))


async def inventory_worker(db, service, interval=5):
    pending = None
    try:
        while True:
            try:
                await db.expire_reservations()
                if pending is None or pending.done():
                    if pending is not None:
                        pending.result()
                    pending = asyncio.create_task(tick(db,service))
            except Exception:
                log.exception('Не удалось выполнить цикл склада')
            await asyncio.sleep(interval)
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending,return_exceptions=True)
