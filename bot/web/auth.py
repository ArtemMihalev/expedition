import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_init_data(raw: str, token: str, managers: set[int], ttl: int = 3600, current_time=None) -> int:
    """Verify the original initData using the fixed server-side bot token."""
    try:
        if not raw or len(raw) > 16384 or not token:
            raise ValueError
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        data = dict(pairs)
        if len(pairs) != len(data):
            raise ValueError
        received_hash = data.pop('hash')
        check_string = '\n'.join(f'{key}={value}' for key, value in sorted(data.items()))
        secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            raise ValueError
        timestamp = int(data['auth_date'])
        age = (time.time() if current_time is None else current_time) - timestamp
        if age < -30 or age > ttl:
            raise ValueError
        user = json.loads(data['user'])
        user_id = user['id']
        if type(user_id) is not int:
            raise ValueError
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise PermissionError('Недействительная или устаревшая авторизация. Откройте кабинет заново из бота.') from None
    if managers is not None and user_id not in managers:
        raise PermissionError('Финансовый кабинет доступен только менеджеру.')
    return user_id
