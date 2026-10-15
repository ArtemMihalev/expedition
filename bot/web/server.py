import logging
from datetime import datetime
from pathlib import Path

from aiohttp import web

from bot.config import BOT_TOKEN, MANAGER_IDS, INIT_DATA_TTL, DEMO_PAYMENTS_ENABLED
from bot.database.finance import CATEGORIES, MOSCOW
from bot.refunds import YooKassaRefunds
from bot.web.auth import validate_init_data

STATIC = Path(__file__).resolve().parent / 'static'
logger = logging.getLogger(__name__)
DB = web.AppKey('db', object)
BOT_TOKEN_KEY = web.AppKey('bot_token', str)
MANAGERS = web.AppKey('managers', set)
AUTH_TTL = web.AppKey('auth_ttl', int)
REFUND_SERVICE = web.AppKey('refund_service', object)


@web.middleware
async def security(request, handler):
    try:
        if request.path.startswith('/api/'):
            authorization = request.headers.get('Authorization', '')
            if not authorization.startswith('tma '):
                raise PermissionError('Откройте Mini App из Telegram под аккаунтом менеджера.')
            request['manager_id'] = validate_init_data(authorization[4:], request.app[BOT_TOKEN_KEY], request.app[MANAGERS], request.app[AUTH_TTL])
        response = await handler(request)
    except PermissionError as exc:
        response = web.json_response({'error': str(exc)}, status=403)
    except (ValueError, TypeError, KeyError):
        # Отдельно сохраняем понятные бизнес-ошибки, не возвращая данные запросов.
        response = web.json_response({'error': 'Некорректный запрос.'}, status=400)
    except web.HTTPException:
        raise
    except Exception:
        logger.error('Ошибка финансового API: %s %s', request.method, request.path)
        response = web.json_response({'error': 'Ошибка сервера. Операция не подтверждена; обновите данные.'}, status=500)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def period(request):
    today = datetime.now(MOSCOW).date()
    return request.query.get('start', today.replace(day=1).isoformat()), request.query.get('end', today.isoformat())


async def body(request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, TypeError):
        raise web.HTTPBadRequest(text='Ожидается JSON-объект.') from None


async def business(action):
    try:
        result = await action
        return web.json_response(result)
    except ValueError as exc:
        return web.json_response({'error': str(exc)}, status=400)


async def index(request):
    return web.FileResponse(STATIC / 'index.html')


async def summary(request):
    return await business(request.app[DB].summary(*period(request)))


async def orders(request):
    return web.json_response(await request.app[DB].finance_orders())


async def order(request):
    return await business(request.app[DB].finance_order(int(request.match_info['id'])))


async def operations(request):
    return await business(request.app[DB].operations(*period(request)))


async def expense(request):
    data = await body(request)
    replace_id = int(request.match_info['id']) if 'id' in request.match_info else None
    return await business(request.app[DB].save_expense(data, request['manager_id'], replace_id))


async def cancel_expense(request):
    return await business(request.app[DB].cancel_expense(int(request.match_info['id']), request['manager_id']))


async def start_refund(request):
    db = request.app[DB]
    try:
        result = await db.start_refund(int(request.match_info['id']), request['manager_id'])
        if result['provider'] == 'yookassa_test':
            result = await request.app[REFUND_SERVICE].process(db, result['id'])
        return web.json_response(result)
    except ValueError as exc:
        return web.json_response({'error': str(exc)}, status=400)


async def check_refund(request):
    db = request.app[DB]
    refund_id = request.match_info['id']
    try:
        refund = await db.refund_detail(refund_id)
        if refund['provider'] == 'yookassa_test':
            refund = await request.app[REFUND_SERVICE].process(db, refund_id)
        return web.json_response(refund)
    except ValueError as exc:
        return web.json_response({'error': str(exc)}, status=400)


async def demo_refund(request):
    if not DEMO_PAYMENTS_ENABLED:
        raise PermissionError('Имитатор выключен.')
    db = request.app[DB]
    data = await body(request)
    refund = await db.refund_detail(request.match_info['id'])
    if refund['provider'] != 'demo' or refund['status'] != 'refund_pending':
        raise PermissionError('Демонстрационный возврат недоступен.')
    if data.get('outcome') == 'succeeded':
        await db.complete_refund(refund['id'], 'demo-refund:' + refund['id'], refund['amount_kopecks'], 'RUB')
    elif data.get('outcome') == 'canceled':
        async with db.connection() as conn:
            await conn.execute("UPDATE refunds SET status='refund_failed',error='Учебная ошибка возврата' WHERE id=? AND status='refund_pending'", (refund['id'],))
            await conn.commit()
    else:
        raise ValueError
    return web.json_response(await db.refund_detail(refund['id']))


async def metadata(request):
    return web.json_response({'categories': CATEGORIES, 'demo_enabled': DEMO_PAYMENTS_ENABLED, 'manager_id': request['manager_id']})


def create_app(db, bot_token=BOT_TOKEN, managers=None, auth_ttl=INIT_DATA_TTL, refund_service=None):
    app = web.Application(middlewares=[security], client_max_size=32768)
    app[DB], app[BOT_TOKEN_KEY], app[MANAGERS], app[AUTH_TTL] = db, bot_token, MANAGER_IDS if managers is None else managers, auth_ttl
    app[REFUND_SERVICE] = refund_service or YooKassaRefunds()
    app.router.add_get('/', index)
    app.router.add_get('/api/meta', metadata)
    app.router.add_get('/api/summary', summary)
    app.router.add_get('/api/orders', orders)
    app.router.add_get('/api/orders/{id:\\d+}', order)
    app.router.add_get('/api/operations', operations)
    app.router.add_post('/api/expenses', expense)
    app.router.add_post('/api/expenses/{id:\\d+}/correct', expense)
    app.router.add_post('/api/expenses/{id:\\d+}/cancel', cancel_expense)
    app.router.add_post('/api/orders/{id:\\d+}/refund', start_refund)
    app.router.add_post('/api/refunds/{id}/check', check_refund)
    app.router.add_post('/api/refunds/{id}/demo', demo_refund)
    app.router.add_static('/static/', STATIC, show_index=False)
    return app
