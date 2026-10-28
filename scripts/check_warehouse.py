"""One-command evidence: HTTP barriers, real SQLite, separate server processes."""
import argparse
import asyncio
import csv
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import aiohttp
from aiohttp.test_utils import TestClient,TestServer
from bot.database.db import Database
from bot.inventory_worker import process_refund,tick,set_demo_fault
from bot.refunds import YooKassaRefunds
from bot.web.server import create_app
from scripts.test_finance import TOKEN,init_data

ROOT=Path(__file__).resolve().parent.parent
TEST_ROOT=ROOT/'data'/'warehouse-tests'
REPORT=[]
ACTIVE_ACCEPTANCE=None


class Clock:
    def __init__(self):
        self.value=datetime(2026,10,22,10,tzinfo=timezone.utc)
    def __call__(self):
        return self.value
    def advance(self,seconds):
        self.value+=timedelta(seconds=seconds)


def headers(user=999):
    return {'Authorization':'tma '+init_data(user)}


def tables(path):
    with sqlite3.connect(path) as conn:
        conn.row_factory=sqlite3.Row
        names=['products','orders','order_items','reservations','reservation_items','payments','refunds','stock_movements','finance_operations','demo_provider_refunds']
        return {n:[dict(r) for r in conn.execute('SELECT * FROM '+n)] for n in names}


def invariant(path):
    with sqlite3.connect(path) as conn:
        rows=conn.execute('''SELECT p.id,p.stock,p.reserved,
            COALESCE((SELECT SUM(i.quantity) FROM reservation_items i JOIN reservations r ON r.order_id=i.order_id WHERE i.product_id=p.id AND r.state='active'),0),
            COALESCE((SELECT SUM(on_hand_delta) FROM stock_movements m WHERE m.product_id=p.id),0),
            COALESCE((SELECT SUM(reserved_delta) FROM stock_movements m WHERE m.product_id=p.id),0) FROM products p''').fetchall()
        for pid,stock,reserved,active,hand_journal,reserved_journal in rows:
            assert 0<=reserved<=stock,(pid,stock,reserved)
            assert reserved==active==reserved_journal,(pid,reserved,active,reserved_journal)
            assert stock==hand_journal,(pid,stock,hand_journal)
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert conn.execute("SELECT order_id FROM finance_operations WHERE type IN ('receipt','refund') GROUP BY order_id,type HAVING COUNT(*)>1").fetchall()==[]


class Bench:
    async def open(self,label,stock=10,real_time=False):
        self.clock=None if real_time else Clock()
        self.path=TEST_ROOT/f'test-{label}-{uuid.uuid4().hex}.db'
        self.db=Database(str(self.path),clock=self.clock,training=True)
        await self.db.init_db()
        async with self.db.connection() as conn:
            await conn.execute('UPDATE products SET price=1200,price_kopecks=120000,cost_kopecks=70000 WHERE id=1')
            await conn.commit()
        if stock:
            await self.db.receive_stock(1,stock,999,'initial')
        self.clients=[]
        # Two independent applications and Database objects use the same file.
        for _ in range(2):
            other=Database(str(self.path),clock=self.clock,training=True)
            client=TestClient(TestServer(create_app(other,bot_token=TOKEN,managers={999},workers=False)))
            await client.start_server()
            self.clients.append(client)
        return self

    async def close(self):
        for c in self.clients:
            await c.close()

    async def call(self,path,data=None,user=999,status=200,index=0):
        client=self.clients[index]
        response=await (client.get(path,headers=headers(user)) if data is None else client.post(path,headers=headers(user),json=data))
        result=await response.json()
        assert response.status==status,(path,response.status,result)
        return result

    async def order(self,user=101,key=None,items=None,status=200):
        data=dict(request_id=key or str(uuid.uuid4()),items=items or [{'product_id':1,'quantity':1}],customer_name='Учебный покупатель',pickup_method='store',payment_method='demo')
        return await self.call('/api/shop/orders',data,user,status)

    async def pay(self,oid,outcome='succeeded'):
        return await self.call(f'/api/training/orders/{oid}/payment',{'outcome':outcome})

    async def snapshot(self):
        invariant(self.path)
        return dict(database=str(self.path.relative_to(ROOT)),tables=tables(self.path),summary=await self.db.summary('2020-01-01','2099-12-31'))


