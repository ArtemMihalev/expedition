"""Loopback-only test process. No production configuration can enable crash hooks."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from aiohttp import web
from bot.database.db import Database
from bot.web.server import create_app
from scripts.test_finance import TOKEN


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--stop',choices=['before_late_commit','after_late_commit','during_checkout'])
    parser.add_argument('--pause-workers',action='store_true')
    args=parser.parse_args()
    path=Path(args.db).resolve()
    root=Path(__file__).resolve().parent.parent/'data'/'warehouse-tests'
    if not path.is_relative_to(root.resolve()) or not path.name.startswith('test-'):
        raise ValueError('Нужна отдельная data/warehouse-tests/test-*.db')
    db=Database(str(path),training=True)
    db.crash_point=args.stop
    await db.init_db()
    runner=web.AppRunner(create_app(db,bot_token=TOKEN,managers={999},workers=not bool(args.stop or args.pause_workers)),access_log=None)
    await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0)
    await site.start()
    print(json.dumps({'port':site._server.sockets[0].getsockname()[1]}),flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__=='__main__':
    asyncio.run(main())
