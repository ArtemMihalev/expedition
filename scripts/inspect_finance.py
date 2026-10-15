"""Read-only financial evidence without customer names, tokens or Telegram IDs."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.config import DATABASE_PATH


def main():
    path = Path(DATABASE_PATH).resolve()
    if not path.is_file():
        raise SystemExit('База ещё не создана. Сначала запустите run.py.')
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
        queries = {
            'Заказы (суммы в копейках)': 'SELECT id,status,payment_status,total_kopecks FROM orders ORDER BY id DESC LIMIT 20',
            'Платежи': 'SELECT id,order_id,provider,status,amount_kopecks FROM payments ORDER BY id DESC LIMIT 20',
            'Возвраты': 'SELECT id,order_id,status,amount_kopecks FROM refunds ORDER BY id DESC LIMIT 20',
            'Операции': 'SELECT id,type,order_id,amount_kopecks,occurred_at,cancelled_at,replacement_of FROM finance_operations ORDER BY id DESC LIMIT 30',
        }
        for label, query in queries.items():
            print('\n' + label)
            cursor = conn.execute(query)
            print(' | '.join(column[0] for column in cursor.description))
            for row in cursor:
                print(' | '.join('—' if value is None else str(value) for value in row))


if __name__ == '__main__':
    try:
        main()
    except sqlite3.OperationalError:
        raise SystemExit('Нет финансовых таблиц. Запустите run.py для миграции базы.') from None