async def scenario(name,expected,action):
    started=time.monotonic()
    try:
        actual=await action()
        REPORT.append(dict(acceptance_id=ACTIVE_ACCEPTANCE,scenario=name,expected=expected,actual=actual,status='passed',seconds=round(time.monotonic()-started,3)))
        print('PASS '+name,flush=True)
    except Exception as exc:
        REPORT.append(dict(acceptance_id=ACTIVE_ACCEPTANCE,scenario=name,expected=expected,actual=repr(exc),status='FAILED',seconds=round(time.monotonic()-started,3)))
        print('FAIL '+name+': '+repr(exc),flush=True)
        import traceback
        traceback.print_exc()


def report_result(actual):
    if not isinstance(actual,dict):
        return str(actual)
    concise={k:actual[k] for k in ['database','initial','triples','winner','recovery_seconds','second_restart_unchanged','crash_exit','summary'] if k in actual}
    if 'tables' in actual:
        tables=actual['tables']
        p=tables['products'][0]
        concise['on_hand_reserved_available']=[p['stock'],p['reserved'],p['stock']-p['reserved']]
        concise['orders']=len(tables['orders'])
        concise['reservations']=len(tables['reservations'])
        concise['provider_refunds']=len(tables['demo_provider_refunds'])
    responses=actual.get('responses',[])
    if responses and isinstance(responses[0],dict) and 'status' in responses[0]:
        concise['successes']=sum(r['status']==200 for r in responses)
        concise['shortages']=sum(r['status']==400 for r in responses)
    return json.dumps(concise,ensure_ascii=False)


async def scarcity(stock,buyers,iteration):
    b=await Bench().open(f'concurrent-{stock}-{buyers}-{iteration}',stock)
    try:
        barrier=asyncio.Event()
        ready=0
        all_ready=asyncio.Event()
        async def buy(i):
            nonlocal ready
            ready+=1
            if ready==buyers: all_ready.set()
            await barrier.wait()
            data=dict(request_id=f'buyer-{i}',items=[{'product_id':1,'quantity':1}],customer_name='Тест',pickup_method='store',payment_method='demo')
            response=await b.clients[i%2].post('/api/shop/orders',headers=headers(1000+i),json=data)
            value=await response.json()
            return {'user':1000+i,'key':f'buyer-{i}','status':response.status,'response':value}
        tasks=[asyncio.create_task(buy(i)) for i in range(buyers)]
        await all_ready.wait()
        barrier.set()
        results=await asyncio.gather(*tasks)
        assert sum(r['status']==200 for r in results)==min(stock,buyers),results
        assert all(r['status']==200 or (r['status']==400 and r['response'].get('error')=='Сейчас нет в наличии нужного количества. Обновите корзину.') for r in results),results
        snapshot=await b.snapshot()
        assert len(snapshot['tables']['orders'])==min(stock,buyers)
        assert len(snapshot['tables']['reservations'])==min(stock,buyers)
        assert snapshot['tables']['products'][0]['stock']==stock
        assert snapshot['tables']['products'][0]['reserved']==min(stock,buyers)
        assert snapshot['summary']['receipts']==0
        return dict(initial={'stock':stock,'buyers':buyers,'reserve_seconds':120},responses=results,**snapshot)
    finally:
        await b.close()


