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
    raw = _env('TELEGRAM_LIMIT_ALLOWED_CHAT_IDS') or _env('TELEGRAM_ALLOWED_CHAT_IDS')
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


def _send_message(chat_id: int | str, text: str, *, reply_to_message_id: int | None = None) -> bool:
    payload: dict[str, Any] = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    if reply_to_message_id is not None:
        payload['reply_to_message_id'] = reply_to_message_id

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


def _help_text() -> str:
    return (
        'Команда лимита по машине:\n\n'
        '<code>/limit 34ABC123</code> — лимит, перерасход, остаток, заправки Shell/Petrol/Turpak, водитель и подразделение за текущий месяц\n'
        '<code>/limit 34ABC123 2026-08</code> — то же за выбранный месяц'
    )


def _handle_limit(chat_id: int, message_id: int | None, text: str) -> None:
    plate, year_month, help_requested = _parse_limit_command(text)
    if help_requested or not plate:
        _send_message(chat_id, _help_text(), reply_to_message_id=message_id)
        return

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


def _handle_update(update: dict[str, Any], allowed_chat_ids: set[str]) -> None:
    message = update.get('message') or {}
    text = str(message.get('text') or '').strip()
    if not text:
        return

    text_normalized = _command_without_mention(text)
    chat_id = message.get('chat', {}).get('id')
    message_id = message.get('message_id')

    if not text_normalized.lower().startswith('/limit'):
        if text_normalized.lower() in {'/start', '/help'} and chat_id is not None:
            _send_message(chat_id, _help_text(), reply_to_message_id=message_id)
        return

    if chat_id is None:
        return

    if allowed_chat_ids and str(chat_id) not in allowed_chat_ids:
        print(f'[WARN] Unauthorized chat_id={chat_id}', flush=True)
        _send_message(chat_id, 'Доступ к команде /limit не разрешён.', reply_to_message_id=message_id)
        return

    try:
        _handle_limit(int(chat_id), message_id, text)
    except Exception as exc:
        print('[ERROR] /limit failed:', repr(exc), flush=True)
        _send_message(chat_id, f'Ошибка при расчёте лимита: {exc}', reply_to_message_id=message_id)


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
