"""Prepare/reset only a specifically named isolated teaching database."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from bot.database.db import Database

ROOT=Path(__file__).resolve().parent.parent/'data'/'warehouse-tests'


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--stock',type=int,default=10)
    parser.add_argument('--buyers',type=int,default=50)
    parser.add_argument('--reset',action='store_true')
    args=parser.parse_args()
    path=Path(args.db).resolve()
    if path.parent!=ROOT.resolve() or not path.name.startswith('test-') or path.suffix!='.db':
        parser.error('Разрешены только data/warehouse-tests/test-*.db')
    if not 0<=args.stock<=1000000 or not 1<=args.buyers<=500:
        parser.error('Некорректные начальные данные')
    if path.exists() and not args.reset:
        parser.error('База существует. Для сброса именно этой тестовой базы укажите --reset; сервер должен быть остановлен.')
    if args.reset:
        # No recursion, glob, or configurable production database path.
        for target in [path,Path(str(path)+'-wal'),Path(str(path)+'-shm'),Path(str(path)+'-journal')]:
            if target.parent!=ROOT.resolve():
                raise ValueError('Путь вне тестового каталога')
            target.unlink(missing_ok=True)
    db=Database(str(path),training=True)
    await db.init_db()
    if args.stock:
        await db.receive_stock(1,args.stock,999,'initial')
    for i in range(args.buyers):
        await db.upsert_user(1000+i,None,'Учебный покупатель')
    print(f'Тестовая база: {path}; товар 1: {args.stock}/0/{args.stock}; покупатели 1000..{999+args.buyers}')


if __name__=='__main__':
    asyncio.run(main())