async def rollback_and_replay():
    b=await Bench().open('rollback',1)
    try:
        items=[{'product_id':1,'quantity':1},{'product_id':2,'quantity':1}]
        await b.order(items=items,status=400)
        s=await b.snapshot()
        assert not s['tables']['orders'] and not s['tables']['reservations']
        assert s['tables']['products'][0]['reserved']==0
        await b.db.receive_stock(2,1,999,'second-product')
        key=str(uuid.uuid4())
        oid,_=await b.order(key=key,items=items)
        for _ in range(3):
            assert (await b.order(key=key,items=items))[0]==oid
        await b.order(key=key,items=[{'product_id':1,'quantity':1}],status=400)
        receipt={'product_id':1,'quantity':10,'request_id':str(uuid.uuid4())}
        await b.call('/api/inventory/receipts',receipt)
        await b.call('/api/inventory/receipts',receipt)
        await b.call('/api/inventory/receipts',{**receipt,'quantity':11},status=400)
        s=await b.snapshot()
        assert len(s['tables']['orders'])==1 and s['tables']['products'][0]['stock']==11
        return s
    finally:
        await b.close()


async def sale_return():
    b=await Bench().open('sale-return',0)
    try:
        await b.call('/api/inventory/receipts',dict(product_id=1,quantity=10,request_id='receipt10'))
        states=[]
        async def triple():
            p=(await b.call('/api/inventory'))['products'][0]
            states.append([p['on_hand'],p['reserved'],p['available']])
        await triple()
        oid,_=await b.order(items=[{'product_id':1,'quantity':3}]);await triple()
        for _ in range(5): await b.pay(oid)
        await b.pay(oid,'canceled')
        await triple()
        other,_=await b.order(202,items=[{'product_id':1,'quantity':2}]);await triple()
        b.clock.advance(120)
        await b.db.expire_reservations();await b.db.expire_reservations();await triple()
        refund=await b.call(f'/api/orders/{oid}/refund',{})
        assert (await b.db.summary('2020-01-01','2099-12-31'))['refunds']==0
        await triple()
        await b.call('/api/refunds/'+refund['id']+'/demo',{'outcome':'succeeded'})
        await triple()
        await process_refund(b.db,refund['id'],YooKassaRefunds())
        assert states==[[10,0,10],[10,3,7],[7,0,7],[7,2,5],[7,0,7],[7,0,7],[10,0,10]],states
        s=await b.snapshot()
        assert (s['summary']['cash_flow'],s['summary']['result'])==(0,0)
        return dict(triples=states,**s)
    finally:
        await b.close()


async def boundary(offset):
    b=await Bench().open('boundary-'+str(offset),1)
    try:
        oid,_=await b.order()
        b.clock.advance(120+offset)
        await b.pay(oid)
        s=await b.snapshot()
        reserve=s['tables']['reservations'][0]
        assert reserve['state']==('consumed' if offset<0 else 'expired')
        assert s['summary']['cost_paid']==(70000 if offset<0 else 0)
        assert len(s['tables']['refunds'])==(0 if offset<0 else 1)
        return dict(offset_seconds=offset,**s)
    finally:
        await b.close()


async def race(iteration,expired=False):
    b=await Bench().open(f'race-{iteration}-{expired}',1)
    try:
        oid,_=await b.order()
        if expired: b.clock.advance(120)
        gate=asyncio.Event()
        async def run(action):
            await gate.wait()
            return await action()
        competitor=b.db.expire_reservations if expired else lambda:b.call(f'/api/shop/orders/{oid}/cancel',{},101)
        tasks=[asyncio.create_task(run(lambda:b.pay(oid))),asyncio.create_task(run(competitor))]
        gate.set();results=await asyncio.gather(*tasks)
        s=await b.snapshot();state=s['tables']['reservations'][0]['state']
        assert state in ({'expired'} if expired else {'consumed','cancelled'}),state
        assert len(s['tables']['payments'])==1 and len(s['tables']['finance_operations'])==1
        if state=='consumed':
            assert not s['tables']['refunds'] and s['tables']['products'][0]['stock']==0
        else:
            assert len(s['tables']['refunds'])==1 and s['tables']['products'][0]['stock']==1
        return dict(winner=state,explanation='Первый допустимый переход под BEGIN IMMEDIATE; оплата проверяет now >= expires_at внутри транзакции',responses=results,**s)
    finally:
        await b.close()


