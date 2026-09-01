#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

# Manual runs do not get systemd EnvironmentFile variables.
# Load .env before reading TELEGRAM_LIMIT_* from os.environ.
load_dotenv('.env')

from app.config import settings
from app.db import SessionLocal
from app.services.telegram_limit_service import build_vehicle_limit_message
from app.utils import current_year_month, normalize_plate


BOT_API_BASE = 'https://api.telegram.org/bot'
CHECK_LIMIT_BUTTON = '⛽ Проверить лимит'
HELP_BUTTON = 'ℹ️ Помощь'
AWAITING_LIMIT_PLATE: set[str] = set()


def _env(name: str, default: str = '') -> str:
    return os.getenv(name, default).strip()


def _bot_token() -> str:
    # Dedicated token for the /limit bot has priority so it does not conflict
    # with the main Telegram bot that may already use getUpdates/webhook.
    token = (
        _env('TELEGRAM_LIMIT_BOT_TOKEN')
        or _env('TELEGRAM_BOT_TOKEN')
        or str(getattr(settings, 'telegram_bot_token', '') or '').strip()
    )
    if not token:
        raise SystemExit('[STOP] TELEGRAM_LIMIT_BOT_TOKEN / TELEGRAM_BOT_TOKEN is not configured')
    return token


def _allowed_chat_ids() -> set[str]:
    raw_limit = _env('TELEGRAM_LIMIT_ALLOWED_CHAT_IDS')
    if raw_limit.lower() in {'all', '*', 'any', 'everyone'}:
        return set()

    raw = raw_limit or _env('TELEGRAM_ALLOWED_CHAT_IDS')
    if raw.lower() in {'all', '*', 'any', 'everyone'}:
        return set()

    if not raw:
        raw = str(getattr(settings, 'telegram_chat_id', '') or '')
    return {item.strip() for item in raw.replace(';', ',').split(',') if item.strip()}


def _offset_path() -> Path:
    return Path(_env('TELEGRAM_LIMIT_OFFSET_PATH', './tmp/telegram_limit_bot_offset.txt'))


def _api_url(method: str) -> str:
    return f'{BOT_API_BASE}{_bot_token()}/{method}'


def _read_offset() -> int | None:
    path = _offset_path()
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding='utf-8').strip())
    except Exception:
        return None


def _write_offset(offset: int) -> None:
    path = _offset_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(offset), encoding='utf-8')


def _main_keyboard() -> dict[str, Any]:
    return {
        'keyboard': [
            [{'text': CHECK_LIMIT_BUTTON}],
            [{'text': HELP_BUTTON}],
        ],
        'resize_keyboard': True,
        'one_time_keyboard': False,
        'is_persistent': True,
        'input_field_placeholder': 'Нажмите кнопку или введите госномер',
    }


def _send_message(
    chat_id: int | str,
    text: str,
    *,
    reply_to_message_id: int | None = None,
    with_keyboard: bool = True,
) -> bool:
    payload: dict[str, Any] = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    if reply_to_message_id is not None:
        payload['reply_to_message_id'] = reply_to_message_id
    if with_keyboard:
        payload['reply_markup'] = _main_keyboard()

    response = requests.post(_api_url('sendMessage'), json=payload, timeout=30)
    if not response.ok:
        print('[WARN] sendMessage failed:', response.status_code, response.text[:500], flush=True)
    return response.ok


def _get_updates(offset: int | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        'timeout': 30,
        'allowed_updates': ['message'],
    }
    if offset is not None:
        params['offset'] = offset

    response = requests.get(_api_url('getUpdates'), params=params, timeout=40)
    response.raise_for_status()
    payload = response.json()
    if not payload.get('ok'):
        raise RuntimeError(f'Telegram getUpdates failed: {payload}')
    return payload.get('result') or []


def _command_without_mention(text: str) -> str:
    return re.sub(r'^/([a-zA-Z0-9_]+)@[A-Za-z0-9_]+', r'/\1', text.strip())


