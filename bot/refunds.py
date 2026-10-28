"""Test YooKassa refunds. Never infer success from the button click."""
import asyncio
from decimal import Decimal
from datetime import datetime, timezone
from urllib.parse import urlencode

import aiohttp

from bot.config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY


class YooKassaRefunds:
    def __init__(self, shop_id=YOOKASSA_SHOP_ID, secret_key=YOOKASSA_SECRET_KEY):
        self.shop_id, self.secret_key = shop_id, secret_key
        self.lock = asyncio.Lock()

    async def request(self, method, path, payload=None, key=None):
        if not self.shop_id or not self.secret_key.startswith('test_'):
            raise ValueError('Добавьте YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY тестового магазина в .env.')
        headers = {'Idempotence-Key': key} if key else {}
        try:
            async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(self.shop_id, self.secret_key), timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.request(method, 'https://api.yookassa.ru/v3/' + path, json=payload, headers=headers) as response:
                    if response.status >= 400:
                        raise ValueError(f'ЮKassa отклонила запрос (HTTP {response.status}). Проверьте тестовый магазин и его настройки.')
                    return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise ValueError('ЮKassa недоступна. Возврат не подтверждён, сводка не изменена. Повторите проверку.') from None

    @staticmethod
    def matches_amount(obj, amount):
        try:
            return obj['amount']['currency'] == 'RUB' and Decimal(obj['amount']['value']) * 100 == amount
        except (KeyError, TypeError, ArithmeticError):
            return False

    async def process(self, db, refund_id):
        async with self.lock:
            refund = await db.refund_detail(refund_id)
            if refund['provider'] != 'yookassa_test':
                raise ValueError('Этот возврат не относится к ЮKassa.')
            if refund['status'] == 'refund_succeeded':
                return refund
            try:
                payment = await self.request('GET', 'payments/' + refund['provider_payment_id'])
                if payment.get('id') != refund['provider_payment_id'] or payment.get('test') is not True or payment.get('status') != 'succeeded' or not self.matches_amount(payment, refund['amount_kopecks']):
                    raise ValueError('Провайдер не подтвердил успешный тестовый платёж с нужной суммой.')
                external_id = refund['provider_refund_id']
                if not external_id and refund.get('attempts',0) > 1:
                    # Query provider before resending after an ambiguous response.
                    cursor = None
                    while True:
                        params = {'payment_id':refund['provider_payment_id'],'limit':100}
                        if cursor:
                            params['cursor'] = cursor
                        page = await self.request('GET','refunds?' + urlencode(params))
                        matches = [r for r in page.get('items',[]) if r.get('description') == f"Учебный полный возврат заказа {refund['order_id']}" and self.matches_amount(r,refund['amount_kopecks'])]
                        if matches:
                            external_id = matches[0]['id']
                            break
                        cursor = page.get('next_cursor')
                        if not cursor:
                            break
                    first = refund.get('first_attempt_at')
                    if not external_id and first and (datetime.now(timezone.utc)-datetime.fromisoformat(first).replace(tzinfo=timezone.utc)).total_seconds() >= 23*3600:
                        raise ValueError('Срок безопасного повтора ЮKassa истёк. Нужна сверка с провайдером; новый возврат автоматически не отправляется.')
                if not external_id:
                    created = await self.request('POST', 'refunds', {
                        'payment_id': refund['provider_payment_id'],
                        'amount': {'value': f"{Decimal(refund['amount_kopecks']) / 100:.2f}", 'currency': 'RUB'},
                        'description': f"Учебный полный возврат заказа {refund['order_id']}",
                    }, key=refund['attempt_key'])
                    external_id = created['id']
                    async with db.connection() as conn:
                        await conn.execute('UPDATE refunds SET provider_refund_id=? WHERE id=?', (external_id, refund_id))
                        await conn.commit()
                result = await self.request('GET', 'refunds/' + external_id)
                if result.get('id') != external_id or result.get('payment_id') != refund['provider_payment_id'] or not self.matches_amount(result, refund['amount_kopecks']):
                    raise ValueError('Возврат провайдера не совпадает с сохранённым заказом.')
                if result.get('status') == 'succeeded':
                    await db.complete_refund(refund_id, external_id, refund['amount_kopecks'], 'RUB')
                elif result.get('status') == 'canceled':
                    async with db.connection() as conn:
                        await conn.execute("UPDATE refunds SET status='refund_failed',error='ЮKassa отменила возврат' WHERE id=?", (refund_id,))
                        await conn.commit()
                    raise ValueError('ЮKassa отменила возврат. Сводка не изменилась.')
            except ValueError as exc:
                await db.refund_failed(refund_id, str(exc))
                raise
            return await db.refund_detail(refund_id)