async def late_refund(mode):
    b=await Bench().open('late-'+mode,1)
    try:
        a,_=await b.order(101)
        b.clock.advance(120);await b.db.expire_reservations()
        bb,_=await b.order(202);await b.pay(bb)
        await b.pay(a)
        before=await b.snapshot()
        assert before['summary']['receipts']==240000 and before['summary']['cost_paid']==70000
        assert before['summary']['pending_late_refunds']==120000
        refund=before['tables']['refunds'][0]
        await b.call('/api/training/refunds/'+refund['id']+'/fault',{'mode':mode})
        await tick(b.db,YooKassaRefunds())
        error=await b.db.refund_detail(refund['id'])
        assert error['status']=='refund_pending' and error['error']
        assert (await b.db.summary('2020-01-01','2099-12-31'))['refunds']==0
        restarted=Database(str(b.path),clock=b.clock,training=True)
        await restarted.init_db()
        await tick(restarted,YooKassaRefunds())
        for _ in range(5): await b.pay(a);await b.pay(bb);await process_refund(restarted,refund['id'],YooKassaRefunds())
        s=await b.snapshot()
        assert (s['summary']['receipts'],s['summary']['refunds'],s['summary']['cost_paid'],s['summary']['cost_returned'],s['summary']['cash_flow'],s['summary']['result'])==(240000,120000,70000,0,120000,50000),s['summary']
        assert s['tables']['products'][0]['stock']==0 and len(s['tables']['demo_provider_refunds'])==1
        assert (await b.db.refund_detail(refund['id']))['attempt_key']==refund['attempt_key']
        return dict(initial={'price':120000,'cost':70000,'stock':1},pending_summary=before['summary'],error=error,**s)
    finally:
        await b.close()


async def auth():
    b=await Bench().open('auth',1)
    try:
        oid,_=await b.order()
        before=await b.snapshot()
        response=await b.clients[0].get(f'/api/shop/orders/{oid}',headers=headers(202))
        assert response.status==404
        for path,data in [('/api/inventory',None),('/api/inventory/receipts',{'product_id':1,'quantity':100,'request_id':'evil'}),('/api/training/orders/1/payment',{})]:
            await b.call(path,data,202,403)
        for raw in ['invalid',init_data(999,timestamp=1),init_data(999,token='wrong')]:
            response=await b.clients[0].get('/api/inventory',headers={'Authorization':'tma '+raw})
            assert response.status==403
        for quantity in [0,-1,1.5,True]:
            await b.call('/api/inventory/receipts',dict(product_id=1,quantity=quantity,request_id=str(uuid.uuid4())),status=400)
        await b.call('/api/shop/orders',dict(request_id='evil',items=[{'product_id':1,'quantity':1}],total=1,expires_at='2099'),101,400)
        assert not await b.call(f'/api/shop/orders/{oid}/cancel',{},202)
        # Production application never registers teaching payment/fault controls.
        production=Database(str(b.path))
        paths=[r.resource.canonical for r in create_app(production,bot_token=TOKEN,managers={999},workers=False).router.routes()]
        assert not any('/training/' in p for p in paths)
        after=await b.snapshot()
        assert after['tables']==before['tables'] and after['summary']==before['summary']
        return dict(before=before,**after)
    finally:
        await b.close()


async def start_child(path,stop=None,pause_workers=False):
    args=[sys.executable,str(ROOT/'scripts'/'warehouse_server.py'),'--db',str(path)]
    if stop: args+=['--stop',stop]
    if pause_workers: args+=['--pause-workers']
    process=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,cwd=ROOT,env=dict(os.environ,PYTHONIOENCODING='utf-8'))
    try:
        line=await asyncio.wait_for(process.stdout.readline(),15)
        return process,json.loads(line)['port']
    except Exception:
        process.terminate();await process.wait()
        raise