def _parse_limit_command(text: str) -> tuple[str | None, str | None, bool]:
    """
    Returns: plate, year_month, help_requested.

    Supported:
      /limit 34ABC123
      /limit 34ABC123 2026-08
      /limit help
    """
    text = _command_without_mention(text)
    parts = text.split()

    if not parts or parts[0].lower() != '/limit':
        return None, None, False

    if len(parts) == 1:
        return None, None, True

    if parts[1].lower() in {'help', '?', 'помощь'}:
        return None, None, True

    plate = None
    year_month = None

    for arg in parts[1:]:
        if re.fullmatch(r'\d{4}-\d{2}', arg):
            year_month = arg
        elif arg.strip():
            plate = normalize_plate(arg)

    return plate, year_month, False


def _parse_plate_input(text: str) -> tuple[str | None, str | None]:
    """Parse plain input after pressing the keyboard button.

    Supported:
      34ABC123
      34ABC123 2026-08
    """
    parts = text.strip().split()
    plate = None
    year_month = None

    for arg in parts:
        if re.fullmatch(r'\d{4}-\d{2}', arg):
            year_month = arg
        elif arg.strip():
            plate = normalize_plate(arg)

    return plate, year_month


def _help_text() -> str:
    return (
        'Команда лимита по машине:\n\n'
        f'Нажмите кнопку <b>{html_escape(CHECK_LIMIT_BUTTON)}</b> и отправьте госномер.\n\n'
        '<code>34ABC123</code> — лимит за текущий месяц\n'
        '<code>34ABC123 2026-08</code> — лимит за выбранный месяц\n\n'
        'Также работает старый формат:\n'
        '<code>/limit 34ABC123</code>\n'
        '<code>/limit 34ABC123 2026-08</code>'
    )


