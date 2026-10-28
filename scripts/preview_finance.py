"""Local UI test bench. Temporary database, synthetic auth, no Telegram requests.

Never expose port 8081 through a tunnel. Real Mini App is served by run.py on 8080.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.database.db import Database
from bot.database.finance import MOSCOW
from bot.web.server import create_app, STATIC
from datetime import datetime
from scripts.test_finance import init_data, TOKEN


async def main():
    with tempfile.TemporaryDirectory() as directory:
        db = Database(str(Path(directory) / 'ui-test.db'))
        await db.init_db()
        for pid in range(1,7):
            await db.receive_stock(pid,10,999,f"fixture:{pid}")
        await db.upsert_user(101, None, 'Учебный покупатель')
        for product_id in (1, 2):
            await db.add_to_cart(101, product_id)
            token = await db.create_checkout_token(101)
            oid, _ = await db.confirm_order(101, token, 'Учебный покупатель', 'store', 'demo')
            if product_id == 1:
                await db.demo_payment(oid, 101, 'succeeded')
        await db.save_expense({'amount': '100', 'category': 'delivery', 'date': datetime.now(MOSCOW).date().isoformat(), 'description': 'Доставка в учебный пункт выдачи', 'request_id': '7d58799c-dba8-45d3-af23-e4f90885703d'}, 999)

        @web.middleware
        async def bridge(request, handler):
            if request.path == '/':
                html = (STATIC / 'index.html').read_text(encoding='utf-8')
                code = '<script>window.Telegram={WebApp:{initData:'+json.dumps(init_data())+',ready(){},expand(){}}};</script>'
                html = html.replace('</head>', code+'</head>').replace('Учебный магазин', 'Локальный тест · не Telegram')
                return web.Response(text=html, content_type='text/html')
            return await handler(request)

        app = create_app(db, bot_token=TOKEN, managers={999})
        app.middlewares.insert(0, bridge)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, '127.0.0.1', 8081).start()
            print('Local UI test: http://127.0.0.1:8081 (temporary database)', flush=True)
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