async def restart(stop):
    b=await Bench().open('restart-'+stop,1,real_time=True)
    process=None
    try:
        b.db.reserve_seconds=1
        await b.db.upsert_user(101,None,'А')
        a,_=await b.db.place_order(101,'A',[{'product_id':1,'quantity':1}],'А','store')
        await asyncio.sleep(1.1)
        await b.db.expire_reservations()
        b.db.reserve_seconds=120
        await b.db.upsert_user(202,None,'Б')
        bb,_=await b.db.place_order(202,'B',[{'product_id':1,'quantity':1}],'Б','store')
        await b.db.demo_payment(bb,202,'succeeded')
        process,port=await start_child(b.path,stop)
        async with aiohttp.ClientSession() as client:
            try:
                await client.post(f'http://127.0.0.1:{port}/api/training/orders/{a}/payment',json={'outcome':'succeeded'},headers=headers())
            except aiohttp.ClientError:
                pass
        code=await asyncio.wait_for(process.wait(),10)
        assert code==(74 if stop=='after_late_commit' else 73),code
        crashed=tables(b.path)
        assert len(crashed['finance_operations'])==(2 if stop=='after_late_commit' else 1)
        assert len(crashed['refunds'])==(1 if stop=='after_late_commit' else 0)
        assert not crashed['demo_provider_refunds']
        fault_steps=[]
        if stop=='after_late_commit':
            # Resume the actual server with its worker deliberately paused so
            # both provider failures can be observed before automatic recovery.
            process,port=await start_child(b.path,pause_workers=True)
            rid=crashed['refunds'][0]['id']
            async with aiohttp.ClientSession() as client:
                for mode in ['temporary','lost_response']:
                    response=await client.post(f'http://127.0.0.1:{port}/api/training/refunds/{rid}/fault',headers=headers(),json={'mode':mode})
                    assert response.status==200
                    response=await client.post(f'http://127.0.0.1:{port}/api/refunds/{rid}/demo',headers=headers(),json={'outcome':'succeeded'})
                    error=await response.json()
                    assert response.status==400,error
                    state=tables(b.path)
                    assert state['refunds'][0]['status']=='refund_pending' and state['refunds'][0]['error']
                    assert not any(op['type']=='refund' for op in state['finance_operations'])
                    assert len(state['demo_provider_refunds'])==(1 if mode=='lost_response' else 0)
                    fault_steps.append(dict(mode=mode,error=error,tables=state))
            process.terminate();await process.wait()
        process,port=await start_child(b.path)
        async with aiohttp.ClientSession() as client:
            if stop=='before_late_commit':
                response=await client.post(f'http://127.0.0.1:{port}/api/training/orders/{a}/payment',json={'outcome':'succeeded'},headers=headers())
                assert response.status==200
            started=time.monotonic()
            while time.monotonic()-started<10:
                rows=tables(b.path)['refunds']
                if rows and rows[0]['status']=='refund_succeeded': break
                await asyncio.sleep(.1)
            else: raise AssertionError('Возврат не восстановлен за два интервала')
        s=await b.snapshot()
        assert len(s['tables']['finance_operations'])==3
        assert len(s['tables']['demo_provider_refunds'])==1
        assert s['tables']['products'][0]['stock']==0
        assert s['summary']['result']==50000
        return dict(crash_exit=code,after_crash=crashed,fault_steps=fault_steps,recovery_seconds=round(time.monotonic()-started,3),**s)
    finally:
        if process and process.returncode is None:
            process.terminate();await process.wait()
        await b.close()