def html_escape(value: Any) -> str:
    return (
        str(value)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def _sender_label(message: dict[str, Any]) -> str:
    sender = message.get('from') or {}
    chat = message.get('chat') or {}

    user_id = sender.get('id') or chat.get('id') or '-'
    username = sender.get('username') or chat.get('username') or '-'
    first_name = sender.get('first_name') or ''
    last_name = sender.get('last_name') or ''
    full_name = ' '.join(x for x in [first_name, last_name] if x).strip() or '-'
    chat_id = chat.get('id') or '-'
    chat_type = chat.get('type') or '-'
    chat_title = chat.get('title') or '-'

    return (
        f'chat_id={chat_id} chat_type={chat_type} chat_title={chat_title!r} '
        f'user_id={user_id} username={username!r} full_name={full_name!r}'
    )


def _log_limit_request(message: dict[str, Any], text: str, allowed: bool, *, source: str = 'command') -> None:
    if source == 'plain_plate':
        plate, year_month = _parse_plate_input(text)
        help_requested = False
    else:
        plate, year_month, help_requested = _parse_limit_command(text)
    requested_period = year_month or current_year_month()
    print(
        '[AUDIT] /limit request '
        f'allowed={allowed} '
        f'source={source} '
        f'plate={plate or "-"} '
        f'year_month={requested_period} '
        f'help={help_requested} '
        f'{_sender_label(message)}',
        flush=True,
    )


def _send_limit_result(chat_id: int, message_id: int | None, plate: str, year_month: str | None) -> None:
    db = SessionLocal()
    try:
        response_text = build_vehicle_limit_message(
            db,
            plate=plate,
            year_month=year_month or current_year_month(),
        )
    finally:
        db.close()

    _send_message(chat_id, response_text, reply_to_message_id=message_id)


def _handle_limit(chat_id: int, message_id: int | None, text: str) -> None:
    plate, year_month, help_requested = _parse_limit_command(text)
    if help_requested or not plate:
        _send_message(chat_id, _help_text(), reply_to_message_id=message_id)
        return

    _send_limit_result(chat_id, message_id, plate, year_month)


def _is_allowed(chat_id: Any, allowed_chat_ids: set[str]) -> bool:
    return chat_id is not None and (not allowed_chat_ids or str(chat_id) in allowed_chat_ids)


def _handle_update(update: dict[str, Any], allowed_chat_ids: set[str]) -> None:
    message = update.get('message') or {}
    text = str(message.get('text') or '').strip()
    if not text:
        return

    text_normalized = _command_without_mention(text)
    chat_id = message.get('chat', {}).get('id')
    message_id = message.get('message_id')

    if chat_id is None:
        return

    allowed = _is_allowed(chat_id, allowed_chat_ids)

    if text_normalized.lower() in {'/start', '/help', HELP_BUTTON.lower()}:
        _send_message(chat_id, _help_text(), reply_to_message_id=message_id)
        return

    if text_normalized == CHECK_LIMIT_BUTTON:
        if not allowed:
            print(f'[WARN] Unauthorized chat_id={chat_id}', flush=True)
            _send_message(chat_id, 'Доступ к команде /limit не разрешён.', reply_to_message_id=message_id)
            return
        AWAITING_LIMIT_PLATE.add(str(chat_id))
        _send_message(
            chat_id,
            'Введите госномер. Например: <code>34FRL826</code>\n'
            'Можно указать месяц: <code>34FRL826 2026-08</code>',
            reply_to_message_id=message_id,
        )
        return

    if text_normalized.lower().startswith('/limit'):
        _log_limit_request(message, text, allowed, source='command')
        if not allowed:
            print(f'[WARN] Unauthorized chat_id={chat_id}', flush=True)
            _send_message(chat_id, 'Доступ к команде /limit не разрешён.', reply_to_message_id=message_id)
            return
        try:
            _handle_limit(int(chat_id), message_id, text)
        except Exception as exc:
            print('[ERROR] /limit failed:', repr(exc), flush=True)
            _send_message(chat_id, f'Ошибка при расчёте лимита: {exc}', reply_to_message_id=message_id)
        return

    if str(chat_id) in AWAITING_LIMIT_PLATE:
        plate, year_month = _parse_plate_input(text)
        _log_limit_request(message, text, allowed, source='plain_plate')
        if not allowed:
            print(f'[WARN] Unauthorized chat_id={chat_id}', flush=True)
            _send_message(chat_id, 'Доступ к команде /limit не разрешён.', reply_to_message_id=message_id)
            return
        if not plate:
            _send_message(chat_id, 'Не понял госномер. Пример: <code>34FRL826</code>', reply_to_message_id=message_id)
            return
        AWAITING_LIMIT_PLATE.discard(str(chat_id))
        try:
            _send_limit_result(int(chat_id), message_id, plate, year_month)
        except Exception as exc:
            print('[ERROR] /limit failed:', repr(exc), flush=True)
            _send_message(chat_id, f'Ошибка при расчёте лимита: {exc}', reply_to_message_id=message_id)
        return


def main() -> int:
    token = _bot_token()
    allowed = _allowed_chat_ids()
    print('[START] Telegram /limit bot', flush=True)
    print('[INFO] token exists:', bool(token), flush=True)
    print('[INFO] dedicated token:', bool(_env('TELEGRAM_LIMIT_BOT_TOKEN')), flush=True)
    print('[INFO] allowed_chat_ids:', sorted(allowed) if allowed else 'ALL (not recommended)', flush=True)

    offset = _read_offset()
    print('[INFO] initial offset:', offset, flush=True)

    while True:
        try:
            updates = _get_updates(offset)
            for update in updates:
                update_id = int(update['update_id'])
                offset = update_id + 1
                _write_offset(offset)
                _handle_update(update, allowed)
        except KeyboardInterrupt:
            print('[STOP] KeyboardInterrupt', flush=True)
            return 0
        except Exception as exc:
            print('[ERROR] polling failed:', repr(exc), flush=True)
            time.sleep(5)


if __name__ == '__main__':
    raise SystemExit(main())
