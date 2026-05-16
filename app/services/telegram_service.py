from __future__ import annotations

from pathlib import Path

import requests

from ..config import settings


def send_telegram_text(text: str) -> bool:
    if not settings.telegram_enabled:
        return True
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    url = f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage'
    response = requests.post(url, json={'chat_id': settings.telegram_chat_id, 'text': text}, timeout=30)
    return response.ok


def send_telegram_document(file_path: str | Path, caption: str = '') -> bool:
    if not settings.telegram_enabled:
        return True
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    url = f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendDocument'
    with open(file_path, 'rb') as fh:
        response = requests.post(
            url,
            data={'chat_id': settings.telegram_chat_id, 'caption': caption},
            files={'document': fh},
            timeout=120,
        )
    return response.ok