async def reserve_restart():
    b=await Bench().open('reserve-restart',1,real_time=True)
    process=None
    try:
        b.db.reserve_seconds=1
        await b.db.upsert_user(101,None,'Тест')
        oid,_=await b.db.place_order(101,'reserve',[{'product_id':1,'quantity':1}],'Тест','store')
        process,port=await start_child(b.path,'after_late_commit')
        process.terminate();await process.wait()
        await asyncio.sleep(1.1)
        started=time.monotonic()
        process,port=await start_child(b.path)
        async with aiohttp.ClientSession() as client:
            response=await client.get(f'http://127.0.0.1:{port}/api/inventory',headers=headers())
            value=await response.json()
        assert not value['reservations'] and value['products'][0]['available']==1
        s=await b.snapshot()
        assert s['tables']['reservations'][0]['state']=='expired'
        recovery=round(time.monotonic()-started,3)
        process.terminate();await process.wait()
        process,port=await start_child(b.path)
        again=await b.snapshot()
        assert again['tables']==s['tables'] and again['summary']==s['summary']
        return dict(recovery_seconds=recovery,second_restart_unchanged=True,**s)
    finally:
        if process and process.returncode is None:
            process.terminate();await process.wait()
        await b.close()


async def multi_rollback():
    b=await Bench().open('multi-3-0',3)
    try:
        await b.order(items=[{'product_id':1,'quantity':1},{'product_id':2,'quantity':1}],status=400)
        s=await b.snapshot()
        assert not s['tables']['orders'] and not s['tables']['order_items'] and not s['tables']['reservations']
        assert not s['tables']['finance_operations']
        assert (s['tables']['products'][0]['stock'],s['tables']['products'][0]['reserved'])==(3,0)
        assert not any(m['kind']=='reserve' for m in s['tables']['stock_movements'])
        return dict(initial={'A':3,'B':0},**s)
    finally:
        await b.close()


async def concurrent_key():
    b=await Bench().open('same-key',3)
    try:
        # Create the identity before the start barrier; every request still
        # validates the signed buyer identity through the real HTTP middleware.
        await b.db.upsert_user(101,None,'Тест')
        key=str(uuid.uuid4())
        gate=asyncio.Event()
        async def duplicate(i):
            await gate.wait()
            return await b.call('/api/shop/orders',dict(request_id=key,items=[{'product_id':1,'quantity':1}],customer_name='Тест',pickup_method='store',payment_method='demo'),101,index=i%2)
        tasks=[asyncio.create_task(duplicate(i)) for i in range(20)]
        # All tasks reach the event before any request is released.
        await asyncio.sleep(0)
        gate.set()
        results=await asyncio.gather(*tasks)
        oid=results[0][0]
        assert all(r[0]==oid for r in results),results
        await b.call('/api/shop/orders',dict(request_id=key,items=[{'product_id':1,'quantity':2}],customer_name='Тест',pickup_method='store',payment_method='demo'),101,status=400)
        s=await b.snapshot()
        assert len(s['tables']['orders'])==len(s['tables']['reservations'])==1
        assert s['tables']['products'][0]['reserved']==1
        return dict(request_key=key,responses=results,**s)
    finally:
        await b.close()


async def five_payments():
    b=await Bench().open('five-payments',1)
    try:
        oid,_=await b.order()
        for _ in range(5): await b.pay(oid)
        await b.pay(oid,'canceled')
        s=await b.snapshot()
        p=s['tables']['products'][0]
        assert (p['stock'],p['reserved'])==(0,0)
        assert len(s['tables']['payments'])==1 and s['tables']['payments'][0]['status']=='succeeded'
        assert len(s['tables']['finance_operations'])==1 and s['summary']['receipts']==120000
        assert s['tables']['reservations'][0]['state']=='consumed'
        return s
    finally:
        await b.close()


