"""Reproduce the historical paid-cancel defect in a disposable SQLite only.

Requires the project's previous commit, 862006b. Does not checkout files or
connect to Telegram. This is evidence of the old defect, not a current test.
"""
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
ROOT=Path(__file__).resolve().parent.parent


async def main():
    def source(path):
        return subprocess.check_output(['git','show','862006b:'+path],cwd=ROOT,encoding='utf-8')
    finance={'__name__':'stage2_finance'}
    exec(compile(source('bot/database/finance.py'),'stage2_finance.py','exec'),finance)
    namespace={'__name__':'stage2_db','FinanceMixin':finance['FinanceMixin']}
    code=source('bot/database/db.py').replace('from bot.database.finance import FinanceMixin','')
    exec(compile(code,'stage2_db.py','exec'),namespace)
    with tempfile.TemporaryDirectory() as directory:
        db=namespace['Database'](str(Path(directory)/'historical-test.db'))
        await db.init_db()
        await db.upsert_user(101,None,'Учебный покупатель')
        await db.add_to_cart(101,1,2)
        key=await db.create_checkout_token(101)
        oid,_=await db.confirm_order(101,key,'Учебный покупатель','store','demo')
        await db.demo_payment(oid,101,'succeeded')
        before=(await db.get_product(1))['stock']
        accepted=await db.update_order_status(oid,'cancelled',user_id=101)
        after=(await db.get_product(1))['stock']
        totals=await db.summary('2020-01-01','2099-12-31')
        result=dict(revision='862006b',command='.\\.venv\\Scripts\\python.exe scripts/reproduce_stage2_defect.py',input={'initial_stock':10,'quantity':2,'action':'Оплатить → отменить без подтверждённого возврата'},expected={'cancel_allowed':False,'stock':8,'refunds':0},actual={'cancel_allowed':accepted,'stock_before':before,'stock_after':after,'refunds':totals['refunds']},defect_reproduced=accepted and after==10)
        assert result['defect_reproduced'],result
        (ROOT/'docs'/'warehouse-defect-before.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Historical defect reproduced: paid cancellation restored stock 8 -> 10 without refund.')


if __name__=='__main__':
    asyncio.run(main())