async def cancel_and_expire():
    b=await Bench().open('cancel-twice-expire',1)
    try:
        a,_=await b.order()
        assert await b.call(f'/api/shop/orders/{a}/cancel',{},101)
        assert not await b.call(f'/api/shop/orders/{a}/cancel',{},101)
        bb,_=await b.order(202)
        b.clock.advance(120)
        assert await b.db.expire_reservations()==1
        assert await b.db.expire_reservations()==0
        s=await b.snapshot()
        assert s['tables']['products'][0]['stock']==1 and s['tables']['products'][0]['reserved']==0
        assert not s['tables']['finance_operations']
        movements=s['tables']['stock_movements']
        assert sum(m['kind']=='cancelled' and m['order_id']==a for m in movements)==1
        assert sum(m['kind']=='expired' and m['order_id']==bb for m in movements)==1
        return s
    finally:
        await b.close()


async def checkout_restart():
    b=await Bench().open('checkout-crash',3,real_time=True)
    process=None
    try:
        await b.db.receive_stock(2,3,999,'second-product')
        data=dict(request_id='same-after-crash',items=[{'product_id':1,'quantity':1},{'product_id':2,'quantity':1}],customer_name='Тест',pickup_method='store',payment_method='demo')
        process,port=await start_child(b.path,'during_checkout')
        async with aiohttp.ClientSession() as client:
            try:
                await client.post(f'http://127.0.0.1:{port}/api/shop/orders',headers=headers(101),json=data)
            except aiohttp.ClientError:
                pass
        code=await asyncio.wait_for(process.wait(),10)
        assert code==75,code
        after_crash=await b.snapshot()
        for name in ['orders','order_items','reservations','reservation_items','payments','finance_operations']:
            assert not after_crash['tables'][name],(name,after_crash['tables'][name])
        assert not any(m['kind']=='reserve' for m in after_crash['tables']['stock_movements'])
        assert all(p['reserved']==0 for p in after_crash['tables']['products'])
        process,port=await start_child(b.path)
        async with aiohttp.ClientSession() as client:
            results=[]
            for _ in range(2):
                response=await client.post(f'http://127.0.0.1:{port}/api/shop/orders',headers=headers(101),json=data)
                assert response.status==200
                results.append(await response.json())
        assert results[0][0]==results[1][0]
        s=await b.snapshot()
        assert len(s['tables']['orders'])==1 and len(s['tables']['order_items'])==2
        assert [p['reserved'] for p in s['tables']['products'][:2]]==[1,1]
        return dict(crash_exit=code,stop_point='После записей первого товара, до записей второго и до commit',after_crash=after_crash,responses=results,**s)
    finally:
        if process and process.returncode is None:
            process.terminate();await process.wait()
        await b.close()


async def main(args):
    TEST_ROOT.mkdir(parents=True,exist_ok=True)
    if not args.skip_unit:
        result=await asyncio.create_subprocess_exec(sys.executable,'-m','unittest','discover','-s','scripts','-p','test_*.py',cwd=ROOT,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,env=dict(os.environ,PYTHONIOENCODING='utf-8'))
        output,_=await result.communicate()
        (ROOT/'docs'/'warehouse-unit.log').write_bytes(output)
        REPORT.append(dict(scenario='Прежние регрессионные тесты',expected='exit 0',actual=output.decode(errors='replace'),status='passed' if result.returncode==0 else 'FAILED'))
        if result.returncode: raise AssertionError('Регрессионные тесты: docs/warehouse-unit.log')
    global ACTIVE_ACCEPTANCE
    selected=args.scenario or list(range(1,14))
    for number in selected:
        ACTIVE_ACCEPTANCE=number
        if number==1:
            await scenario('№1 Цепочка остатков и полный возврат','Шесть троек и неизменность pending',sale_return)
        elif number in (2,3):
            stock,buyers=(args.stock,args.buyers) if number==2 else (10,50)
            for i in range(args.repeat):
                await scenario(f'№{number} Остаток {stock}, покупателей {buyers}, прогон {i+1}',f'{min(stock,buyers)} успешных резервов; остальные — именно нехватка',lambda s=stock,n=buyers,j=i:scarcity(s,n,j))
        elif number==4:
            await scenario('№4 Многотоварный откат 3+0','Товар А остаётся 3/0/3; заказов и резервов нет',multi_rollback)
        elif number==5:
            for i in range(args.repeat):
                await scenario(f'№5 Одновременный повтор одного ключа, прогон {i+1}','20 ответов с одним ID, один резерв; другой состав отклонён',concurrent_key)
        elif number==6:
            await scenario('№6 Пять подтверждений 1200 ₽ и canceled','Одна продажа, одно поступление, 0/0/0',five_payments)
        elif number==7:
            await scenario('№7 Двойная отмена и отдельное истечение','Освобождение по одному разу, поступлений нет',cancel_and_expire)
        elif number==8:
            for i in range(args.repeat):
                for expired in [False,True]:
                    await scenario(f'№8 Гонка {i+1}, граница={expired}','Один допустимый переход, одна денежная запись',lambda j=i,e=expired:race(j,e))
            for offset in [-.001,0,.001]:
                await scenario(f'№8 Оплата относительно границы: {offset} с','Продажа только строго до срока',lambda o=offset:boundary(o))
        elif number==9:
            await scenario('№9 Последняя единица Б и поздняя оплата А','2400−1200=1200 ₽; себестоимость 700 ₽; результат 500 ₽',lambda:late_refund('temporary'))
        elif number==10:
            await scenario('№10 Перезапуск с резервом','Освобождение за ≤10 с; второй запуск ничего не меняет',reserve_restart)
        elif number==11:
            await scenario('№11 Остановка, ошибка и потерянный ответ возврата','Один внешний возврат, без лишнего товара',lambda:restart('after_late_commit'))
            await scenario('№11 Потерянный ответ имитатора','Повтор сверяет прежний ключ; результат 500 ₽',lambda:late_refund('lost_response'))
        elif number==12:
            await scenario('№12 Остановка внутри оформления до commit','Нет половины заказа; тот же ключ после запуска оформляет заказ',checkout_restart)
        elif number==13:
            await scenario('№13 Авторизация и подмена параметров','403/404/400; чужие данные и суммы не изменены',auth)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='backslashreplace')
    sys.stderr.reconfigure(encoding='utf-8',errors='backslashreplace')
    parser=argparse.ArgumentParser()
    parser.add_argument('--scenario',type=int,nargs='+',choices=range(1,14),help='Номера сценариев приёмки; по умолчанию все 13')
    parser.add_argument('--stock',type=int,default=1)
    parser.add_argument('--buyers',type=int,default=20)
    parser.add_argument('--repeat',type=int,default=5)
    parser.add_argument('--skip-unit',action='store_true')
    args=parser.parse_args()
    if not 0<=args.stock<=1000 or not 1<=args.buyers<=500 or args.repeat<5:
        parser.error('Остаток 0..1000, покупателей 1..500, повторов не меньше 5')
    failed=False
    try:
        asyncio.run(main(args))
        failed=any(row['status']=='FAILED' for row in REPORT)
    except Exception:
        import traceback
        traceback.print_exc()
        failed=True
        REPORT.append(dict(scenario='Остальные проверки после ошибки подготовки',expected='Полный запуск',actual='не запускалось: ошибка подготовки или регрессионных тестов; см. traceback и warehouse-unit.log',status='not_run'))
    finally:
        report=dict(command=subprocess.list2cmdline(sys.argv),created_at=datetime.now(timezone.utc).isoformat(),scenarios=REPORT)
        (ROOT/'docs'/'warehouse-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        with (ROOT/'docs'/'warehouse-report.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['сценарий','ожидалось','получено','статус'])
            for row in REPORT: writer.writerow([row['scenario'],row['expected'],report_result(row['actual']),row['status']])
    sys.exit(1 if failed else 0)
